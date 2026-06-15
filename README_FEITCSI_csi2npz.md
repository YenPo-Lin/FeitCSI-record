# FeitCSI CSI-to-NPZ Processing

This document describes the four-card FeitCSI processing pipeline started by:

```bash
./csi2npz.sh <session>
```

## Input Data

The pipeline reads data recorded by `datacapture-subscriber-4ax210.py`:

```text
CSI_data/
  db/<session>/csi.rx.1/*.csv
  db/<session>/csi.rx.2/*.csv
  db/<session>/csi.rx.3/*.csv
  db/<session>/csi.rx.4/*.csv
  artifacts/<session>/arrays/csi.rx.1/*.npy
  artifacts/<session>/arrays/csi.rx.2/*.npy
  artifacts/<session>/arrays/csi.rx.3/*.npy
  artifacts/<session>/arrays/csi.rx.4/*.npy
```

Each original FeitCSI NPY array has shape:

```text
(1992 subcarriers, 2 STS, 2 RX, 1 CSI frame)
```

The four topics map to four physical NICs:

```text
csi.rx.1 -> NIC 51
csi.rx.2 -> NIC 52
csi.rx.3 -> NIC 53
csi.rx.4 -> NIC 54
```

## Complete Pipeline

```text
Four-card CSV metadata
  |
  | nearest rx_system_ns matching
  v
matched.csv
  |
  | load each card's NPY array
  | remove HE-SU CSD phase
  | resample the full 160 MHz bandwidth to 512 points
  | interpolate missing packets over time
  v
merged.npz
```

## Frame Matching

`csi_matcher.py` uses `csi.rx.1` as the reference timeline. Each frame from
the other three NICs is matched one-to-one to the nearest `rx_system_ns`.

The default maximum time difference is:

```text
750 microseconds
```

Change it with:

```bash
./csi2npz.sh <session> --tolerance-us 300
```

Matched output:

```text
CSI_data/intermediates/<session>/matched_csi/<session>_matched.csv
```

## CSD Removal

The merger removes the HE-SU cyclic shift diversity phase using the same
rotation convention as PicoScenes.

For two spatial streams:

```text
STS 1 CSD =    0 ns
STS 2 CSD = -400 ns
```

For each original subcarrier index `k`, the correction is:

```text
H_corrected(k, sts) =
    H_raw(k, sts) * exp(j * 2*pi*k*CSD_samples(sts) / 2048)
```

For 160 MHz:

```text
CSD_samples = CSD_ns * 160 * 0.001
```

Removing CSD compensates the standard transmitter-introduced phase slope. It
does not remove CFO, SFO, packet timing offset, RF-chain phase offset, or
cross-NIC clock differences.

## Original FeitCSI Tones

HE-SU 160 MHz uses 1992 measured tones with 78.125 kHz spacing:

```text
-1012 ... -515
 -509 ...  -12
   12 ...  509
  515 ... 1012
```

The original tones are not continuous because DC and guard regions are not
measured. The original frequency offset is:

```text
frequency_offset(k) = k * 78.125 kHz
```

## Full-Band 512-Point Resampling

After CSD removal, the complex CSI is resampled across the complete measured
frequency span:

```text
first frequency offset = -80 MHz
last frequency offset  = +80 MHz
frequency span         = 160 MHz
output points          = 512
new spacing            = 313111.545988 Hz
```

Real and imaginary components are linearly interpolated independently:

```text
real(H) -> linear frequency interpolation
imag(H) -> linear frequency interpolation
```

The output points are equally spaced frequency samples. They are not original
Wi-Fi subcarrier indices.

Override the output point count with:

```bash
./csi2npz.sh <session> --subcarriers 512
```

## Missing Packet Interpolation

The default missing-packet policy is time-axis interpolation:

```text
--missing-policy interpolate
```

For each NIC, STS, RX, and frequency point, real and imaginary components are
interpolated independently over the matched frame timeline.

Other policies:

```bash
./csi2npz.sh <session> --missing-policy nan
./csi2npz.sh <session> --missing-policy zero
```

The original missing positions remain recorded in `valid_mask` even after
interpolation.

## Packet Loss

Packet loss is calculated before time interpolation.

For each NIC:

```text
total_packet = number of rows on the reference timeline
packet_loss  = unmatched frame or missing NPY file
loss_rate    = packet_loss / total_packet
```

Example terminal output:

```text
csi.rx.1: loss 0/1000 (0.00%)
csi.rx.2: loss 5/1000 (0.50%)
csi.rx.3: loss 2/1000 (0.20%)
csi.rx.4: loss 1/1000 (0.10%)
overall: loss 8/4000 (0.20%)
```

No separate packet-loss CSV is generated. Statistics are stored in the NPZ.

## NPZ Output

Output file:

```text
CSI_data/intermediates/<session>/merged_csi/<session>_merged.npz
```

Main CSI shape:

```text
(time, 2 STS, 8 RX, 512 frequency points)
```

NPZ fields:

```text
csi
valid_mask
topic_valid_mask
reference_rx_system_ns
rx_seq
delta_ns
topics
nic_ids
antenna_order
missing_policy
original_subcarrier_indices
resampled_index_positions
frequency_offsets_hz
frequency_spacing_hz
frequency_span_hz
nominal_bandwidth_hz
frequency_edge_extrapolated
frequency_resampled
csd_removed
packet_loss
total_packet
loss_rate
overall_packet_loss
overall_total_packet
overall_loss_rate
```

## MUSIC ToF

Use the stored frequency offsets rather than assuming Wi-Fi's original
78.125 kHz spacing:

```python
import numpy as np

data = np.load("SESSION_merged.npz")
csi = data["csi"]
frequency_offsets_hz = data["frequency_offsets_hz"]

tau = 10e-9
steering = np.exp(-1j * 2 * np.pi * frequency_offsets_hz * tau)
```

The full 160 MHz frequency span gives a conventional delay resolution of
approximately:

```text
1 / 160 MHz = 6.25 ns
```

Super-resolution MUSIC performance additionally depends on SNR, calibration,
multipath model, snapshots, and steering-vector correctness.

## MUSIC AoA

Each AX210 provides two RX chains. Before using phase across antennas:

- Measure the physical antenna spacing.
- Calibrate fixed RF-chain phase offsets.
- Keep antenna order consistent with `antenna_order`.
- Do not treat four independent AX210 cards as one coherent eight-element
  array unless cross-card LO, clock, timing, and phase offsets are calibrated.

## Commands

Process one session:

```bash
./csi2npz.sh <session>
```

Process all sessions:

```bash
./csi2npz.sh --all
```

Show options:

```bash
./csi2npz.sh --help
```

Run processing tests:

```bash
cd "CSI_data/processing code"
python3 -m unittest -v test_feitcsi_processing.py
```
