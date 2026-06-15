# FeitCSI Four-Card Processing

This directory processes the CSV and NPY files produced by
`datacapture-subscriber-4ax210.py`.

Run the complete matching and merging pipeline from the project root:

```bash
./完整資料處理.sh <session>
```

## Input Layout

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

FeitCSI arrays are stored in C order with shape:

```text
(subcarrier, STS, RX, 1)
```

The merger removes the standard HE cyclic shift diversity phase from each STS
using the same CSD values and rotation as PicoScenes. It then resamples the
nominal HE160 bandwidth from -80 MHz to +80 MHz into 512 equally spaced
frequency points. Real and imaginary CSI components are interpolated
independently. The short unmeasured edge regions beyond the original
indices -1012 to +1012 are linearly extrapolated.

## 1. Match Four NICs

Run from the project root. Replace `<session>` with the recording directory
name under `CSI_data/db`.

```bash
python3 "CSI_data/processing code/csi_matcher.py" \
  --db-root ./CSI_data/db \
  --intermediates-root ./CSI_data/intermediates \
  --exp-names <session>
```

The matcher uses NIC 51 / `csi.rx.1` as the reference timeline and matches
the other NICs by the nearest `rx_system_ns`. The default maximum difference
is 750 microseconds.

For a tighter threshold:

```bash
python3 "CSI_data/processing code/csi_matcher.py" \
  --db-root ./CSI_data/db \
  --intermediates-root ./CSI_data/intermediates \
  --exp-names <session> \
  --tolerance-us 300
```

Output:

```text
CSI_data/intermediates/<session>/matched_csi/<session>_matched.csv
```

## 2. Merge CSI

```bash
python3 "CSI_data/processing code/csi_merger.py" \
  --artifacts-root ./CSI_data/artifacts \
  --intermediates-root ./CSI_data/intermediates \
  --exp-names <session> \
  --dataset-type rt \
  --subcarriers 512
```

Output:

```text
CSI_data/intermediates/<session>/merged_csi/<session>_merged.npz
```

The main `csi` array has shape:

```text
(time, STS, 8_RX, subcarrier)
```

The NPZ also contains:

- `valid_mask`: valid/missing state for each of the eight RX chains
- `topic_valid_mask`: valid/missing state for each physical NIC
- `packet_loss`, `total_packet`, `loss_rate`: statistics for each NIC
- `overall_packet_loss`, `overall_total_packet`, `overall_loss_rate`
- `reference_rx_system_ns`: reference timeline
- `rx_seq`: FeitCSI sequence number for each NIC
- `delta_ns`: receive-time difference from the reference NIC
- `topics`, `nic_ids`, and `antenna_order`
- `original_subcarrier_indices`: the 1992 original HE-SU tone indices
- `resampled_index_positions`: 512 equally spaced fractional tone positions
- `frequency_offsets_hz`: frequency offset for each resampled point
- `frequency_spacing_hz`: approximately 313.112 kHz
- `frequency_span_hz`: 160 MHz
- `nominal_bandwidth_hz`: 160 MHz
- `frequency_edge_extrapolated`: whether the unmeasured band edges were extrapolated
- `frequency_resampled`: whether full-band frequency resampling was applied
- `csd_removed`: whether CSD phase compensation was applied

For each NIC, `total_packet` is the number of rows on the reference timeline.
An unmatched frame or missing NPY file counts as one packet loss.
The merger prints a short loss summary and stores the values only in the NPZ.

## Missing Frames

The default `--missing-policy interpolate` linearly interpolates missing CSI
over time using real and imaginary components. Other choices are:

```text
--missing-policy zero
--missing-policy nan
```

The original missing positions remain available in `valid_mask` even after
interpolation.

Use `--keep-csd` with `csi_merger.py` only when raw transmitter CSD phase is
required.

## Test

```bash
cd "CSI_data/processing code"
python3 -m unittest -v test_feitcsi_processing.py
```
