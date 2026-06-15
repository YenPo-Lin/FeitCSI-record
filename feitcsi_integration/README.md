# Four-card FeitCSI integration

This integration keeps the existing subscriber contract:

- `csi.rx.1`: NIC 51, PCI `0000:07:00.0`, UDP 8008
- `csi.rx.2`: NIC 52, PCI `0000:08:00.0`, UDP 8009
- `csi.rx.3`: NIC 53, PCI `0000:09:00.0`, UDP 8010
- `csi.rx.4`: NIC 54, PCI `0000:0a:00.0`, UDP 8011
- ZeroMQ publisher: `tcp://0.0.0.0:5556`

The launcher resolves the current `phyN` dynamically from these stable PCI
addresses because Linux PHY numbering can change after a reboot. The bundled
FeitCSI fork adds `--phy` and `--udp-port`. Upstream v2.0.0
otherwise selects the first PHY, removes every Wi-Fi interface, and binds every
UDP server to port 8008.

## Build prerequisites

```bash
sudo apt install libgtkmm-3.0-dev libnl-genl-3-dev libiw-dev libpcap-dev
make -C third_party/FeitCSI
chmod +x run_4card_feitcsi.sh
```

FeitCSI also requires its customized `FeitCSI-iwlwifi` driver. Installing that
driver replaces the currently active PicoScenes iwlwifi module and requires a
reboot. Do not install both drivers for the same running kernel.

The official v2.0.0 driver package is staged locally and repackaged as
`2.0.0+ubuntu3`. The local package uses `dpkg-divert` for firmware files that
are also owned by Ubuntu's `linux-firmware` package, and restores them when
FeitCSI is removed. It also repairs upstream CSI chunk resynchronization after
an incomplete frame and accepts the valid 16th data chunk used by 160 MHz
2x2 CSI frames. Preview the change first:

```bash
./switch_to_feitcsi_driver.sh
```

Apply it when ready:

```bash
sudo ./switch_to_feitcsi_driver.sh --apply
sudo reboot
```

After the FeitCSI driver is active:

```bash
sudo -E ./run_4card_feitcsi.sh
python3 datacapture-subscriber-4ax210.py
```

The default measurement is HE-SU, 5520 MHz control frequency, 160 MHz channel
width. Pass bridge options after the launcher to change these values.
