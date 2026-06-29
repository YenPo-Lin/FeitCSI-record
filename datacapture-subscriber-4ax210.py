#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =============================================================================
# 新版執行區：CSI-only Data Capture
#
# 這一段是根據 datacapture-subscriber-test.py 加上的新程式。
# 用途：
#   - 只接收本機 4-card CSI publisher
#   - 預設從 tcp://127.0.0.1:5556 收資料
#   - 訂閱四張卡的 csi.rx.1、csi.rx.2、csi.rx.3、csi.rx.4
#   - 畫面把 4 張卡 x 2 Rx chain 映射成 csi.rx.1 ... csi.rx.8
#   - 儲存 .npy 陣列檔與 .csv metadata
# =============================================================================

# CSI-only Data Capture for local 4-card AX210 publisher.
# Display expands 4 cards x 2 Rx chains into 8 rows:
#   ID 51 csi_rx_1
#   ID 51 csi_rx_2
#   ID 52 csi_rx_1
#   ID 52 csi_rx_2
#   ID 53 csi_rx_1
#   ID 53 csi_rx_2
#   ID 54 csi_rx_1
#   ID 54 csi_rx_2
# Keys:
#   N = session label
#   Space = start/stop free recording
#   Q = start 10-second recording
#   W = start 20-second recording
#   E = start 30-second recording
#   Space = stop recording
#   t = before->after
#   r = reset counters
#   x = quit

# 匯入需要的標準套件與第三方套件
import argparse
import csv
import curses
import json
import pathlib
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import zmq


PAIR_STOPPED = 1
PAIR_RECORDING = 2
PAIR_FOOTER = 3
PAIR_LABEL = 4


# 預設 CSI 來源設定：四張擴充板 AX210 都從本機 publisher 取資料
# XPS 目前對應：
#   SW P1 -> PhyPath 51 -> wlp7s0  -> csi.rx.1
#   SW P2 -> PhyPath 52 -> wlp8s0  -> csi.rx.2
#   SW P3 -> PhyPath 53 -> wlp9s0  -> csi.rx.3
#   SW P4 -> PhyPath 54 -> wlp10s0 -> csi.rx.4
DEFAULT_SOURCES = [
    {
        "name": "SW_P1",
        "nic_id": "51",
        "endpoint": "tcp://127.0.0.1:5556",
        "topic": "csi.rx.1",
        "rx_chains": 2,
    },
    {
        "name": "SW_P2",
        "nic_id": "52",
        "endpoint": "tcp://127.0.0.1:5556",
        "topic": "csi.rx.2",
        "rx_chains": 2,
    },
    {
        "name": "SW_P3",
        "nic_id": "53",
        "endpoint": "tcp://127.0.0.1:5556",
        "topic": "csi.rx.3",
        "rx_chains": 2,
    },
    {
        "name": "SW_P4",
        "nic_id": "54",
        "endpoint": "tcp://127.0.0.1:5556",
        "topic": "csi.rx.4",
        "rx_chains": 2,
    },
]


# 每一個 CSI 來源的即時狀態，例如封包數、最後接收時間、packet rate
@dataclass
class SourceState:
    idx: int
    name: str
    nic_id: str
    endpoint: str
    topic: str
    rx_chains: int = 2
    sock: Any = None
    status: str = "init"
    rx_count: int = 0
    rx_bytes: int = 0
    last_msg_ts: Optional[float] = None
    rate_win: deque = field(default_factory=deque)
    err_last: str = ""
    last_meta: Dict[str, Any] = field(default_factory=dict)

    def rx_topic(self, rx_idx: int) -> str:
        """Return the logical topic for one of the eight displayed RX chains."""
        return f"csi.rx.{self.idx * self.rx_chains + rx_idx}"


def now_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def now_file_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def sanitize(s: str) -> str:
    s = (s or "untitled").strip()
    for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ' ']:
        s = s.replace(ch, "_")
    return s or "untitled"


def fmt_age(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    dt = time.time() - ts
    if dt < 1:
        return f"{dt * 1000:.0f}ms"
    if dt < 60:
        return f"{dt:.1f}s"
    return f"{dt / 60:.1f}m"


def pkt_rate(st: SourceState, win_sec: float = 5.0) -> float:
    now = time.time()
    while st.rate_win and now - st.rate_win[0] > win_sec:
        st.rate_win.popleft()
    return len(st.rate_win) / win_sec


def safe_add(stdscr, y: int, x: int, text: str, attr: int = 0):
    try:
        h, w = stdscr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        stdscr.addnstr(y, x, text, max(0, w - x - 1), attr)
    except curses.error:
        pass


def prompt_label(stdscr, current: str) -> str:
    h, w = stdscr.getmaxyx()
    line = f"Session label [{current}]: "
    curses.echo()
    stdscr.nodelay(False)
    try:
        safe_add(stdscr, h - 2, 0, " " * (w - 1), curses.A_REVERSE)
        safe_add(stdscr, h - 2, 0, line, curses.A_REVERSE)
        s = stdscr.getstr(h - 2, min(len(line), w - 2), max(1, w - len(line) - 2))
        out = s.decode("utf-8", errors="replace").strip()
        return sanitize(out or current)
    except Exception:
        return current
    finally:
        curses.noecho()
        stdscr.nodelay(True)
        try:
            curses.curs_set(0)
        except Exception:
            pass


# CSV logger：每一個 topic 每分鐘產生一個 CSV，方便長時間收資料
class CsvLogger:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.handles: Dict[str, Any] = {}
        self.writers: Dict[str, csv.writer] = {}
        self.header = [
            "recv_unix",
            "recv_iso",
            "source",
            "nic_id",
            "pci",
            "topic",
            "transition_label",
            "rx_seq",
            "rx_system_ns",
            "packet_seq",
            "packet_taskId",
            "shape",
            "dtype",
            "order",
            "frequency_mhz",
            "center_frequency_mhz",
            "bandwidth_mhz",
            "src_mac",
            "frame_format",
            "mcs",
            "sts",
            "coding",
            "array_saved",
            "error",
        ]

    def _writer(self, topic: str) -> csv.writer:
        safe_topic = sanitize(topic)
        minute = datetime.now().strftime("%Y%m%d_%H%M")
        key = f"{safe_topic}/{minute}"
        if key in self.writers:
            return self.writers[key]

        out_dir = self.root / safe_topic
        out_dir.mkdir(parents=True, exist_ok=True)
        fpath = out_dir / f"{minute}.csv"
        fh = open(fpath, "a", newline="", encoding="utf-8", buffering=1)
        writer = csv.writer(fh)
        if fpath.stat().st_size == 0:
            writer.writerow(self.header)
        self.handles[key] = fh
        self.writers[key] = writer
        return writer

    def write(self, topic: str, row: Dict[str, Any]):
        writer = self._writer(topic)
        writer.writerow([row.get(k, "") for k in self.header])

    def close(self):
        for fh in list(self.handles.values()):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
        self.handles.clear()
        self.writers.clear()


# 解碼 publisher 送來的 CSI meta + array，並把 CSI matrix 存成 .npy
def decode_and_save_meta_array(parts: List[bytes], st: SourceState, art_root: pathlib.Path, transition: str) -> Dict[str, Any]:
    recv_unix = time.time()
    recv_iso = datetime.now().isoformat(timespec="milliseconds")
    topic = parts[0].decode("utf-8", errors="replace") if parts else st.topic

    base_row = {
        "recv_unix": recv_unix,
        "recv_iso": recv_iso,
        "source": st.name,
        "nic_id": st.nic_id,
        "pci": "",
        "topic": topic,
        "transition_label": transition,
        "error": "",
    }

    if len(parts) < 2:
        base_row["error"] = "missing meta frame"
        return base_row

    try:
        meta = json.loads(parts[1].decode("utf-8", errors="replace"))
    except Exception as e:
        base_row["error"] = f"bad meta json: {e}"
        return base_row

    for k in [
        "rx_seq", "rx_system_ns", "packet_seq", "packet_taskId", "dtype",
        "order", "frequency_mhz", "center_frequency_mhz", "bandwidth_mhz",
        "src_mac", "pci",
        "frame_format", "mcs", "sts", "coding",
    ]:
        base_row[k] = meta.get(k, "")

    shape = meta.get("shape", [])
    base_row["shape"] = ",".join(str(x) for x in shape) if isinstance(shape, list) else str(shape)

    if len(parts) < 3:
        base_row["error"] = "missing array frame"
        return base_row

    try:
        dtype = np.dtype(meta.get("dtype", "complex64"))
        shape_tuple = tuple(int(x) for x in meta.get("shape", []))
        order = meta.get("order", "C")

        arr = np.frombuffer(parts[2], dtype=dtype)
        expected = int(np.prod(shape_tuple)) if shape_tuple else arr.size
        if arr.size != expected:
            raise ValueError(f"size mismatch: got {arr.size}, expected {expected}, shape={shape_tuple}")

        arr = arr.reshape(shape_tuple, order=order)

        out_dir = art_root / "arrays" / sanitize(topic)
        out_dir.mkdir(parents=True, exist_ok=True)

        seq = meta.get("rx_seq", st.rx_count)
        fpath = out_dir / f"{now_file_ts()}_nic{st.nic_id}_seq{seq}.npy"
        np.save(fpath, arr)
        base_row["array_saved"] = str(fpath)

    except Exception as e:
        base_row["error"] = f"array save error: {e}"

    return base_row


def publish_session(pub_sock, topic: str, payload: Dict[str, Any]):
    try:
        pub_sock.send_multipart([
            topic.encode("utf-8"),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ], flags=zmq.NOBLOCK)
    except Exception:
        pass


# 初始化 curses 顏色：錄製中使用綠色醒目提示
def init_colors():
    try:
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except Exception:
                pass
            curses.init_pair(PAIR_STOPPED, curses.COLOR_WHITE, -1)
            curses.init_pair(PAIR_RECORDING, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(PAIR_FOOTER, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(PAIR_LABEL, curses.COLOR_YELLOW, -1)
            return True
    except Exception:
        pass
    return False


# curses TUI 畫面：顯示目前連線、封包數、packet rate、session 狀態
def draw_screen(
    stdscr,
    states: List[SourceState],
    capture_on: bool,
    session_dir: Optional[str],
    session_label: str,
    transition: str,
    log_base: pathlib.Path,
    art_base: pathlib.Path,
    session_pub: str,
    capture_start_ts: Optional[float],
    capture_duration: Optional[float],
    colors_enabled: bool,
    band_mode: int,
):
    h, w = stdscr.getmaxyx()

    if capture_on:
        elapsed = time.time() - capture_start_ts if capture_start_ts else 0.0
        if capture_duration is not None:
            mode_text = f"TIMED {capture_duration:.0f}s"
        else:
            mode_text = "MANUAL"
        state_text = f"RECORDING | mode={mode_text} | elapsed={elapsed:.2f}s"
        attr = (curses.color_pair(PAIR_RECORDING) | curses.A_BOLD) if colors_enabled else curses.A_BOLD
    else:
        state_text = "STOPPED"
        attr = (curses.color_pair(PAIR_STOPPED) | curses.A_DIM) if colors_enabled else curses.A_DIM

    safe_add(stdscr, 0, 0, " " * (w - 1), attr)
    prefix = f" Data Capture 8Rx / 4NIC — {state_text} | "
    label_text = f"LABEL: {session_label}"
    suffix = f" | session={session_dir or '-'} "
    label_is_set = session_label != "untitled"
    label_attr = (
        curses.color_pair(PAIR_LABEL) | curses.A_BOLD
        if colors_enabled and label_is_set
        else (curses.A_BOLD if label_is_set else attr)
    )
    safe_add(stdscr, 0, 0, prefix, attr)
    safe_add(stdscr, 0, len(prefix), label_text, label_attr)
    safe_add(stdscr, 0, len(prefix) + len(label_text), suffix, attr)

    safe_add(stdscr, 1, 0, f"Log: {log_base} | Artifacts: {art_base}", curses.A_DIM)
    safe_add(
        stdscr,
        2,
        0,
        f"Session PUB: {session_pub} | transition={transition} | mode={band_mode}GHz",
        curses.A_DIM,
    )
    frame_meta = next((st.last_meta for st in states if st.last_meta), {})
    if frame_meta:
        frame_format = str(frame_meta.get("frame_format", "?")).upper()
        if frame_format == "HESU":
            frame_format = "HE-SU"
        frame_text = (
            "Frame: "
            f"{frame_meta.get('frequency_mhz', '?')}/"
            f"{frame_meta.get('bandwidth_mhz', '?')}/"
            f"{frame_meta.get('center_frequency_mhz', '?')} MHz | "
            f"{frame_format} | "
            f"MCS {frame_meta.get('mcs', '?')} | "
            f"STS {frame_meta.get('sts', '?')} | "
            f"{frame_meta.get('coding', '?')} "
            "(TX phy/delay/repeat unavailable at RX)"
        )
    else:
        frame_text = "Frame: waiting for metadata..."
    safe_add(stdscr, 3, 0, frame_text, curses.A_DIM)
    safe_add(stdscr, 5, 0, " NIC_ID  PHY    PCI           RX_NAME       STATUS         LAST_MSG   PKTS     PCK/s", curses.A_BOLD)

    row = 6
    for st in states:
        rate = pkt_rate(st)

        age = fmt_age(st.last_msg_ts)
        status = st.status if not st.err_last else f"{st.status}:{st.err_last[:18]}"
        phy = st.last_meta.get("phy", "-") if st.last_meta else "-"
        phy_text = f"phy{phy}" if str(phy).isdigit() else str(phy)
        pci_text = st.last_meta.get("pci", "-") if st.last_meta else "-"

        # 展開每張卡的 2 個 Rx chain，依序顯示為 csi.rx.1 ... csi.rx.8。
        # 實際接收仍是一張卡一個 CSI matrix，matrix 內含兩個 Rx chain。
        for rx_idx in range(1, st.rx_chains + 1):
            if row >= h - 3:
                break

            line = (
                f" ID {st.nic_id:<3}  {phy_text:<5}  {pci_text:<12}  csi_rx_{rx_idx:<1}      "
                f"{status:<13} {age:<9} {st.rx_count:>7}  {rate:>7.2f}"
            )


            safe_add(stdscr, row, 0, line)
            row += 1

    if row < h - 3:
        safe_add(stdscr, row, 0, "-" * min(w - 1, 90), curses.A_DIM)

    foot = " N:label | Space:start/stop | Q:10s | W:20s | E:30s | t:before→after | r:reset | x:quit "
    #foot_attr = (curses.color_pair(PAIR_FOOTER) | curses.A_BOLD) if colors_enabled else curses.A_REVERSE
    foot_attr = curses.A_REVERSE
    safe_add(stdscr, h - 1, 0, " " * (w - 1), foot_attr)
    safe_add(stdscr, h - 1, 0, foot, foot_attr)


# 主程式流程：建立 ZMQ subscriber、處理按鍵、開始/停止儲存資料
def run(args):
    ctx = zmq.Context.instance()
    poller = zmq.Poller()

    states: List[SourceState] = []

    for i, src in enumerate(DEFAULT_SOURCES):
        st = SourceState(idx=i, **src)
        try:
            sock = ctx.socket(zmq.SUB)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVHWM, args.rcvhwm)
            sock.setsockopt_string(zmq.SUBSCRIBE, st.topic)
            sock.connect(st.endpoint)

            st.sock = sock
            st.status = "connected"

            poller.register(sock, zmq.POLLIN)

        except Exception as e:
            st.status = "error"
            st.err_last = str(e)

        states.append(st)

    pub_sock = ctx.socket(zmq.PUB)
    pub_sock.setsockopt(zmq.LINGER, 0)
    pub_sock.bind(args.session_pub)

    log_base = pathlib.Path(args.log_dir).resolve()
    art_base = pathlib.Path(args.out_dir).resolve()
    log_base.mkdir(parents=True, exist_ok=True)
    art_base.mkdir(parents=True, exist_ok=True)

    capture_on = False
    session_label = "untitled"
    session_dir: Optional[str] = None
    logger: Optional[CsvLogger] = None
    art_root: Optional[pathlib.Path] = None
    transition = "start"
    capture_start_ts: Optional[float] = None
    capture_duration: Optional[float] = None
    colors_enabled = False

    def start_capture(duration: Optional[float] = None):
        nonlocal capture_on, session_dir, logger, art_root, transition, capture_start_ts, capture_duration

        if capture_on:
            return

        session_dir = f"{now_compact()}_{sanitize(session_label)}"

        log_root = log_base / session_dir
        art_root = art_base / session_dir
        log_root.mkdir(parents=True, exist_ok=True)
        art_root.mkdir(parents=True, exist_ok=True)

        logger = CsvLogger(log_root)
        capture_on = True
        transition = "before"
        capture_start_ts = time.time()
        capture_duration = duration

        publish_session(pub_sock, "session.start", {
            "session_dir": session_dir,
            "session_label": session_label,
            "log_dir": str(log_root),
            "artifacts_dir": str(art_root),
            "started_unix": time.time(),
            "duration_sec": capture_duration,
        })

    def stop_capture():
        nonlocal capture_on, session_dir, logger, art_root, transition, capture_start_ts, capture_duration

        if session_dir:
            publish_session(pub_sock, "session.stop", {
                "session_dir": session_dir,
                "session_label": session_label,
                "stopped_unix": time.time(),
            })

        if logger:
            logger.close()

        capture_on = False
        session_dir = None
        logger = None
        art_root = None
        transition = "start"
        capture_start_ts = None
        capture_duration = None

    def consume(st: SourceState) -> bool:
        nonlocal logger, art_root

        try:
            parts = st.sock.recv_multipart(flags=zmq.NOBLOCK)
        except zmq.Again:
            return False
        except Exception as e:
            st.err_last = f"recv {e}"
            return False

        meta = {}
        if len(parts) >= 2:
            try:
                meta = json.loads(parts[1].decode("utf-8", errors="replace"))
            except Exception:
                pass
        now = time.time()
        st.rx_count += 1
        st.rx_bytes += sum(len(p) for p in parts)
        st.last_msg_ts = now
        st.rate_win.append(now)
        st.last_meta = meta

        if capture_on and logger is not None and art_root is not None:
            row = decode_and_save_meta_array(parts, st, art_root, transition)
            logger.write(row.get("topic", st.topic), row)
        return True

    def loop(stdscr):
        nonlocal session_label, transition, colors_enabled

        stdscr.nodelay(True)

        try:
            curses.curs_set(0)
        except Exception:
            pass

        colors_enabled = init_colors()

        last_draw = 0.0
        running = True

        while running:
            if capture_on and capture_duration is not None and capture_start_ts is not None:
                if time.time() - capture_start_ts >= capture_duration:
                    stop_capture()

            events = dict(poller.poll(50))

            for st in states:
                if st.sock in events and events[st.sock] & zmq.POLLIN:
                    # Drain a bounded batch so high-rate streams do not build up
                    # behind one-message-per-poll processing.
                    for _ in range(256):
                        if not consume(st):
                            break

            try:
                ch = stdscr.getch()
            except Exception:
                ch = -1

            if ch in (ord("x"), ord("X")):
                running = False

            elif ch in (ord("n"), ord("N")):
                session_label = prompt_label(stdscr, session_label)

            elif ch == ord(" "):
                if capture_on:
                    stop_capture()
                else:
                    start_capture(None)

            elif ch in (ord("q"), ord("Q")):
                if not capture_on:
                    start_capture(10.0)

            elif ch in (ord("w"), ord("W")):
                if not capture_on:
                    start_capture(20.0)

            elif ch in (ord("e"), ord("E")):
                if not capture_on:
                    start_capture(30.0)

            elif ch in (ord("t"), ord("T")):
                if capture_on:
                    transition = "after"

            elif ch in (ord("r"), ord("R")):
                for st in states:
                    st.rx_count = 0
                    st.rx_bytes = 0
                    st.last_msg_ts = None
                    st.rate_win.clear()

            if time.time() - last_draw >= 0.1:
                stdscr.erase()
                draw_screen(
                    stdscr,
                    states,
                    capture_on,
                    session_dir,
                    session_label,
                    transition,
                    log_base,
                    art_base,
                    args.session_pub,
                    capture_start_ts,
                    capture_duration,
                    colors_enabled,
                    args.mode,
                )
                stdscr.refresh()
                last_draw = time.time()

        stop_capture()

    try:
        curses.wrapper(loop)

    finally:
        try:
            if logger:
                logger.close()
        except Exception:
            pass

        for st in states:
            try:
                if st.sock:
                    st.sock.close(0)
            except Exception:
                pass

        try:
            pub_sock.close(0)
        except Exception:
            pass

        try:
            ctx.term()
        except Exception:
            pass


# CLI 參數設定
def main():
    ap = argparse.ArgumentParser(description="Local 4-card CSI Data Capture with 8Rx display")
    ap.add_argument("--log-dir", default="/media/tonic/DataSSD/CSI_data_2026/db")
    ap.add_argument("--out-dir", default="/media/tonic/DataSSD/CSI_data_2026/artifacts")
    ap.add_argument("--session-pub", default="tcp://*:60000")
    ap.add_argument("--rcvhwm", type=int, default=10000)
    ap.add_argument(
        "--mode",
        type=int,
        choices=[5, 6],
        default=5,
        help="Expected receiver band mode: 5 or 6 GHz (default: 5)",
    )
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
