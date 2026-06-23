import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

try:
    from .load_npz import load_csi_npz
except ImportError:
    from load_npz import load_csi_npz


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "Heatmap_validation_pics" / "tx0_amp_phase.png"


def select_tx_and_average_subcarriers(csi, tx_index=0, top_avg=10):
    if csi.ndim == 4:
        if not 0 <= tx_index < csi.shape[1]:
            raise ValueError(f"tx_index={tx_index} is outside TX axis size {csi.shape[1]}")
        csi = csi[:, tx_index]
    elif csi.ndim != 3:
        raise ValueError(f"Expected CSI shape (time, tx, rx, subcarrier) or (time, rx, subcarrier), got {csi.shape}")

    if top_avg <= 0 or top_avg > csi.shape[2]:
        raise ValueError(f"--top-avg must be in 1..{csi.shape[2]}, got {top_avg}")

    return np.mean(csi[:, :, :top_avg], axis=2)


def make_time_axis(loaded, fs):
    if loaded.timestamps_ns is not None:
        return (loaded.timestamps_ns - loaded.timestamps_ns[0]) / 1e9
    if fs <= 0:
        raise ValueError("--fs must be positive")
    return np.arange(loaded.csi.shape[0], dtype=np.float64) / fs


def plot_amp_phase(csi_average, time_s, output, tx_index=0, top_avg=10, show=False):
    amplitude = np.abs(csi_average)
    phase = np.unwrap(np.angle(csi_average), axis=0)
    rx_axis = np.arange(csi_average.shape[1])
    rx_labels = [f"RX{rx}" for rx in rx_axis]

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(12, 5),
        sharex=True,
        constrained_layout=True,
    )

    amp_image = axes[0].pcolormesh(time_s, rx_axis, amplitude.T, cmap="jet", shading="auto")
    fig.colorbar(amp_image, ax=axes[0], label="Amplitude")
    axes[0].set_title("Amplitude")

    phase_image = axes[1].pcolormesh(time_s, rx_axis, phase.T, cmap="jet", shading="auto")
    fig.colorbar(phase_image, ax=axes[1], label="Phase (rad)")
    axes[1].set_title("Phase")
    axes[1].set_xlabel("Time (s)")

    for ax in axes:
        ax.set_ylabel("RX")
        ax.set_yticks(rx_axis)
        ax.set_yticklabels(rx_labels)

    fig.suptitle(f"TX{tx_index} RX amplitude/phase, first {top_avg} subcarriers averaged")

    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    print(f"Saved: {output}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot TX-to-RX amplitude and phase from averaged subcarriers."
    )
    parser.add_argument("npz", help="Session name, NPZ filename, or full NPZ path")
    parser.add_argument("--tx-index", type=int, default=0, help="Zero-based TX index (default: 0)")
    parser.add_argument("--top-avg", type=int, default=10, help="Average first N subcarriers (default: 10)")
    parser.add_argument("--fs", type=float, default=100.0, help="Fallback packet rate when timestamps are absent")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    loaded = load_csi_npz(args.npz, expected_ndim=(3, 4))
    csi_average = select_tx_and_average_subcarriers(
        loaded.csi,
        tx_index=args.tx_index,
        top_avg=args.top_avg,
    )
    time_s = make_time_axis(loaded, args.fs)

    print(f"Input: {loaded.path}")
    print(f"Using TX{args.tx_index}, RX0..RX{csi_average.shape[1] - 1}, first {args.top_avg} subcarriers")
    plot_amp_phase(
        csi_average,
        time_s,
        args.output,
        tx_index=args.tx_index,
        top_avg=args.top_avg,
        show=args.show,
    )


if __name__ == "__main__":
    main()
