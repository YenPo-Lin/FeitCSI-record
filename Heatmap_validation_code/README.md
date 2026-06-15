# FeitCSI ToF-Doppler Heatmap

This directory reads the complex 4D CSI produced by `csi2npz.sh`:

```text
(time, STS, RX, 512 resampled tones)
```

`load_npz.py` is the shared loader used by `MUSIC.py`,
`plot_amp_phase.py`, and `plot_STFT.py`. It resolves session names under
`CSI_data/npz_dataset`, validates CSI shape, replaces non-finite values, and
loads timestamps and frequency spacing when available.

Run from the repository root:

```bash
conda activate ax210test
python -m Heatmap_validation_code.main \
  --csi_path CSI_data/processing/output/example.npz
```

All generated images are saved by default under
`Heatmap_validation_code/pics`. Use `--pics_dir` only to override this path.

The program uses `frequency_spacing_hz` and `reference_rx_system_ns` stored in
the NPZ. Use `--fs RATE` only when timestamps are unavailable or when an
explicit packet-rate override is required.

The current validated path generates a ToF-Doppler MUSIC heatmap. AoA across
the four independent AX210 cards is not enabled because it requires stable
inter-card phase synchronization and array calibration.

## Preprocessing modes

The default `--phase_method self_sanitize` follows the amplitude-only sensing
path:

```text
complex CSI -> |CSI| -> moving-average removal -> DFS / MUSIC
```

This is suitable for motion features and amplitude Doppler spectrograms.
MUSIC can still produce a feature heatmap, but its ToF axis is not an absolute
physical delay because absolute ToF is encoded in cross-subcarrier phase.

The optional `SFO_PDD` mode keeps complex CSI and removes the fitted linear
phase slope across subcarriers. That fitted slope also contains physical ToF,
so this mode must not be used when absolute ToF is the target. The
`SFO_PDD_CFO` mode additionally removes per-frame common phase; this can also
remove common Doppler. These corrections require a separate phase reference or
calibration measurement to distinguish hardware phase from sensing phase.

## Amplitude over time

`Plot.amp_along_time` plots one STS as vertically stacked amplitude and phase
heatmaps. The x-axis is time and the y-axis runs from RX1/subcarrier1 through
RX8/subcarrier1024:

```python
import numpy as np
from Heatmap_validation_code.Plot import amp_along_time

with np.load("result.npz", allow_pickle=False) as data:
    csi = data["csi"]
    timestamps = data["reference_rx_system_ns"]

time_s = (timestamps - timestamps[0]) / 1e9
amp_along_time(
    csi,
    time_s=time_s,
    sts_idx=0,
    pics_dir="Heatmap_validation_code/pics",
)
```

The normal `main.py` processing path also generates
`MA_amp_phase_along_time.png` immediately after moving-average preprocessing.
It then averages STFT power over every RX and subcarrier in STS 1 and saves
`MA_Doppler_along_time.png`.
