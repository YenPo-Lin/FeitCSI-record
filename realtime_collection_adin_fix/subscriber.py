#!/usr/bin/env python3
"""
Subscriber (ZeroMQ SUB)
-----------------------
- Connects to tcp://<publisher_ip>:5556
- Receives multipart:
	[topic, meta(json), (optional) data(bytes)]
- If meta contains shape/dtype/order and data exists -> reconstruct numpy.ndarray
"""

import zmq
import json
import numpy as np
import time
import threading
import queue
from typing import Callable, Optional

class Subscriber:
	def __init__(self, broker_address: str, topic: str, callback: Callable[[str, dict, Optional[bytes], Optional[np.ndarray]], None]):
		self.broker_address = broker_address
		self.topic = topic
		self.callback = callback

		self.context = zmq.Context.instance()
		self.socket = self.context.socket(zmq.SUB)

		# Stability settings
		self.socket.setsockopt(zmq.RCVHWM, 200000)
		self.socket.setsockopt(zmq.RCVBUF, 16*1024*1024)
		self.socket.setsockopt(zmq.LINGER, 0)

		# Connect to PUB
		self.socket.connect(f"tcp://{broker_address}")

		# Subscribe to topic (prefix match)
		self.socket.setsockopt_string(zmq.SUBSCRIBE, self.topic)

		# Slow joiner syndrome buffer
		time.sleep(0.5)

		# Internal queue and receiver thread
		self._queue = queue.Queue(maxsize=50000)
		self._stop = False
		self._recv_thread = threading.Thread(target=self._recv_worker, daemon=True)
		self._recv_thread.start()

	def _recv_worker(self):
		"""Continuously pulls frames from ZMQ socket to avoid kernel buffer overflow."""
		while not self._stop:
			try:
				frames = self.socket.recv_multipart(flags=zmq.NOBLOCK)
				self._queue.put_nowait(frames)
			except zmq.Again:
				time.sleep(0.001)
			except queue.Full:
				# Drop oldest frame to make room for new one
				try:
					_ = self._queue.get_nowait()
				except Exception:
					pass
				self._queue.put_nowait(frames)
			except Exception as e:
				print(f"[Subscriber] Receiver thread error: {e}")
				time.sleep(0.01)

	def _try_reconstruct_array(self, meta: dict, data: Optional[bytes]) -> Optional[np.ndarray]:
		if data is None:
			return None
		shape = meta.get("shape")
		dtype = meta.get("dtype")
		order = meta.get("order", "C")
		if shape is None or dtype is None:
			return None
		try:
			arr = np.frombuffer(data, dtype=np.dtype(dtype)).reshape(tuple(shape), order=order)
			return arr
		except Exception as e:
			print(f"[Subscriber] Array reconstruct error: {e}")
			return None

	def listen(self):
		print(f"[Subscriber] Listening on topic='{self.topic}' from {self.broker_address}")
		while True:
			try:
				frames = self._queue.get(timeout=1.0)
			except queue.Empty:
				continue

			if len(frames) < 2:
				print(f"[Subscriber] Unexpected frames: {len(frames)}")
				continue

			topic = frames[0].decode("utf-8", errors="replace")
			meta_raw = frames[1]
			data_bytes = frames[2] if len(frames) >= 3 else None

			try:
				meta = json.loads(meta_raw.decode("utf-8"))
			except json.JSONDecodeError:
				meta = {"error": "Invalid JSON in meta", "raw": meta_raw.decode("utf-8", errors="replace")}

			array = self._try_reconstruct_array(meta, data_bytes)
			self.callback(topic, meta, data_bytes, array)

	def close(self):
		"""Gracefully stop subscriber."""
		self._stop = True
		self._recv_thread.join(timeout=1)
		self.socket.close(linger=0)
		self.context.term()
		print("[Subscriber] Closed cleanly.")


def get_rx_callback(topic: str, meta: dict, data: Optional[bytes], array: Optional[np.ndarray]):
	if array is not None:
		packet_seq = meta.get("packet_seq")
		packet_taskId = meta.get("packet_taskId")
		tstamp = meta.get("rx_tstamp")
		system_ns = meta.get("rx_system_ns")
		rx_seq = meta.get("rx_seq")
		rx_computer_time = meta.get("rx_computer_time")
		shape = array.shape
		dtype = array.dtype

		print(
			f"[Subscriber] topic={topic} rx_seq={rx_seq} shape={shape} dtype={dtype}\n"
			f"             pkt_seq={packet_seq} packet_taskId={packet_taskId} tstamp={tstamp} system_ns={system_ns} rx_computer_time={rx_computer_time}"
		)
	else:
		print(f"[Callback] topic={topic}")

if __name__ == "__main__":
	# Change broker to the Publisher's IP
	broker = "192.168.51.252:5556"   # <---- Change to your publisher's IP:port
	topic = "csi.rx."
	# topic = "csi.rx.2"
	
	# broker = "192.168.51.149:5556"   # <---- Change to your publisher's IP:port
	# topic = "csi.rx.3"
	# topic = "csi.rx.4"

	sub = Subscriber(broker, topic, get_rx_callback)
	try:
		sub.listen()
	except KeyboardInterrupt:
		print("\n[Subscriber] Interrupted, shutting down...")
		sub.close()
