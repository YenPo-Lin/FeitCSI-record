import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import stft

try:
    from .load_npz import load_csi_npz, resolve_npz_path
except ImportError:
    from load_npz import load_csi_npz, resolve_npz_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "Heatmap_validation_pics" / "STFT.png"


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


def load_dynamic_amplitude(
    npz_path,
    sts_idx=0,
    top_avg=10,
    fs=100.0,
    ma_seconds=1.0,
):
    csi = load_csi_npz(npz_path).csi
    if not 0 <= sts_idx < csi.shape[1]:
        raise ValueError(
            f"sts_idx={sts_idx} is outside the STS axis of size {csi.shape[1]}"
        )
    if csi.shape[2] != 8:
        raise ValueError(f"Expected 8 RX channels, got {csi.shape[2]}")
    if top_avg <= 0 or top_avg > csi.shape[3]:
        raise ValueError(
            f"--top-avg must be in 1..{csi.shape[3]}, got {top_avg}"
        )

    csi = np.mean(csi[:, sts_idx, :, :top_avg], axis=2)

    window_frames = max(1, int(round(ma_seconds * fs)))
    dynamic_csi = csi - moving_average_time(csi, window_frames)
    amplitude = np.abs(dynamic_csi)
    return amplitude, window_frames


def calculate_pair_stft(
    amplitude,
    fs=100.0,
    window_seconds=1.0,
    overlap=0.9,
    nfft=512,
):
    if amplitude.ndim != 2 or amplitude.shape[1] != 8:
        raise ValueError("Amplitude must have shape (time, 8 RX)")

    nperseg = min(
        amplitude.shape[0],
        max(8, int(round(window_seconds * fs))),
    )
    noverlap = min(nperseg - 1, int(round(nperseg * overlap)))
    nfft = max(int(nfft), nperseg)

    results = []
    for first_rx in range(0, 8, 2):
        pair_amplitude = np.sum(
            amplitude[:, first_rx:first_rx + 2],
            axis=1,
        )
        pair_amplitude -= np.mean(pair_amplitude)

        frequencies, times, spectrum = stft(
            pair_amplitude,
            fs=fs,
            window="hann",
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
            return_onesided=True,
            boundary=None,
            padded=False,
        )
        power_db = 10 * np.log10(np.abs(spectrum) ** 2 + 1e-12)
        power_db -= np.max(power_db)
        results.append((frequencies, times, power_db))

    return results


def plot_stft_pairs(
    results,
    output_path,
    sts_idx=0,
    top_avg=10,
    max_frequency=None,
    dynamic_range_db=35.0,
    show=False,
):
    fig, axes = plt.subplots(
        4,
        1,
        figsize=(12, 5),
        sharex=True,
        constrained_layout=True,
    )

    image = None
    for pair_idx, (ax, result) in enumerate(zip(axes, results)):
        frequencies, times, power_db = result
        if max_frequency is None:
            mask = np.ones_like(frequencies, dtype=bool)
        else:
            mask = frequencies <= max_frequency

        image = ax.pcolormesh(
            times,
            frequencies[mask],
            power_db[mask],
            cmap="jet",
            shading="auto",
            vmin=-dynamic_range_db,
            vmax=0.0,
        )
        rx_start = pair_idx * 2 + 1
        ax.set_ylabel("Hz")
        ax.set_title(
            f"RX{rx_start} + RX{rx_start + 1}",
            fontsize=9,
        )

    axes[-1].set_xlabel("Time (s)")
    fig.suptitle(
        f"Amplitude STFT after MA removal "
        f"(STS {sts_idx + 1}, top {top_avg} subcarriers average)"
    )
    fig.colorbar(
        image,
        ax=axes,
        label="Relative power (dB)",
        pad=0.015,
    )

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    print(f"Saved: {output_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot four RX-pair amplitude STFTs after MA removal."
    )
    parser.add_argument(
        "npz",
        help="Session name, NPZ filename, or full NPZ path",
    )
    parser.add_argument("--sts_idx", type=int, default=0)
    parser.add_argument(
        "--top-avg",
        type=int,
        default=10,
        help="Average the first N subcarriers before processing (default: 10)",
    )
    parser.add_argument("--fs", type=float, default=100.0)
    parser.add_argument("--ma-seconds", type=float, default=1.0)
    parser.add_argument("--stft-window-seconds", type=float, default=1.0)
    parser.add_argument("--overlap", type=float, default=0.9)
    parser.add_argument("--nfft", type=int, default=512)
    parser.add_argument("--max-frequency", type=float, default=50.0)
    parser.add_argument("--dynamic-range-db", type=float, default=35.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.fs <= 0:
        raise ValueError("--fs must be positive")
    if args.ma_seconds <= 0 or args.stft_window_seconds <= 0:
        raise ValueError("MA and STFT window lengths must be positive")
    if not 0 <= args.overlap < 1:
        raise ValueError("--overlap must be in [0, 1)")
    if args.top_avg <= 0:
        raise ValueError("--top-avg must be positive")

    npz_path = resolve_npz_path(args.npz)
    print(f"Input: {npz_path}")
    amplitude, ma_window = load_dynamic_amplitude(
        npz_path,
        sts_idx=args.sts_idx,
        top_avg=args.top_avg,
        fs=args.fs,
        ma_seconds=args.ma_seconds,
    )
    print(
        f"MA removed: window={ma_window} frames "
        f"({args.ma_seconds:.3f} s at {args.fs:.3f} Hz)"
    )

    results = calculate_pair_stft(
        amplitude,
        fs=args.fs,
        window_seconds=args.stft_window_seconds,
        overlap=args.overlap,
        nfft=args.nfft,
    )
    plot_stft_pairs(
        results,
        args.output,
        sts_idx=args.sts_idx,
        top_avg=args.top_avg,
        max_frequency=args.max_frequency,
        dynamic_range_db=args.dynamic_range_db,
        show=args.show,
    )


if __name__ == "__main__":
    main()
