#!/usr/bin/env python3

import json
import socket
import struct
import threading
import time
import unittest

import numpy as np

from feitcsi_bridge import Bridge, Card, HEADER_SIZE, normalize_mac, parse_frame


def make_payload(num_rx=2, num_tx=2, tones=3, src_mac="001122334455"):
    values = np.arange(num_rx * num_tx * tones, dtype=np.int16)
    iq = np.column_stack((values, -values)).astype("<i2")
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<I", header, 0, iq.nbytes)
    struct.pack_into("<I", header, 8, 1234)
    struct.pack_into("<Q", header, 12, 5678)
    struct.pack_into("<BB", header, 46, num_rx, num_tx)
    struct.pack_into("<I", header, 52, tones)
    struct.pack_into("<II", header, 60, 41, 42)
    header[68:74] = bytes.fromhex(src_mac.replace(":", "").replace("-", ""))
    struct.pack_into("<I", header, 92, 0xAABBCCDD)
    return bytes(header) + iq.tobytes()


class ParseFrameTest(unittest.TestCase):
    def test_parses_and_reorders_csi(self):
        num_rx, num_tx, tones = 2, 2, 3
        frame = parse_frame(make_payload(num_rx, num_tx, tones))

        self.assertEqual(frame.csi.shape, (tones, num_tx, num_rx, 1))
        self.assertEqual(frame.csi.dtype, np.complex64)
        self.assertEqual(frame.src_mac, "00:11:22:33:44:55")
        self.assertEqual(frame.ftm_clock, 1234)
        self.assertEqual(frame.timestamp, 5678)
        self.assertEqual(frame.csi[0, 0, 0, 0], 0 + 0j)
        self.assertEqual(frame.csi[0, 0, 1, 0], 6 - 6j)
        self.assertEqual(frame.csi[2, 1, 1, 0], 11 - 11j)

    def test_rejects_short_datagram(self):
        with self.assertRaisesRegex(ValueError, "short"):
            parse_frame(b"\0" * 20)

    def test_normalizes_mac(self):
        self.assertEqual(normalize_mac("70-D8-23-17-7E-38"), "70:d8:23:17:7e:38")
        self.assertEqual(normalize_mac("70:d8:23:17:7e:38"), "70:d8:23:17:7e:38")
        self.assertIsNone(normalize_mac(""))
        with self.assertRaisesRegex(ValueError, "invalid MAC"):
            normalize_mac("70:d8:23")

    def test_udp_to_zmq_bridge(self):
        try:
            import zmq
        except ImportError:
            self.skipTest("pyzmq is not installed")

        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        udp_port = server.getsockname()[1]
        port_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port_probe.bind(("127.0.0.1", 0))
        zmq_port = port_probe.getsockname()[1]
        port_probe.close()

        def fake_feitcsi():
            _command, client = server.recvfrom(2048)
            time.sleep(0.3)
            server.sendto(make_payload(), client)

        fake = threading.Thread(target=fake_feitcsi, daemon=True)
        fake.start()

        bridge = Bridge(
            f"127.0.0.1:{zmq_port}",
            [Card("51", 3, udp_port, "csi.rx.1")],
            5520,
            5570,
            160,
            "HESU",
            tx_mac=None,
        )
        context = zmq.Context()
        subscriber = context.socket(zmq.SUB)
        subscriber.setsockopt_string(zmq.SUBSCRIBE, "csi.rx.1")
        subscriber.connect(f"tcp://127.0.0.1:{zmq_port}")
        runner = threading.Thread(target=bridge.run)
        runner.start()

        self.assertTrue(subscriber.poll(4000))
        topic, metadata, raw = subscriber.recv_multipart()
        meta = json.loads(metadata)
        array = np.frombuffer(raw, dtype=meta["dtype"]).reshape(meta["shape"])
        self.assertEqual(topic, b"csi.rx.1")
        self.assertEqual(meta["nic_id"], "51")
        self.assertEqual(meta["frequency_mhz"], 5520)
        self.assertEqual(meta["center_frequency_mhz"], 5570)
        self.assertEqual(meta["bandwidth_mhz"], 160)
        self.assertEqual(meta["mcs"], 13)
        self.assertEqual(meta["sts"], 2)
        self.assertEqual(array.shape, (3, 2, 2, 1))

        bridge.stop()
        runner.join(timeout=3)
        subscriber.close(linger=0)
        context.term()
        server.close()

    def test_tx_mac_filter(self):
        try:
            import zmq
        except ImportError:
            self.skipTest("pyzmq is not installed")

        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        udp_port = server.getsockname()[1]
        port_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port_probe.bind(("127.0.0.1", 0))
        zmq_port = port_probe.getsockname()[1]
        port_probe.close()

        def fake_feitcsi():
            _command, client = server.recvfrom(2048)
            time.sleep(0.3)
            server.sendto(make_payload(src_mac="aa:bb:cc:dd:ee:ff"), client)
            time.sleep(0.1)
            server.sendto(make_payload(src_mac="70:d8:23:17:7e:38"), client)

        fake = threading.Thread(target=fake_feitcsi, daemon=True)
        fake.start()

        bridge = Bridge(
            f"127.0.0.1:{zmq_port}",
            [Card("51", 3, udp_port, "csi.rx.1")],
            5520,
            5570,
            160,
            "HESU",
            tx_mac="70-D8-23-17-7E-38",
        )
        context = zmq.Context()
        subscriber = context.socket(zmq.SUB)
        subscriber.setsockopt_string(zmq.SUBSCRIBE, "csi.rx.1")
        subscriber.connect(f"tcp://127.0.0.1:{zmq_port}")
        runner = threading.Thread(target=bridge.run)
        runner.start()

        self.assertTrue(subscriber.poll(4000))
        _topic, metadata, _raw = subscriber.recv_multipart()
        meta = json.loads(metadata)
        self.assertEqual(meta["src_mac"], "70:d8:23:17:7e:38")

        bridge.stop()
        runner.join(timeout=3)
        subscriber.close(linger=0)
        context.term()
        server.close()


if __name__ == "__main__":
    unittest.main()
