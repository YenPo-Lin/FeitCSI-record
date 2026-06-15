"""
Simple usage example

Processing and plotting CSI collected using Picoscenes with a USRP N210.
"""
from picoscenes import Picoscenes
import numpy as np
import matplotlib.pyplot as plt


frames = Picoscenes("rx_by_usrpN210.csi")
frame = frames.raw[0]  # Get the first frame in the list

num_tones = frame.get("CSI").get("numTones")
subcarrier_indices = np.array(frame.get("CSI").get("SubcarrierIndex"))
magnitude = np.array(frame.get("CSI").get("Mag"))[:num_tones]

plt.title("Magnitude Demo")
plt.xlabel("subcarrier index")
plt.ylabel("CSI Magnitude")
plt.plot(subcarrier_indices, magnitude)
plt.show()
