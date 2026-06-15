import unittest

import numpy as np

from picoscenes import Picoscenes


class TestConsistency(unittest.TestCase):
    def setUp(self):
        self.frames = Picoscenes("rx_by_usrpN210.csi")
        self.frame = self.frames.raw[0]  # Get the first frame in the list
        self.subcarrier_indices = np.array(self.frame.get("CSI").get("SubcarrierIndex"))
        self.n_tones = self.frame.get("CSI").get("numTones")
        self.n_rx_antennas = self.frame.get("RxSBasic").get("numRx")
        self.n_streams = self.frame.get("RxSBasic").get("numSTS")

    def test_number_of_frames(self):
        # Test file contains 74 frames
        n_frames = 74
        self.assertEqual(len(self.frames.raw), n_frames)

    def test_subcarrier_indices(self):
        self.assertTrue(np.array_equal(self.subcarrier_indices, np.arange(-26, 27)))

    def test_magnitude(self):
        # Check shape of magnitude is correctly read
        mag = np.array(self.frame.get("CSI").get("Mag"))
        self.assertEqual(len(mag), self.n_tones)

    def test_csi(self):
        csi = np.array(self.frame.get("CSI").get("CSI"))
        self.assertEqual(len(csi), self.n_tones * self.n_streams * self.n_rx_antennas)

        # Just test whether this doesn't crash
        csi = csi.reshape((self.n_tones, self.n_streams, self.n_rx_antennas), order="F")


if __name__ == "__main__":
    unittest.main()
