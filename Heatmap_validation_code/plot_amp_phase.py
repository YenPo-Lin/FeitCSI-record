import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

try:
    from .load_npz import load_csi_npz, resolve_npz_path
except ImportError:
    from load_npz import load_csi_npz, resolve_npz_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "Heatmap_validation_pics"
    / "top_subcarriers_amp_phase.png"
)


def infer_fs(timestamps_ns, fs_override=None):
    if fs_override is not None:
        if fs_override <= 0:
            raise ValueError("--fs must be positive")
        return float(fs_override)

    if timestamps_ns is None:
        return None

    intervals = np.diff(np.asarray(timestamps_ns, dtype=np.float64))
    intervals = intervals[np.isfinite(intervals) & (intervals > 0)]
    if intervals.size == 0:
        return None
    return 1e9 / float(np.median(intervals))


def moving_average_time(csi, window_size):
    window_size = max(1, int(round(window_size)))
    half = window_size // 2
    starts = np.maximum(0, np.arange(csi.shape[0]) - half)
    ends = np.minimum(csi.shape[0], np.arange(csi.shape[0]) + half + 1)
    cumulative = np.concatenate(
        [np.zeros_like(csi[:1]), np.cumsum(csi, axis=0)],
        axis=0,
    )
    counts = (ends - starts).reshape((-1,) + (1,) * (csi.ndim - 1))
    return (cumulative[ends] - cumulative[starts]) / counts


def load_csi(
    npz_path,
    sts_idx=0,
    fs=100.0,
    remove_ma=True,
    top_avg=10,
):
    loaded = load_csi_npz(npz_path, expected_ndim=(3, 4))
    csi = loaded.csi
    if csi.ndim == 4:
        if not 0 <= sts_idx < csi.shape[1]:
            raise ValueError(
                f"sts_idx={sts_idx} is outside the STS axis of size {csi.shape[1]}"
            )
        csi = csi[:, sts_idx]

    if csi.shape[2] < 1:
        raise ValueError("CSI contains no subcarriers")
    if top_avg <= 0 or top_avg > csi.shape[2]:
        raise ValueError(
            f"--top-avg must be in 1..{csi.shape[2]}, got {top_avg}"
        )

    # Average complex CSI first so both amplitude and phase remain defined.
    csi = np.mean(csi[:, :, :top_avg], axis=2)

    timestamps = loaded.timestamps_ns
    if timestamps is not None:
        time_s = (timestamps - timestamps[0]) / 1e9
    elif fs is not None:
        if fs <= 0:
            raise ValueError("--fs must be positive")
        time_s = np.arange(csi.shape[0], dtype=np.float64) / fs
    else:
        time_s = np.arange(csi.shape[0], dtype=np.float64)

    packet_rate = infer_fs(timestamps, fs_override=fs)
    if remove_ma:
        if packet_rate is None:
            raise ValueError(
                "Moving-average removal requires timestamps or an explicit --fs"
            )
        window_size = max(1, int(round(packet_rate)))
        csi = csi - moving_average_time(csi, window_size)
        print(
            f"Moving average removed: window={window_size} frames "
            f"(1.0 s at {packet_rate:.3f} Hz)"
        )

    return csi, time_s


def plot_subcarrier_average(
    csi_average,
    time_s,
    output_path,
    sts_idx=0,
    top_avg=10,
    show=False,
):
    if csi_average.ndim != 2:
        raise ValueError("csi_average must have shape (time, RX)")

    amplitude = np.abs(csi_average)
    phase = np.unwrap(np.angle(csi_average), axis=0)
    rx_labels = [f"RX{rx + 1}" for rx in range(csi_average.shape[1])]
    rx_axis = np.arange(csi_average.shape[1])

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(12, 5),
        sharex=True,
        constrained_layout=True,
    )

    amplitude_image = axes[0].pcolormesh(
        time_s,
        rx_axis,
        amplitude.T,
        cmap="jet",
        shading="auto",
    )
    fig.colorbar(amplitude_image, ax=axes[0], label="CSI amplitude")

    phase_image = axes[1].pcolormesh(
        time_s,
        rx_axis,
        phase.T,
        cmap="jet",
        shading="auto",
    )
    fig.colorbar(phase_image, ax=axes[1], label="Unwrapped phase (rad)")

    axes[0].set_title("Amplitude")

    axes[1].set_xlabel("Time (s)")
    axes[1].set_title("Unwrapped phase")

    for ax in axes:
        ax.set_yticks(rx_axis)
        ax.set_yticklabels(rx_labels, fontsize=7)
        ax.set_ylabel("RX / subcarrier")

    fig.suptitle(
        f"Top {top_avg} subcarriers average after MA removal "
        f"(STS {sts_idx + 1})"
    )

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    print(f"Saved: {output_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)

    return amplitude, phase


def main():
    parser = argparse.ArgumentParser(
        description="Plot amplitude and phase of averaged top subcarriers."
    )
    parser.add_argument(
        "npz",
        help="Session name, NPZ filename, or full NPZ path",
    )
    parser.add_argument("--sts_idx", type=int, default=0, help="Zero-based STS index")
    parser.add_argument(
        "--fs",
        type=float,
        default=100.0,
        help="Packet rate in Hz (default: 100)",
    )
    parser.add_argument(
        "--top-avg",
        type=int,
        default=10,
        help="Average the first N subcarriers before processing (default: 10)",
    )
    parser.add_argument(
        "--no-remove-ma",
        action="store_true",
        help="Plot without subtracting the default 1-second moving average",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output image path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    npz_path = resolve_npz_path(args.npz)
    print(f"Input: {npz_path}")
    csi_average, time_s = load_csi(
        npz_path,
        sts_idx=args.sts_idx,
        fs=args.fs,
        remove_ma=not args.no_remove_ma,
        top_avg=args.top_avg,
    )
    plot_subcarrier_average(
        csi_average,
        time_s,
        args.output,
        sts_idx=args.sts_idx,
        top_avg=args.top_avg,
        show=args.show,
    )


if __name__ == "__main__":
    main()
