#!/usr/bin/env python3
import zmq, json, time
from typing import Optional, Union, List
import numpy as np
from PyPicoScenes.PyPicoScenes.PyPicoScenes import *
FrameDumper = cppyy.gbl.FrameDumper

# --- 關鍵：保留回呼的強參考，避免被 GC ---
_KEEP_ALIVE: List[object] = []

class Publisher:
    def __init__(self, broker_address: str, topic: str):
        self.broker_address = broker_address
        self.topic = topic
        self.context = zmq.Context.instance()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.setsockopt(zmq.SNDHWM, 100)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.bind(f"tcp://{broker_address}")
        time.sleep(0.3)

    def publish(self, meta: dict, data: Optional[Union[bytes, memoryview, bytearray]] = None, topic: Optional[str] = None):
        t = (topic or self.topic).encode("utf-8")
        meta_bytes = json.dumps(meta).encode("utf-8")
        if data is None:
            self.socket.send_multipart([t, meta_bytes])
        else:
            mv = memoryview(data)
            self.socket.send_multipart([t, meta_bytes, mv])

_seq = 0

def make_tx_callback(publisher: Publisher, fileName: str = "testCSI", topic: Optional[str] = None):
    def py_call_back_dump(frame):
        global _seq
        # 可選：同步存檔
        # FrameDumper.getInstanceWithoutTime(fileName).dumpRxFrame(frame)

        sm = frame.csiSegment.getCSI().CSIArray
        vec = sm.array
        arr = np.array(vec, dtype=np.complex64, copy=True)
        dims = [int(d) for d in sm.dimensions]
        order = 'C' if sm.majority == cppyy.gbl.SignalMatrixStorageMajority.RowMajor else 'F'
        arr = arr.reshape(dims, order=order)

        payload = np.ascontiguousarray(arr)
        meta = {
            "seq": int(_seq),
            "ts": time.time(),
            "shape": list(payload.shape),
            "dtype": str(payload.dtype),
            "order": "C",
            "fileName": fileName,
            "desc": "PicoScenes CSI",
        }
        _seq += 1

        publisher.publish(meta, data=payload.data, topic=topic)
        return True
    return py_call_back_dump

def run_publisher_with_picoscenes(nicName: str, publisher: Publisher, fileName: str = "testCSI", topic: Optional[str] = None):
    picoscenes_start()
    nic = getNic(nicName)
    nic.startRxService()

    cb = make_tx_callback(publisher, fileName=fileName, topic=topic)
    # --- 關鍵：把 cb 存進 _KEEP_ALIVE，確保整個進程期間不會被 GC ---
    _KEEP_ALIVE.append(cb)
    nic.registerGeneralHandler("tx_csi_callback", cb)

    print(f"[Publisher] Running… NIC={nicName}, topic={topic or publisher.topic}, bind=tcp://{publisher.broker_address}")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        # 先停 RX/TX 與 PicoScenes，再清除回呼參考
        try:
            nic.stopRxService()
        except Exception:
            pass
        try:
            nic.stopTxService()
        except Exception:
            pass
        picoscenes_stop()
        picoscenes_wait()

        # 到這裡 C++ 不會再呼叫回呼了，才安全清掉參考
        _KEEP_ALIVE.clear()

if __name__ == "__main__":
    nicName = "24"
    broker = "0.0.0.0:5556"
    topic = "csi.topic"
    pub = Publisher(broker, topic)
    run_publisher_with_picoscenes(nicName, pub, fileName="testCSI", topic=topic)
