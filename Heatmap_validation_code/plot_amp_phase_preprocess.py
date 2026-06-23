#!/usr/bin/env python3
"""
Plot CSI amplitude / phase along time with simple preprocessing.

Main functions
--------------
1. moving_average()
   Default: 100 samples.

2. amp_pha_along_time()
   Generate three heatmaps:
   - amplitude minus moving_average(amplitude)
   - direct phase along time
   - RX-pair phase difference: RX1-RX2, RX3-RX4, ...

3. pca_amp_phase_along_time()
   Generate two heatmaps:
   - TX0-RX0..RX7 amplitude after PCA over subcarriers
   - TX0-RX0..RX7 phase after PCA over subcarriers

Expected CSI shape:
    (time, tx, rx, subcarrier)

Example:
    python plot_amp_phase_preprocess.py data.npz --mode both
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

try:
    from .load_npz import load_csi_npz
except ImportError:
    from load_npz import load_csi_npz


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Heatmap_validation_pics"


def moving_average(x, window_size=100, axis=0):
    """
    Moving average along one axis.

    Default window_size is 100 samples.
    """
    window_size = max(1, int(round(window_size)))
    x = np.asarray(x)

    if window_size == 1:
        return x.copy()

    x_move = np.moveaxis(x, axis, 0)
    n = x_move.shape[0]
    flat = x_move.reshape(n, -1)

    out = np.empty_like(flat, dtype=np.result_type(flat, np.float64))
    half = window_size // 2

    csum = np.cumsum(flat, axis=0, dtype=np.result_type(flat, np.float64))
    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        total = csum[end - 1]
        if start > 0:
            total = total - csum[start - 1]
        out[i] = total / (end - start)

    out = out.reshape(x_move.shape)
    return np.moveaxis(out, 0, axis)


def make_time_axis(loaded, fs):
    """Use timestamps if present; otherwise use fs."""
    if getattr(loaded, "timestamps_ns", None) is not None:
        return (loaded.timestamps_ns - loaded.timestamps_ns[0]) / 1e9

    if fs <= 0:
        raise ValueError("--fs must be positive when timestamps are absent")

    return np.arange(loaded.csi.shape[0], dtype=np.float64) / fs


def _select_top_subcarriers(csi_tx, top_subc=10):
    """Select the first N subcarriers from shape (time, rx, subcarrier)."""
    if csi_tx.ndim != 3:
        raise ValueError(f"Expected csi_tx shape (time, rx, subcarrier), got {csi_tx.shape}")

    n_subc = csi_tx.shape[-1]
    top_subc = int(top_subc)

    if top_subc <= 0 or top_subc > n_subc:
        raise ValueError(f"top_subc must be in 1..{n_subc}, got {top_subc}")

    return csi_tx[:, :, :top_subc]


def _save(fig, output):
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    print(f"Saved: {output}")
    plt.close(fig)


def amp_pha_along_time(
    csi,
    time_s,
    output,
    top_subc=10,
    ma_samples=100,
):
    """
    Plot three heatmaps:
    1. amp - moving_average(amp)
    2. direct phase along time
    3. phase difference for RX pairs: RX1-RX2, RX3-RX4, ...

    Notes
    -----
    - Amplitude is averaged over selected subcarriers first.
    - Phase is directly computed from the complex average over selected subcarriers.
    - Phase difference uses non-overlapping RX pairs:
        RX1-RX2, RX3-RX4, RX5-RX6, RX7-RX8, ...
      in 1-based labels.
    """
    csi_tx = csi[:, 0]
    csi_win = _select_top_subcarriers(csi_tx, top_subc=top_subc)

    # 1) Amplitude heatmap: amp - MA(amp)
    amp = np.mean(np.abs(csi_win), axis=-1)  # (time, rx)
    amp_dyn = amp - moving_average(amp, window_size=ma_samples, axis=0)

    # 2) Direct phase heatmap
    # Average complex CSI over selected subcarriers, then take phase directly.
    csi_avg = np.mean(csi_win, axis=-1)  # (time, rx)
    phase = np.unwrap(np.angle(csi_avg), axis=0)

    # 3) RX-pair phase difference heatmap: RX1-RX2, RX3-RX4, ...
    n_rx = csi_win.shape[1]
    n_pairs = n_rx // 2

    if n_pairs > 0:
        left = csi_win[:, 0 : 2 * n_pairs : 2, :]
        right = csi_win[:, 1 : 2 * n_pairs : 2, :]
        z_pair = np.mean(left * np.conj(right), axis=-1)  # (time, pair)
        phase_diff = np.unwrap(np.angle(z_pair), axis=0)
        pair_labels = [f"RX{2*i+1}-RX{2*i+2}" for i in range(n_pairs)]
    else:
        phase_diff = np.empty((csi_win.shape[0], 0))
        pair_labels = []

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(13, 8),
        sharex=True,
        constrained_layout=True,
    )

    rx_axis = np.arange(n_rx)
    rx_labels = [f"RX{i+1}" for i in rx_axis]

    im0 = axes[0].pcolormesh(time_s, rx_axis, amp_dyn.T, cmap="jet", shading="auto")
    fig.colorbar(im0, ax=axes[0], label="Amplitude - MA(Amplitude)")
    axes[0].set_title(f"Amplitude after MA removal, TX0, first {top_subc} subcarriers")
    axes[0].set_ylabel("RX")
    axes[0].set_yticks(rx_axis)
    axes[0].set_yticklabels(rx_labels)

    im1 = axes[1].pcolormesh(time_s, rx_axis, phase.T, cmap="jet", shading="auto")
    fig.colorbar(im1, ax=axes[1], label="Phase (rad)")
    axes[1].set_title("Direct phase along time")
    axes[1].set_ylabel("RX")
    axes[1].set_yticks(rx_axis)
    axes[1].set_yticklabels(rx_labels)

    if n_pairs > 0:
        pair_axis = np.arange(n_pairs)
        im2 = axes[2].pcolormesh(time_s, pair_axis, phase_diff.T, cmap="jet", shading="auto")
        fig.colorbar(im2, ax=axes[2], label="Phase difference (rad)")
        axes[2].set_yticks(pair_axis)
        axes[2].set_yticklabels(pair_labels)
    else:
        axes[2].text(0.5, 0.5, "Need at least 2 RX antennas", ha="center", va="center")
    axes[2].set_title("RX-pair phase difference")
    axes[2].set_ylabel("RX pair")
    axes[2].set_xlabel("Time (s)")

    fig.suptitle(
        "TX0 amplitude / phase along time "
        f"(top_subc={top_subc}, MA={ma_samples} samples)"
    )

    _save(fig, output)


def _first_pca_score(x):
    """Return the first PCA score for x with shape (time, subcarrier)."""
    x = np.asarray(x, dtype=np.float64)
    x = x - np.mean(x, axis=0, keepdims=True)
    cov = x.T @ x
    eigvals, eigvecs = np.linalg.eigh(cov)
    pc1 = eigvecs[:, np.argmax(eigvals)]
    return x @ pc1


def _subcarrier_pca_by_rx(values):
    """Run PCA over subcarriers for each RX and return shape (time, rx)."""
    if values.ndim != 3:
        raise ValueError(f"Expected shape (time, rx, subcarrier), got {values.shape}")

    scores = [
        _first_pca_score(values[:, rx_idx, :])
        for rx_idx in range(values.shape[1])
    ]
    return np.stack(scores, axis=1)


def _plot_pca_amp_phase(amp_scores, phase_scores, time_s, output):
    rx_axis = np.arange(amp_scores.shape[1])
    rx_labels = [f"RX{rx}" for rx in rx_axis]

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(13, 7),
        sharex=True,
        constrained_layout=True,
    )

    amp_image = axes[0].pcolormesh(time_s, rx_axis, amp_scores.T, cmap="jet", shading="auto")
    fig.colorbar(amp_image, ax=axes[0], label="Amplitude PC1 score")
    axes[0].set_title("TX0 amplitude PCA over subcarriers")

    phase_image = axes[1].pcolormesh(time_s, rx_axis, phase_scores.T, cmap="jet", shading="auto")
    fig.colorbar(phase_image, ax=axes[1], label="Phase PC1 score")
    axes[1].set_title("TX0 phase PCA over subcarriers")
    axes[1].set_xlabel("Time (s)")

    for ax in axes:
        ax.set_ylabel("RX")
        ax.set_yticks(rx_axis)
        ax.set_yticklabels(rx_labels)

    _save(fig, output)


def pca_amp_phase_along_time(
    csi,
    time_s,
    output,
    top_subc=100,
    ma_samples=100,
):
    """
    Run PCA over subcarriers independently for each RX and plot two heatmaps.

    Outputs have shape (time, rx), so the y-axis remains TX0-RX0..RXN.
    """
    csi_tx = _select_top_subcarriers(csi[:, 0], top_subc=top_subc)

    amp = np.abs(csi_tx)
    amp_dyn = amp - moving_average(amp, window_size=ma_samples, axis=0)
    amp_scores = _subcarrier_pca_by_rx(amp_dyn)

    phase = np.unwrap(np.angle(csi_tx), axis=0)
    phase_scores = _subcarrier_pca_by_rx(phase)

    _plot_pca_amp_phase(amp_scores, phase_scores, time_s, output)


def main():
    parser = argparse.ArgumentParser(
        description="Plot CSI amplitude/phase heatmaps with MA and PCA preprocessing."
    )
    parser.add_argument("npz", help="Session name, NPZ filename, or full NPZ path")
    parser.add_argument("--fs", type=float, default=100.0, help="Fallback packet rate when timestamps are absent")

    parser.add_argument(
        "--mode",
        choices=["amp_phase", "pca", "both"],
        default="both",
        help="Which plot to generate",
    )

    parser.add_argument("--ma-samples", type=int, default=100, help="Moving-average window in samples")
    parser.add_argument("--top-subc", type=int, default=10, help="Use the first N subcarriers")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))

    args = parser.parse_args()

    loaded = load_csi_npz(args.npz, expected_ndim=(4,))
    csi = loaded.csi
    time_s = make_time_axis(loaded, args.fs)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input: {loaded.path}")
    print(f"CSI shape: {csi.shape}")
    print("TX index: 0")
    print(f"Top subcarriers: {args.top_subc}")
    print(f"MA samples: {args.ma_samples}")

    if args.mode in ("amp_phase", "both"):
        out1 = output_dir / "amp_phase.png"
        amp_pha_along_time(
            csi,
            time_s,
            out1,
            top_subc=args.top_subc,
            ma_samples=args.ma_samples,
        )

    if args.mode in ("pca", "both"):
        pca_out = output_dir / "pca_amp_phase.png"
        pca_amp_phase_along_time(
            csi,
            time_s,
            pca_out,
            top_subc=args.top_subc,
            ma_samples=args.ma_samples,
        )


if __name__ == "__main__":
    main()
