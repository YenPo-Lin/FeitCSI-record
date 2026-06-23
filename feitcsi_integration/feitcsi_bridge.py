#!/usr/bin/env python3
"""Bridge four FeitCSI UDP streams to the existing CSI ZeroMQ topics."""

import argparse
import json
import queue
import signal
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


HEADER_SIZE = 272
HEADER = struct.Struct("<IIIQ26xBB4xI4xII6s18xI")
UDP_RECEIVE_BUFFER = 50_000_000


@dataclass(frozen=True)
class Card:
    nic_id: str
    phy: int
    pci: str
    port: int
    topic: str


@dataclass(frozen=True)
class FeitCsiFrame:
    csi: np.ndarray
    csi_data_size: int
    ftm_clock: int
    timestamp: int
    num_rx: int
    num_tx: int
    num_subcarriers: int
    rssi1: int
    rssi2: int
    src_mac: str
    rate_n_flags: int


def normalize_mac(mac: Optional[str]) -> Optional[str]:
    if mac is None:
        return None
    cleaned = mac.strip().lower().replace("-", ":")
    if not cleaned:
        return None
    parts = cleaned.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise ValueError(f"invalid MAC address: {mac!r}")
    try:
        return ":".join(f"{int(part, 16):02x}" for part in parts)
    except ValueError as exc:
        raise ValueError(f"invalid MAC address: {mac!r}") from exc


def parse_frame(payload: bytes) -> FeitCsiFrame:
    if len(payload) < HEADER_SIZE:
        raise ValueError(f"short FeitCSI datagram: {len(payload)} bytes")

    (
        csi_data_size,
        _space4,
        ftm_clock,
        timestamp,
        num_rx,
        num_tx,
        num_subcarriers,
        rssi1,
        rssi2,
        src_mac,
        rate_n_flags,
    ) = HEADER.unpack_from(payload)

    expected_values = num_rx * num_tx * num_subcarriers
    expected_size = expected_values * 4
    if csi_data_size != expected_size:
        raise ValueError(
            f"CSI size mismatch: header={csi_data_size}, dimensions={expected_size}"
        )
    if len(payload) != HEADER_SIZE + csi_data_size:
        raise ValueError(
            f"datagram size mismatch: received={len(payload)}, "
            f"expected={HEADER_SIZE + csi_data_size}"
        )

    iq = np.frombuffer(payload, dtype="<i2", count=expected_values * 2,
                       offset=HEADER_SIZE).reshape(expected_values, 2)
    flat = iq[:, 0].astype(np.float32) + 1j * iq[:, 1].astype(np.float32)
    # FeitCSI stores RX, TX/spatial-stream, subcarrier. Match the existing
    # subscriber contract: subcarrier, TX/STS, RX, CSI-frame.
    csi = flat.reshape(num_rx, num_tx, num_subcarriers)
    csi = np.ascontiguousarray(csi.transpose(2, 1, 0)[..., np.newaxis],
                               dtype=np.complex64)

    return FeitCsiFrame(
        csi=csi,
        csi_data_size=csi_data_size,
        ftm_clock=ftm_clock,
        timestamp=timestamp,
        num_rx=num_rx,
        num_tx=num_tx,
        num_subcarriers=num_subcarriers,
        rssi1=rssi1,
        rssi2=rssi2,
        src_mac=":".join(f"{byte:02x}" for byte in src_mac),
        rate_n_flags=rate_n_flags,
    )


class Bridge:
    def __init__(self, bind: str, cards: Sequence[Card], frequency: int,
                 center_frequency: int, bandwidth: int, frame_format: str,
                 tx_mac: Optional[str] = None, print_src_mac: bool = False):
        import zmq

        self.zmq = zmq
        self.cards = cards
        self.frequency = frequency
        self.center_frequency = center_frequency
        self.bandwidth = bandwidth
        self.frame_format = frame_format
        self.tx_mac = normalize_mac(tx_mac)
        self.print_src_mac = print_src_mac
        self.src_mac_last_print = {}
        self.stop_event = threading.Event()
        self.context = zmq.Context.instance()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.setsockopt(zmq.SNDHWM, 200000)
        self.publisher.setsockopt(zmq.LINGER, 0)
        self.publisher.bind(f"tcp://{bind}")
        self.publish_queue = queue.Queue(maxsize=50000)
        self.threads = []

    def command(self) -> bytes:
        return (
            f"feitcsi --phy 0 --frequency {self.frequency} "
            f"--channel-width {self.bandwidth} --format {self.frame_format} "
            "--mode measure"
        ).encode("ascii")

    def run_card(self, card: Card) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECEIVE_BUFFER)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(1.0)
        command = self.command().replace(b"--phy 0", f"--phy {card.phy}".encode())
        sock.sendto(command, ("127.0.0.1", card.port))
        print(
            f"[FeitCSI] NIC={card.nic_id} phy{card.phy} UDP={card.port} "
            f"-> {card.topic} rcvbuf={sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)}",
            flush=True,
        )

        rx_seq = 0
        while not self.stop_event.is_set():
            try:
                payload, _address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            try:
                frame = parse_frame(payload)
            except ValueError as exc:
                print(f"[FeitCSI] NIC={card.nic_id} dropped frame: {exc}")
                continue
            if self.print_src_mac:
                now = time.time()
                key = (card.nic_id, frame.src_mac)
                last_print = self.src_mac_last_print.get(key, 0.0)
                if now - last_print >= 1.0:
                    print(
                        f"[FeitCSI] NIC={card.nic_id} observed src_mac={frame.src_mac} "
                        f"rssi1={frame.rssi1} rssi2={frame.rssi2}",
                        flush=True,
                    )
                    self.src_mac_last_print[key] = now
            if self.tx_mac is not None and frame.src_mac != self.tx_mac:
                continue
            rx_seq += 1
            now_ns = time.time_ns()
            meta = {
                "source": "FeitCSI",
                "nic_id": card.nic_id,
                "phy": card.phy,
                "pci": card.pci,
                "rx_seq": rx_seq,
                "rx_tstamp": frame.timestamp,
                "rx_system_ns": now_ns,
                "rx_computer_time": now_ns / 1_000_000_000,
                "ftm_clock": frame.ftm_clock,
                "num_rx": frame.num_rx,
                "num_tx": frame.num_tx,
                "num_subcarriers": frame.num_subcarriers,
                "rssi1": frame.rssi1,
                "rssi2": frame.rssi2,
                "src_mac": frame.src_mac,
                "rate_n_flags": frame.rate_n_flags,
                "frequency_mhz": self.frequency,
                "center_frequency_mhz": self.center_frequency,
                "bandwidth_mhz": self.bandwidth,
                "frame_format": self.frame_format,
                "mcs": frame.rate_n_flags & 0xF,
                "sts": frame.num_tx,
                "coding": "LDPC" if frame.rate_n_flags & (1 << 16) else "BCC",
                "shape": list(frame.csi.shape),
                "dtype": str(frame.csi.dtype),
                "order": "C",
            }
            message = (
                card.topic.encode("ascii"),
                json.dumps(meta).encode("utf-8"),
                frame.csi.tobytes(),
            )
            try:
                self.publish_queue.put_nowait(message)
            except queue.Full:
                try:
                    self.publish_queue.get_nowait()
                except queue.Empty:
                    pass
                self.publish_queue.put_nowait(message)

        try:
            sock.sendto(b"stop", ("127.0.0.1", card.port))
        except OSError:
            pass
        sock.close()

    def run(self) -> None:
        time.sleep(0.5)

        for card in self.cards:
            thread = threading.Thread(target=self.run_card, args=(card,), daemon=True)
            thread.start()
            self.threads.append(thread)

        # 等四張卡 run_card() 完成初始化印出
        time.sleep(0.5)

        print("[ZMQ] Publisher ready. Waiting for CSI frames...", flush=True)

        while not self.stop_event.is_set():
            try:
                message = self.publish_queue.get(timeout=0.5)
            except queue.Empty:
                message = None
            if message is not None:
                self.publisher.send_multipart(message)

        for thread in self.threads:
            thread.join(timeout=3)

        self.publisher.close(linger=0)

    def stop(self, *_args) -> None:
        self.stop_event.set()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0:5556")
    parser.add_argument("--frequency", type=int, default=5520)
    parser.add_argument("--center-frequency", type=int, default=5570)
    parser.add_argument("--bandwidth", type=int, default=160)
    parser.add_argument("--format", default="HESU")
    parser.add_argument(
        "--tx-mac",
        default=None,
        help="publish only frames whose source MAC matches this address",
    )
    parser.add_argument(
        "--print-src-mac",
        action="store_true",
        help="print observed source MAC addresses for debugging filters",
    )
    parser.add_argument(
        "--card",
        action="append",
        metavar="NIC_ID:PHY:PCI:PORT:TOPIC",
        help="card mapping; may be specified multiple times",
    )
    args = parser.parse_args()

    if args.card:
        cards = []
        for value in args.card:
            try:
                left, port, topic = value.rsplit(":", 2)
                parts = left.split(":", 2)
                if len(parts) == 2:
                    nic_id, phy = parts
                    pci = ""
                else:
                    nic_id, phy, pci = parts
                cards.append(Card(nic_id, int(phy), pci, int(port), topic))
            except ValueError as exc:
                parser.error(f"invalid --card value {value!r}: {exc}")
    else:
        cards = [
            Card("51", 3, "0000:07:00.0", 8008, "csi.rx.1"),
            Card("52", 1, "0000:08:00.0", 8009, "csi.rx.2"),
            Card("53", 2, "0000:09:00.0", 8010, "csi.rx.3"),
            Card("54", 4, "0000:0a:00.0", 8011, "csi.rx.4"),
        ]
    bridge = Bridge(
        args.bind,
        cards,
        args.frequency,
        args.center_frequency,
        args.bandwidth,
        args.format,
        args.tx_mac,
        args.print_src_mac,
    )
    signal.signal(signal.SIGINT, bridge.stop)
    signal.signal(signal.SIGTERM, bridge.stop)
    bridge.run()


if __name__ == "__main__":
    main()
