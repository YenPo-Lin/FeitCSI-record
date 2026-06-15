#!/usr/bin/env python3
# rx_publisher.py — PicoScenes publisher with SessionBus listener (robust)
#
# - Subscribes to controller's session bus (default host:port 192.168.50.209:60000)
# - Publishes CSI with session metadata injected.

import time
import numpy as np
import argparse
import signal
import sys
import threading
import os
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple

import zmq

from zmq_utils import PicoscenesPublisher
from PyPicoScenes.PyPicoScenes.PyPicoScenes import *
FrameDumper = cppyy.gbl.FrameDumper

# ---------------------------
# Process control
# ---------------------------
_KEEP_ALIVE = []
_shutdown = False

def handle_sigint(sig, frame):
    print("\n[Main] Caught Ctrl+C, shutting down cleanly...")
    global _shutdown
    _shutdown = True

# ---------------------------
# Thread-safe session state
# ---------------------------
@dataclass
class SessionInfo:
    state: str = "STOP"          # "RUN" or "STOP"
    session_id: str = ""
    label: str = ""
    ts: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_run(self, sid: str, label: str, ts: str):
        with self._lock:
            self.state = "RUN"
            self.session_id = sid or ""
            self.label = label or ""
            self.ts = ts or ""

    def set_stop(self):
        with self._lock:
            self.state = "STOP"

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "session_id": self.session_id,
                "label": self.label,
                "ts": self.ts,
            }

SESSION = SessionInfo()

def _sanitize(s: str) -> str:
    s = (s or "").strip()
    bad = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for ch in bad:
        s = s.replace(ch, "_")
    return s

def _iso_to_compact(s: str) -> str:
    # "2025-10-16T13:27:44.624Z" -> "20251016-132744"
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    # Expect at least YYYYMMDDhhmmss
    if len(digits) >= 14:
        return f"{digits[0:8]}-{digits[8:14]}"
    return ""

def _derive_session_fields(obj: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Returns (session_id, label, ts) given controller payload.
    Priority:
      id  := session_dir  OR  f"{session_ts}_{session_label}" OR id/ session_id
      ts  := session_ts   OR  compact(started_iso)            OR from session_dir prefix
      lbl := session_label OR label OR from session_dir suffix
    """
    label = str(obj.get("session_label") or obj.get("label") or "").strip()

    ts = str(obj.get("session_ts") or obj.get("ts") or "").strip()
    if not ts:
        iso = str(obj.get("started_iso") or "")
        if iso:
            ts = _iso_to_compact(iso)
        elif "started_unix" in obj:
            try:
                ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime(float(obj["started_unix"])))
            except Exception:
                ts = ""

    sess_dir = str(obj.get("session_dir") or "").strip()
    sid = sess_dir

    # If session_dir absent, synthesize from ts & label; or try id/session_id
    if not sid:
        sid = str(obj.get("id") or obj.get("session_id") or "").strip()
        if not sid and (ts or label):
            sid = f"{ts}_{label}".strip("_")

    # If still no label/ts but we have session_dir like "YYYYMMDD-HHMMSS_label"
    if (not label or not ts) and sess_dir:
        parts = sess_dir.split("_", 1)
        if not ts and len(parts[0]) >= 15:
            ts = parts[0]
        if not label and len(parts) > 1:
            label = parts[1]

    return (_sanitize(sid), _sanitize(label), ts)

# ---------------------------
# SessionBus resilient listener
# ---------------------------
class SessionBusListener(threading.Thread):
    """
    SUB to controller's SessionBus, e.g. tcp://<host>:60000
    Subscribes to topic prefix (default: "session.")
    Robust to disconnects (recreate socket on error/backoff).
    Accepts:
      - multipart: [topic][json]
      - single-frame: "topic {json}"
    """
    def __init__(self, address: str, topic_prefix: str = "session.", rcvhwm: int = 128):
        super().__init__(daemon=True)
        self._ctx = zmq.Context.instance()
        self._topic_prefix = topic_prefix
        self._endpoint = self._normalize_address(address)
        self._rcvhwm = rcvhwm
        self._sock = None
        self._poller = None
        self._running = True
        self._backoff = 0.1  # exponential backoff on reconnect
        self._backoff_max = 2.0

    @staticmethod
    def _normalize_address(addr: str) -> str:
        if "://" in addr:
            return addr
        return f"tcp://{addr}"

    def _build_socket(self):
        if self._sock is not None:
            try:
                self._poller.unregister(self._sock)
            except Exception:
                pass
            try:
                self._sock.close(0)
            except Exception:
                pass
            self._sock = None

        sock = self._ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.RCVHWM, self._rcvhwm)
        sock.setsockopt(zmq.LINGER, 0)
        # keepalive helps detect half-open
        try:
            sock.setsockopt(zmq.TCP_KEEPALIVE, 1)
            sock.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 30)
            sock.setsockopt(zmq.TCP_KEEPALIVE_INTVL, 5)
            sock.setsockopt(zmq.TCP_KEEPALIVE_CNT, 3)
        except Exception:
            pass
        sock.setsockopt_string(zmq.SUBSCRIBE, self._topic_prefix)
        sock.connect(self._endpoint)

        self._sock = sock
        if self._poller is None:
            self._poller = zmq.Poller()
        self._poller.register(self._sock, zmq.POLLIN)

        # on (re)connect, announce current subscribe state
        print(f"[SessionBus] SUB connected -> {self._endpoint} topic='{self._topic_prefix}'")

    def _parse(self, parts):
        # returns (topic, obj)
        if len(parts) >= 2:
            try:
                topic = parts[0].decode("utf-8", errors="replace")
            except Exception:
                topic = ""
            body = parts[1]
            try:
                j = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                j = {}
            return topic, j
        # single frame "topic JSON"
        try:
            msg = parts[0].decode("utf-8", errors="replace")
        except Exception:
            return "", {}
        if " " in msg:
            topic, payload = msg.split(" ", 1)
            try:
                j = json.loads(payload)
            except Exception:
                j = {}
            return topic, j
        return msg, {}

    def _handle_msg(self, topic: str, obj: Dict[str, Any]):
        t = (topic or "").strip()
        sid, label, ts = _derive_session_fields(obj)
        if t.endswith("session.start"):
            SESSION.set_run(sid, label, ts)
        elif t.endswith("session.stop"):
            # Keep last known sid/label/ts but mark STOP
            SESSION.set_stop()
        else:
            return

        snap = SESSION.snapshot()
        print(f"[SessionBus][{snap['state']}] id={snap['session_id']} label='{snap['label']}' "
              f"ts={snap['ts']} topic='{t}' src='{self._endpoint}'")

    def run(self):
        # initial connect
        try:
            self._build_socket()
        except Exception as e:
            print(f"[SessionBus] initial connect error: {e}")

        while self._running and not _shutdown:
            try:
                # poll for up to 100ms
                events = dict(self._poller.poll(100)) if self._poller else {}
                if self._sock and (events.get(self._sock, 0) & zmq.POLLIN):
                    try:
                        parts = self._sock.recv_multipart(flags=zmq.NOBLOCK)
                    except zmq.Again:
                        parts = None
                    if parts:
                        self._backoff = 0.1  # healthy traffic -> reset backoff
                        topic, obj = self._parse(parts)
                        self._handle_msg(topic, obj)
                # small sleep to yield
                time.sleep(0.01)

            except zmq.error.ZMQError as e:
                # typical network/socket errors -> rebuild with backoff
                print(f"[SessionBus] socket error: {e}; reconnecting soon...")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2.0, self._backoff_max)
                try:
                    self._build_socket()
                except Exception as ee:
                    print(f"[SessionBus] reconnect failed: {ee}")
                    time.sleep(self._backoff)

            except Exception as e:
                # generic protection
                print(f"[SessionBus] unexpected error: {e}")
                time.sleep(0.1)

        # cleanup
        try:
            if self._sock:
                try:
                    self._poller.unregister(self._sock)
                except Exception:
                    pass
                self._sock.close(0)
        except Exception:
            pass

    def close(self):
        self._running = False

# ---------------------------
# PicoScenes helpers
# ---------------------------
_seqs = {}  # Sequence numbers per NIC

def safe_picoscenes_stop(timeout=5):
    """Stop PicoScenes gracefully, with timeout fallback."""
    try:
        print("[Publisher] Stopping PicoScenes...")
        picoscenes_stop()

        def waiter():
            try:
                picoscenes_wait()
            except Exception:
                pass

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            print(f"[Publisher] PicoScenes did not exit after {timeout}s — forcing termination")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                pass
        else:
            print("[Publisher] PicoScenes stopped cleanly.")
    except Exception as e:
        print(f"[Publisher] Error stopping PicoScenes: {e}")

def create_publisher_callback_picoscenes(nicName, publisher, topic, tx_mac):
    def py_call_back_dump(frame):
        global _seqs, _shutdown
        if _shutdown:
            return False

        # --- Filter by TX MAC address
        source = frame.standardHeader
        mac_str = ':'.join(f'{int(b):02x}' for b in source.addr2)
        if mac_str != tx_mac:
            return True

        # --- Extract CSI into NumPy
        frame.csiSegment.getCSI().removeCSDAndInterpolateCSI()
        sm = frame.csiSegment.getCSI().CSIArray
        vec = sm.array
        arr = np.array(vec, dtype=np.complex64, copy=True)
        dims = [int(d) for d in sm.dimensions]
        order = 'C' if sm.majority == cppyy.gbl.SignalMatrixStorageMajority.RowMajor else 'F'
        arr = arr.reshape(dims, order=order)

        # Packet sequence & timing
        packet_seq = frame.standardHeader.seq >> 4
        packet_taskId = frame.PicoScenesHeader.taskId
        tstamp = frame.rxSBasicSegment.getBasic().tstamp
        system_ns = frame.rxSBasicSegment.getBasic().systemTime

        # --- Session snapshot (thread-safe)
        ss = SESSION.snapshot()
        sess_tag = f"[{ss['state']} id={ss['session_id']}]" if ss['session_id'] else f"[{ss['state']}]"

        # # test Rx x8 antenna order
        # # ==================================================
        # # [修改處] 針對你的形狀 (Sub, Tx, Rx, 1) 計算振幅
        # try:
        #     # axis=(0, 1, 3) 代表把 Subcarriers, Tx, 和最後一個維度全部平均
        #     # 結果會變成 shape (2,)，也就是兩個 Rx 天線的數值
        #     amps = np.mean(np.abs(arr), axis=(0, 1, 3)) 
            
        #     # 格式化成字串顯示
        #     amps_str = "[" + ", ".join([f"{x:.1f}" for x in amps]) + "]"
        # except Exception as e:
        #     amps_str = f"[Error: {e}]"
        # # ==================================================
        
        # # Console log with session tag
        # log_msg = (
        #     f"{sess_tag} [Publisher] NIC={nicName} topic={topic} seq={_seqs[nicName]} mac={mac_str} "
        #     f"Amp={amps_str}"
        #     f"shape={arr.shape} dtype={arr.dtype} "
        #     f"pkt_seq={packet_seq} taskId={packet_taskId} tstamp={tstamp} system_ns={system_ns}"
        #     # f" amps={amps_str}"
        # )
        # print(log_msg)

        # test Tx x2 antenna order
        # ==================================================
        # [修改處] 針對你的形狀 (Sub, Tx, Rx, 1) 計算振幅，保留 Tx 與 Rx 維度
        try:
            # axis=(0, 3) 代表把 Subcarriers 和最後一個維度平均掉
            # 結果 arr_mean 的 shape 會變成 (Tx, Rx)，也就是一個二維矩陣
            amps_matrix = np.mean(np.abs(arr), axis=(0, 3)) 
            
            # 將矩陣格式化成容易閱讀的字串
            # 假設有 2 個 Tx，迴圈會跑兩次，組裝出 Tx0 與 Tx1 對所有 Rx 的振幅
            amps_str_list = []
            for tx_idx, rx_amps in enumerate(amps_matrix):
                # 將每個 Rx 的數值取小數點後一位
                rx_str = ", ".join([f"{x:.1f}" for x in rx_amps])
                amps_str_list.append(f"Tx{tx_idx}:[{rx_str}]")
            
            # 用 | 隔開不同 Tx 的數據
            amps_str = " | ".join(amps_str_list)
            
        except Exception as e:
            amps_str = f"[Error: {e}]"
        # ==================================================
        
        # Console log with session tag
        log_msg = (
            f"{sess_tag} [Publisher] NIC={nicName} topic={topic} seq={_seqs[nicName]} mac={mac_str} "
            f"Amp={amps_str} "
            f"shape={arr.shape} dtype={arr.dtype} "
            f"pkt_seq={packet_seq} taskId={packet_taskId} tstamp={tstamp} system_ns={system_ns}"
        )
        print(log_msg)

        # --- Publish
        payload = np.ascontiguousarray(arr)
        meta = {
            "session_state": ss["state"],
            "session_id": ss["session_id"],
            "session_label": ss["label"],
            "session_ts": ss["ts"],

            "rx_seq": int(_seqs[nicName]),
            "rx_tstamp": int(tstamp),
            "rx_system_ns": int(system_ns),
            "packet_seq": int(packet_seq),
            "packet_taskId": int(packet_taskId),
            "rx_computer_time": time.time(),
            "shape": list(payload.shape),
            "dtype": str(payload.dtype),
            "order": order,
        }
        _seqs[nicName] += 1

        publisher.publish(meta, data=payload.data, topic=topic)
        return True
    return py_call_back_dump

def run_publisher_picoscenes(args, publisher, sess_listener: Optional[SessionBusListener]):
    # PicoScenes init
    picoscenes_start()

    # Start RX on NICs
    nicNames = args.nicNames
    if args.nicIDs and len(args.nicNames) != len(args.nicIDs):
        raise ValueError("Length of nicNames and nicIDs must be equal when both provided.")

    nics = {}
    for nicName in nicNames:
        nic = getNic(nicName)
        nic.startRxService()
        nics[nicName] = nic

    # PicoScenes logging verbosity
    inst = LoggingService.getInstance()
    inst.setLevelAll(args.pico_log_level)

    # Configure EchoProbeParameters (inj_target_mac_address)
    echo_params = cppyy.gbl.EchoProbeParameters()
    target_mac = cppyy.gbl.std.array['uint8_t', 6]()
    mac_parts = [int(part, 16) for part in args.tx_mac.split(':')]
    for i, part in enumerate(mac_parts):
        target_mac[i] = part
    echo_params.inj_target_mac_address = target_mac

    # Register callbacks
    pub_cbs = {}
    for idx, nicName in enumerate(nicNames):
        nicID = args.nicIDs[idx]
        _seqs[nicName] = 0
        cb = create_publisher_callback_picoscenes(
            nicName, publisher, topic=args.topic + "." + nicID, tx_mac=args.tx_mac
        )
        pub_cbs[nicName] = cb
        _KEEP_ALIVE.append(cb)

    for nicName, nic in nics.items():
        nicID = args.nicIDs[nicNames.index(nicName)]
        nic.registerGeneralHandler("rx_zmq_callback", pub_cbs[nicName])
        print(f"[Publisher] Running… NIC={nicName}, topic={args.topic}.{nicID}, bind=tcp://{publisher.broker_address}")

    try:
        while not _shutdown:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Interrupted, stopping...")
    finally:
        # Stop RX/TX and PicoScenes, then clear callback references
        for nicName, nic in nics.items():
            try:
                print(f"[Publisher] Stopping RX service for NIC={nicName}...")
                nic.stopRxService()
                time.sleep(0.1)
            except Exception:
                print(f"[Publisher] Error stopping RX service for NIC={nicName}")
            try:
                print(f"[Publisher] Stopping TX service for NIC={nicName}...")
                nic.stopTxService()
            except Exception:
                print(f"[Publisher] Error stopping TX service for NIC={nicName}")

        safe_picoscenes_stop(timeout=5)

        _KEEP_ALIVE.clear()
        try:
            publisher.close()
        except Exception:
            pass

        if sess_listener:
            sess_listener.close()

    print("[Publisher] Exited.")

# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # NIC config
    parser.add_argument("--nicNames", nargs="+", required=True)
    parser.add_argument("--nicIDs", nargs="+", required=True)

    # PicoScenes Logging level
    parser.add_argument("--pico_log_level", type=int, default=5)

    # Tx filter
    parser.add_argument("--tx_mac", default="70:d8:23:17:7e:38")

    # data publisher config
    parser.add_argument("--broker", default="0.0.0.0:5556")
    parser.add_argument("--topic", default="csi.rx")

    # session bus config
    parser.add_argument("--session-bus", dest="session_bus_addr", default="192.168.50.209:60000",
                        help="Host:port or tcp://host:port for session bus SUB")
    parser.add_argument("--session-topic-prefix", dest="session_topic_prefix", default="session.",
                        help="Topic prefix to subscribe on session bus (default: 'session.')")

    args = parser.parse_args()

    # Handle Ctrl+C
    signal.signal(signal.SIGINT, handle_sigint)

    # Start SessionBus listener (robust, reconnecting)
    sess_listener = None
    try:
        sess_listener = SessionBusListener(args.session_bus_addr, topic_prefix=args.session_topic_prefix)
        sess_listener.start()
        # Initial banner (the thread logs the actual connect)
        print(f"[SessionBus] Listening on {sess_listener._endpoint} topic='{args.session_topic_prefix}'")
    except Exception as e:
        print(f"[SessionBus] WARNING: could not start listener: {e}")

    pub = PicoscenesPublisher(args.broker, args.topic)
    run_publisher_picoscenes(args, pub, sess_listener)
