# zmq_utils.py
import zmq, json, time, threading, queue, signal, sys
from typing import Optional, Union

class PicoscenesPublisher:
    """Threaded, non-blocking ZMQ PUB publisher for PicoScenes with clean shutdown."""

    def __init__(self, broker_address: str, topic: str,
                 hwm: int = 2000000, max_queue: int = 2000000):
        """
        broker_address: e.g. "0.0.0.0:5556"
        topic: base topic string
        hwm: ZMQ high-water mark
        max_queue: max messages before oldest are dropped
        """
        self.broker_address = broker_address
        self.topic = topic
        self.context = zmq.Context.instance()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.setsockopt(zmq.SNDHWM, hwm)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.bind(f"tcp://{broker_address}")
        time.sleep(0.3)

        self._queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._send_thread, daemon=True)
        self._thread.start()

        print(f"[ZMQ] Publisher started on tcp://{broker_address}, topic={topic}")

        # Make Ctrl+C immediately stop this publisher
        signal.signal(signal.SIGINT, self._sigint_handler)

    def _sigint_handler(self, signum, frame):
        print("\n[ZMQ] SIGINT caught, shutting down publisher...")
        self.close()
        sys.exit(0)

    def _send_thread(self):
        """Background sender thread."""
        while not self._stop.is_set():
            try:
                t, meta_bytes, data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if data is None:
                    self.socket.send_multipart([t, meta_bytes], flags=zmq.NOBLOCK)
                else:
                    self.socket.send_multipart([t, meta_bytes, data], flags=zmq.NOBLOCK)
            except zmq.Again:
                pass  # drop silently if internal buffers full
            except Exception as e:
                print(f"[ZMQ] Send error: {e}")
            finally:
                self._queue.task_done()

    def publish(self, meta: dict,
                data: Optional[Union[bytes, memoryview, bytearray]] = None,
                topic: Optional[str] = None):
        """Non-blocking enqueue of frame."""
        t = (topic if topic else self.topic).encode("utf-8")
        meta_bytes = json.dumps(meta).encode("utf-8")
        mv = memoryview(data) if data is not None else None
        try:
            self._queue.put_nowait((t, meta_bytes, mv))
        except queue.Full:
            # drop oldest when full
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait((t, meta_bytes, mv))

    def close(self):
        """Graceful shutdown."""
        if self._stop.is_set():
            return
        self._stop.set()
        print("[ZMQ] Closing publisher...")
        self._thread.join(timeout=2)
        try:
            self.socket.close(linger=0)
            self.context.term()
        except Exception:
            pass
        print("[ZMQ] Publisher closed cleanly")
