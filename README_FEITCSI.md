# FeitCSI Four-AX210 Installation and Debug Record

This document records the complete migration of
`5card_CSI_collection_codex` from PicoScenes RX capture to FeitCSI, including
installation, local source changes, driver packaging fixes, debugging, and the
final four-card verification.

Recorded environment:

- Ubuntu 22.04
- Active kernel during development: `6.5.0-15-generic`
- Four capture NICs: Intel AX210
- TX: PicoScenes AX210/AX200 injector
- RX channel: control 5520 MHz, 160 MHz width, center 5570 MHz
- Frame format: HE-SU, MCS 5, 2 STS
- Packet rate: 10 packets/s
- Python environment: `ax210test`

## Why FeitCSI

The PicoScenes Free License limits `MaxNumFrontEnd` to two. One PicoScenes
process could therefore start only two NICs. A second process worked in a
technical experiment but triggered the `MultiInstance` license restriction and
was not used as the final solution.

FeitCSI was selected because it is open source and supports:

- Intel AX200 and AX210
- 802.11ax HE-SU
- 20/40/80/160 MHz
- CSI extraction without a licensed frontend count

The upstream FeitCSI v2.0.0 application and driver required several changes
before four AX210 cards could capture 160 MHz 2x2 CSI reliably.

## Project Layout

Important files:

```text
third_party/FeitCSI/                  Modified FeitCSI v2.0.0 source
third_party/feitcsi-packages/
  feitcsi-iwlwifi_2.0.0+ubuntu3_all.deb
feitcsi_integration/feitcsi_bridge.py FeitCSI UDP to ZeroMQ bridge
feitcsi_integration/test_feitcsi_bridge.py
run_4card_feitcsi.sh                  Four-card launcher
switch_to_feitcsi_driver.sh           Driver install/upgrade script
datacapture-subscriber-4ax210.py      Existing capture UI/subscriber
```

ZeroMQ compatibility is preserved:

| Logical NIC | Stable PCI address | UDP port | ZeroMQ topic |
|---|---|---:|---|
| 51 | `0000:07:00.0` | 8008 | `csi.rx.1` |
| 52 | `0000:08:00.0` | 8009 | `csi.rx.2` |
| 53 | `0000:09:00.0` | 8010 | `csi.rx.3` |
| 54 | `0000:0a:00.0` | 8011 | `csi.rx.4` |

The ZeroMQ publisher binds to `tcp://0.0.0.0:5556`.

## Fresh Installation

### 1. Build Dependencies

```bash
sudo apt update
sudo apt install \
  build-essential flex bison dkms \
  linux-headers-$(uname -r) \
  libgtkmm-3.0-dev libnl-3-dev libnl-genl-3-dev \
  libiw-dev libpcap-dev
```

Do not run `apt autoremove` merely because APT reports these packages as no
longer required. Several are needed to rebuild FeitCSI.

### 2. Build the Modified FeitCSI Application

```bash
cd ~/5card_CSI_collection_codex
make -C third_party/FeitCSI
chmod +x run_4card_feitcsi.sh
chmod +x switch_to_feitcsi_driver.sh
```

Verify:

```bash
third_party/FeitCSI/bin/app --version
third_party/FeitCSI/bin/app --help | grep -E -- '--phy|--udp-port'
```

Expected features:

```text
FeitCSI 2.0.0
--phy=PHY
--udp-port=PORT
```

### 3. Preview the Driver Change

FeitCSI and PicoScenes provide different versions of `iwlwifi`. Do not keep
both driver packages installed for the same running kernel.

```bash
./switch_to_feitcsi_driver.sh
```

This is a dry run. It shows the PicoScenes package to remove and the FeitCSI
package to install.

### 4. Install the FeitCSI Driver

Stop every PicoScenes or FeitCSI capture process first:

```bash
sudo ./switch_to_feitcsi_driver.sh --apply
sudo reboot
```

The installed local driver package is:

```text
feitcsi-iwlwifi 2.0.0+ubuntu3
SHA256:
fa6030fe3ec74e871baf32884e20dd9e77caa4207ef9db6293f85351dfd23e32
```

Verify after reboot:

```bash
dpkg-query -W -f='${Package} ${Version} ${db:Status-Abbrev}\n' feitcsi-iwlwifi
dkms status | grep feitcsi
modinfo -n iwlwifi
sudo find /sys/kernel/debug/iwlwifi -path '*/iwlmvm/csi_enabled'
```

Expected:

- Package state is `ii`
- DKMS is installed for the active kernel
- `iwlwifi.ko` is under `/lib/modules/.../updates/dkms/`
- At least four `csi_enabled` files are present

`/sys/kernel/debug` normally requires `sudo`. A plain `find` may return
`Permission denied`; that is not a driver failure.

## Running Four-Card Capture

### RX Publisher

```bash
cd ~/5card_CSI_collection_codex
conda activate ax210test
sudo -E ./run_4card_feitcsi.sh
```

Keep this terminal open. The launcher:

1. Clears software RF-kill.
2. Verifies that FeitCSI `csi_enabled` controls exist.
3. Resolves each current `phyN` from its stable PCI address.
4. Starts four independent FeitCSI UDP servers.
5. Starts the UDP-to-ZeroMQ bridge.

Example mapping after one reboot:

```text
[FeitCSI] Resolved NIC=51 PCI=0000:07:00.0 -> phy5
[FeitCSI] Resolved NIC=52 PCI=0000:08:00.0 -> phy4
[FeitCSI] Resolved NIC=53 PCI=0000:09:00.0 -> phy1
[FeitCSI] Resolved NIC=54 PCI=0000:0a:00.0 -> phy2
```

The `phyN` values may change on another reboot. The PCI addresses are the
stable identifiers.

### Subscriber

In a second terminal:

```bash
cd ~/5card_CSI_collection_codex
conda activate ax210test
python datacapture-subscriber-4ax210.py
```

All four topics should show increasing packet counts and approximately
10 packets/s with the current transmitter settings.

### TX Settings

No transmitter source change was required. The working TX settings are:

```text
channel = "5520 160 5570"
cbw = 160
format = "hesu"
coding = "ldpc"
mcs = 5
sts = 2
delay = 100000 us
```

The delay corresponds to 10 packets/s.

## FeitCSI Application Changes

Upstream FeitCSI v2.0.0 assumes a single radio. The local fork adds:

### Explicit PHY Selection

New argument:

```text
--phy N
```

Without this change, upstream always creates interfaces on `phys[0]`.

### Per-Process Interface Names

Each process uses names derived from its PHY:

```text
fc1mon / fc1ap
fc2mon / fc2ap
...
```

This prevents four processes from fighting over the original fixed names
`FeitCSImon` and `FeitCSIap`.

### Per-Process UDP Port

New argument:

```text
--udp-port PORT
```

Upstream binds every process to UDP 8008. The four-card setup uses ports
8008 through 8011.

### Per-PHY Ownership

Each process now:

- Removes only interfaces belonging to its selected PHY
- Creates monitor/AP interfaces only on that PHY
- Enables/disables CSI only for the selected PCI device in debugfs
- Restores only its own interfaces

### Memory and Compatibility Fixes

The fork also:

- Uses `delete[]` for CSI byte arrays
- Releases CSI objects in non-plot mode
- Removes per-packet debug printing
- Guards Wi-Fi 7 `NL80211_CHAN_WIDTH_320` for Ubuntu 22.04 headers

## ZeroMQ Bridge

`feitcsi_integration/feitcsi_bridge.py` receives raw FeitCSI UDP frames and
publishes the existing multipart format:

```text
[topic, metadata JSON, complex64 CSI bytes]
```

The CSI array is converted to:

```text
(subcarrier, TX/STS, RX, 1)
```

For the current HE-SU 160 MHz 2x2 frames this is normally:

```text
(1992, 2, 2, 1)
```

Only the main bridge thread uses the ZeroMQ PUB socket. Four UDP worker threads
parse frames and enqueue messages, avoiding unsafe concurrent ZMQ socket use.

Run bridge tests:

```bash
cd ~/5card_CSI_collection_codex/feitcsi_integration
~/miniconda3/envs/ax210test/bin/python -m unittest -v test_feitcsi_bridge.py
```

Tests cover:

- Raw FeitCSI header and IQ parsing
- Array dimension reordering
- Invalid frame rejection
- Simulated UDP-to-ZeroMQ end-to-end delivery

## Driver Packaging and Debug History

### Problem 1: PicoScenes Driver Still Active

Symptom:

```text
The active iwlwifi module does not expose FeitCSI csi_enabled controls.
```

Diagnosis:

```bash
modinfo -n iwlwifi
sudo find /sys/kernel/debug/iwlwifi -path '*/iwlmvm/csi_enabled'
```

PicoScenes and the Ubuntu stock driver do not provide FeitCSI's
`iwlmvm/csi_enabled` interface. Install the FeitCSI DKMS driver and reboot.

### Problem 2: Official DEB Conflicts with linux-firmware

Symptom:

```text
trying to overwrite '/lib/firmware/iwlwifi-cc-a0-77.ucode',
which is also in package linux-firmware
```

Cause:

The official FeitCSI package ships five firmware files already owned by
Ubuntu's `linux-firmware`, but does not declare or safely manage that overlap.

Local fix in `2.0.0+ubuntu1`:

- Added `dpkg-divert` in package maintainer scripts
- Ubuntu firmware is renamed to `.linux-firmware` before installation
- Original firmware is restored when FeitCSI is removed
- Corrected the official `prerm` DKMS version from `1.0` to `2.0.0`

Check diversions:

```bash
dpkg-divert --list | grep iwlwifi
```

### Problem 3: RF-kill Prevents Interface Setup

Symptom:

```text
Failed to bring down interface: Operation not possible due to RF-kill
```

Diagnosis:

```bash
rfkill list wlan
```

All radios showed:

```text
Soft blocked: yes
Hard blocked: no
```

Fix:

```bash
sudo rfkill unblock wlan
```

The launcher now performs this automatically and stops with a clear error if a
soft or hard block remains.

### Problem 4: CSI Chunks Enter Kernel but No Userspace Frames

Symptoms:

- TX was confirmed active
- Monitor interfaces were on 5520/160 MHz
- Subscriber stayed at zero
- Kernel log repeatedly showed:

```text
iwl_mvm_csi_steal
vendor-cmd.c:2186
```

Diagnosis:

```bash
journalctl -k -b --no-pager | \
  grep -E 'iwl_mvm_csi_steal|iwl_mvm_rx_csi_chunk'
```

The kernel was receiving CSI chunks, but a new frame could reuse a chunk slot
before the previous incomplete frame was assembled. Upstream freed the old
frame and then returned, also dropping the new chunk. This caused continuing
loss of synchronization.

Local fix in `2.0.0+ubuntu2`:

- Free an incomplete previous frame
- Continue and preserve the current chunk
- Allow the stream to resynchronize immediately

### Problem 5: 160 MHz 2x2 Rejects the Final Chunk

After the resynchronization fix, the kernel showed:

```text
vendor-cmd.c:2285 iwl_mvm_rx_csi_chunk
```

Register values showed `idx=16`.

The driver defines:

```c
#define IWL_CSI_MAX_EXPECTED_CHUNKS 16
```

Its array contains entry 0 for the header and entries 1 through 16 for CSI
chunks. Upstream incorrectly rejected:

```c
idx >= ARRAY_SIZE(csi_data_entries) - 1
```

That rejects valid chunk 16, so a 160 MHz 2x2 frame can never complete.

Local fix in `2.0.0+ubuntu3`:

```c
idx >= ARRAY_SIZE(csi_data_entries)
```

This accepts valid indices 1 through 16 and rejects only true out-of-range
values.

The resulting FeitCSI UDP payload is about 32 KB:

```text
272-byte header + 1992 * 2 * 2 * 4-byte IQ = 32144 bytes
```

This is below the UDP payload limit of 65507 bytes.

### Problem 6: One Logical NIC Receives Nothing After Reboot

Symptom:

- Three topics received 10 packets/s
- `csi.rx.1` stayed at zero

Cause:

Linux `phyN` numbering changed after reboot. The old fixed mapping pointed NIC
51 to the wrong physical card, and the other logical topics were also
mislabelled.

Example before and after reboot:

```text
PCI 07:00.0 was phy3, later became phy5
```

Final fix:

- Identify cards by stable PCI address
- Resolve the current `phyN` dynamically at every launcher start
- Pass the resolved mapping to the bridge with `--card`

Useful mapping command:

```bash
for phy in /sys/class/ieee80211/phy*; do
  printf '%s pci=%s mac=%s\n' \
    "$(basename "$phy")" \
    "$(basename "$(readlink -f "$phy/device")")" \
    "$(cat "$phy/macaddress")"
done
```

## Operational Diagnostics

### Check Processes and Ports

```bash
pgrep -af 'FeitCSI/bin/app|feitcsi_bridge.py'
ss -lunp | grep -E ':(8008|8009|8010|8011)\b'
ss -ltnp | grep ':5556\b'
```

Expected:

- Four FeitCSI application processes
- UDP ports 8008, 8009, 8010, 8011
- One Python bridge
- TCP port 5556 listening

### Check Interfaces and Channels

```bash
iw dev
```

Each selected PHY should have:

- `fcNmon`, type monitor
- Channel 104 / 5520 MHz
- Width 160 MHz
- Center frequency 5570 MHz

### Check RF-kill

```bash
rfkill list wlan
```

Every selected radio should show:

```text
Soft blocked: no
Hard blocked: no
```

### Check Kernel Errors

```bash
journalctl -k -b --since '10 minutes ago' --no-pager | \
  grep -Ei 'iwl_mvm_csi|firmware error|microcode sw error|WARNING:.*iwl'
```

No output is expected during normal capture.

### Independent 10-Second ZeroMQ Probe

```bash
~/miniconda3/envs/ax210test/bin/python - <<'PY'
import collections
import time
import zmq

ctx = zmq.Context()
sock = ctx.socket(zmq.SUB)
sock.setsockopt_string(zmq.SUBSCRIBE, "csi.rx.")
sock.connect("tcp://127.0.0.1:5556")
poller = zmq.Poller()
poller.register(sock, zmq.POLLIN)
counts = collections.Counter()
end = time.monotonic() + 10

while time.monotonic() < end:
    if sock in dict(poller.poll(250)):
        topic = sock.recv_multipart()[0].decode()
        counts[topic] += 1

for topic in [f"csi.rx.{i}" for i in range(1, 5)]:
    print(topic, counts[topic], f"{counts[topic] / 10:.1f}/s")
PY
```

Final measured result:

```text
csi.rx.1 100 packets 10.0/s
csi.rx.2 100 packets 10.0/s
csi.rx.3 101 packets 10.1/s
csi.rx.4 100 packets 10.0/s
```

Packet sequences were continuous, all four FeitCSI processes remained active,
TCP 5556 was listening, and no new CSI/kernel warnings appeared during the
probe.

## Shutdown

Stop capture with `Ctrl+C` in the publisher terminal.

The launcher sends `stop` to all four UDP control ports, waits briefly, and
then terminates remaining child processes. Do not repeatedly start another
publisher while ports 8008-8011 or 5556 are still occupied.

Check for leftovers:

```bash
pgrep -af 'FeitCSI/bin/app|feitcsi_bridge.py'
ss -lunp | grep -E ':(8008|8009|8010|8011)\b'
ss -ltnp | grep ':5556\b'
```

## Current Known Scope

The setup has been verified with:

- Four AX210 receivers
- HE-SU
- 160 MHz
- Two spatial streams
- 10 packets/s
- Existing four-topic subscriber

A 10-second independent probe confirmed stable delivery. For production data
collection, perform an additional 30-60 minute soak test while monitoring
packet counts, sequence continuity, memory usage, and kernel logs.
