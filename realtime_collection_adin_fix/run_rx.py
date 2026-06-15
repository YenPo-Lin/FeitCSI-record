import numpy as np
from PyPicoScenes.PyPicoScenes.PyPicoScenes import *

FrameDumper = cppyy.gbl.FrameDumper

_seq = 0

def get_simple_call_back():
    # Simple callback function
    def py_call_back(frame):
        print("-----------------------------get one frame----------------------------")
        return True
    return py_call_back

def get_call_back_dump(fileName="testCSI"):
    # Python callback receives frame and saves it to file
    def py_call_back_dump(frame):
        global _seq
        print(f"dump a frame to {fileName}")
        # Save frame to file
        FrameDumper.getInstanceWithoutTime(fileName).dumpRxFrame(frame)
        # pretty_print_frame(frame)

        # https://gitlab.com/wifisensing/rxs_parsing_core/-/blob/master/ModularPicoScenesFrame.hxx
        # https://gitlab.com/wifisensing/rxs_parsing_core/-/blob/master/CSISegment.hxx
        sm = frame.csiSegment.getCSI().CSIArray
        print(sm)

        # MACHeader:[type=[MF]Reserved_14, dest=00:16:ea:12:34:56, src=70:d8:23:17:7e:38, seq=245, frag=0, mfrags=0]
        source = frame.standardHeader
        mac_str = ':'.join(f'{int(b):02x}' for b in source.addr2)
        print(mac_str)
        if not mac_str == "70:d8:23:17:7e:38":
            print("Not target source")
            return True

        # print(type(source.addr2))
        # print(type(source))

        # https://gitlab.com/wifisensing/rxs_parsing_core/-/blob/master/SignalMatrix.hxx
        vec = sm.array                                    # std::vector<std::complex<float>>
        arr = np.array(vec, dtype=np.complex64, copy=True)  # 這行會把資料拷貝進 NumPy
        dims = [int(d) for d in sm.dimensions]
        order = 'C' if sm.majority == cppyy.gbl.SignalMatrixStorageMajority.RowMajor else 'F'
        arr = arr.reshape(dims, order=order)

        # print(arr.shape) # (1992, 2, 2, 1)
        # print(arr[0:10, 0, 0, 0])
        return True
    return py_call_back_dump

def recv_frame(nicName:str = '4'):
    # Start PicoScenes platform
    picoscenes_start()
    # Get network interface card
    nic = getNic(nicName)
    
    # Start NIC's Rx service
    nic.startRxService()

    # Register Python callbacks
    call_backs = {
        "call_back" : get_simple_call_back(),
        "call_back_dump" : get_call_back_dump(),
#         "call_back_plot" : get_call_back_plot(nicName),
    }
    for call_back_name, call_back in call_backs.items():
        nic.registerGeneralHandler(call_back_name, call_back)
        
    while (True):
        pass

    # Stop NIC's Rx service
    nic.stopRxService()
    # Stop NIC's Tx service
    nic.stopTxService()
    # Stop PicoScenes platform
    picoscenes_stop()
    # picoscenes_wait() will block until picoscenes_stop() is called
    picoscenes_wait()

recv_frame("24")
