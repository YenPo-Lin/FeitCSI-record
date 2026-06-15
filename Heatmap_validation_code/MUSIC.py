import argparse
import os
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np

try:
    from .load_npz import load_csi_npz
except ImportError:
    from load_npz import load_csi_npz

try:
    import torch
except ModuleNotFoundError:
    torch = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PICS_DIR = PROJECT_ROOT / "Heatmap_validation_pics"
GPU_PYTHON = Path("/home/tonic/miniconda3/bin/python")


def relaunch_with_gpu_python_if_needed(device):
    if device == "cpu" or torch is not None:
        return
    if os.environ.get("MUSIC_GPU_RELAUNCHED") == "1":
        return
    if not GPU_PYTHON.is_file() or Path(sys.executable).resolve() == GPU_PYTHON.resolve():
        return

    print(
        f"[MUSIC] PyTorch is unavailable in {sys.executable}.\n"
        f"[MUSIC] Relaunching with GPU Python: {GPU_PYTHON}",
        flush=True,
    )
    environment = os.environ.copy()
    environment["MUSIC_GPU_RELAUNCHED"] = "1"
    os.execve(
        str(GPU_PYTHON),
        [str(GPU_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        environment,
    )


class Preprocessing:
    def __init__(self, csi, fs):
        self.csi = csi
        self.fs = fs

    def self_sanitize(x):
        mag = np.abs(x)
        mag[mag == 0] = 1
        return x * np.conj(x) / mag
    
    def MA(csi_amp, window_size):
        window_size = max(1, int(round(window_size)))
        half = window_size // 2
        x = np.asarray(csi_amp)
        out = np.empty_like(x, dtype=np.result_type(x, np.float64))

        for i in range(x.shape[0]):
            start = max(0, i - half)
            end = min(x.shape[0], i + half + 1)
            out[i] = np.mean(x[start:end], axis=0)
        return out
    
    def preprocess(self):
        csi_pp = self.self_sanitize(self.csi)
        csi_pp -= self.MA(csi_pp, self.fs*1.0)
        return csi_pp



class MUSIC_ToF_Dop:
    """
    2D MUSIC (ToF-Doppler), aligned with the validated XMUSIC convention:
    - Snapshot flatten order: (subcarrier, time).reshape(-1)
    - Steering: exp(-j * (2*pi*delta_f*m*tau - 2*pi*fd*t/fs))
    - Spectrum: 1 / (1 - a^H Es Es^H a + epsilon)
    """
    def __init__(self, args):
        self.args = args
        self.subc_win = int(args.subc_win)
        self.subc_stride = int(args.subc_stride)
        self.dop_win = int(args.dop_win)
        self.time_win = int(args.time_win)
        self.fs = float(args.fs)
        self.new_delta_f = float(
            getattr(
                args,
                "new_delta_f",
                float(args.delta_f) * int(args.subc_space),
            )
        )
        self.epsilon = float(
            getattr(args, "tof_dop_epsilon", getattr(args, "epsilon", 1e-5))
        )
        self.floor_percentile = float(getattr(args, "floor_percentile", 5.0))
        self.tau_grid = np.arange(args.tau_min, args.tau_max, args.tau_step)
        self.fd_grid = np.arange(
            args.fd_min,
            args.fd_max + 0.5 * args.fd_step,
            args.fd_step,
        )
        self.device = self._resolve_device(getattr(args, "device", "auto"))
        self.last_meta = None

    def _resolve_device(self, requested):
        if requested == "cpu":
            print("[MUSIC] Device: CPU")
            return "cpu"
        if torch is None:
            if requested == "cuda":
                raise RuntimeError(
                    "CUDA requested but PyTorch is not installed in this Python"
                )
            print("[MUSIC] PyTorch unavailable; falling back to CPU")
            return "cpu"
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            print(f"[MUSIC] Device: CUDA ({device_name})")
            return "cuda"
        if requested == "cuda":
            raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
        print("[MUSIC] CUDA unavailable; falling back to CPU")
        return "cpu"

    def _check_gpu_memory(self, vector_length):
        # Complex64 covariance alone uses 8*N^2 bytes. Eigh and workspace need
        # several copies, so use a conservative four-times estimate.
        estimated = 4 * 8 * vector_length * vector_length
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        print(
            f"[MUSIC] Estimated GPU matrix/workspace: "
            f"{estimated / 2**30:.2f} GiB; free={free_bytes / 2**30:.2f} GiB"
        )
        if estimated > free_bytes * 0.85:
            raise MemoryError(
                "Selected subc_win*dop_win is too large for available GPU memory. "
                "Reduce --subc_win or --dop_win."
            )

    def _estimate_signal_dimension(self, eigenvalues):
        if self.args.Sdim is not None:
            return int(self.args.Sdim)

        values = np.maximum(np.real(eigenvalues), np.finfo(float).eps)
        ratios = values[:-1] / values[1:]
        dimension = int(np.argmax(ratios) + 1) if ratios.size else 1
        print(f"[MUSIC] Estimated signal dimension={dimension}")
        return dimension

    def _as_time_tx_rx_subc(self, csi):
        if csi.ndim == 3:
            return csi[:, None, :, :]
        if csi.ndim == 4:
            return csi
        raise ValueError(
            "MUSIC_ToF_Dop expects CSI shape (T,Rx,K) or (T,Tx,Rx,K), "
            f"got {csi.shape}"
        )

    def _select_time_context(self, csi, frame_idx):
        total_frames = csi.shape[0]
        context_len = min(self.time_win, total_frames)
        frame_idx = int(np.clip(frame_idx, 0, total_frames - 1))
        start = int(
            np.clip(
                frame_idx - context_len // 2,
                0,
                total_frames - context_len,
            )
        )
        return csi[start:start + context_len], start, start + context_len

    def Rxx_smooth(self, csi, frame_idx):
        csi = self._as_time_tx_rx_subc(csi)
        segment, start, end = self._select_time_context(csi, frame_idx)
        context_len, num_tx, num_rx, num_subcarriers = segment.shape
        if context_len < self.dop_win or num_subcarriers < self.subc_win:
            raise ValueError("Not enough CSI samples for the selected windows")

        num_time_slides = context_len - self.dop_win + 1
        num_freq_slides = (
            (num_subcarriers - self.subc_win) // self.subc_stride
        ) + 1
        vector_length = self.subc_win * self.dop_win
        if self.device == "cuda":
            self._check_gpu_memory(vector_length)
            segment_backend = torch.as_tensor(
                segment,
                dtype=torch.complex64,
                device="cuda",
            )
            covariance = torch.zeros(
                (vector_length, vector_length),
                dtype=torch.complex64,
                device="cuda",
            )
        else:
            segment_backend = segment
            covariance = np.zeros(
                (vector_length, vector_length),
                dtype=np.complex128,
            )
        snapshots = 0

        # Tx/STS and RX are independent snapshots, matching the reference.
        for tx in range(num_tx):
            for rx in range(num_rx):
                for freq_slide in range(num_freq_slides):
                    first_subcarrier = freq_slide * self.subc_stride
                    frequency_block = segment_backend[
                        :,
                        tx,
                        rx,
                        first_subcarrier:first_subcarrier + self.subc_win,
                    ]
                    if self.device == "cuda":
                        windows = frequency_block.unfold(0, self.dop_win, 1)
                        vectors = windows.reshape(num_time_slides, vector_length)
                        if self.args.snapshot_norm:
                            norms = torch.linalg.vector_norm(
                                vectors,
                                dim=1,
                                keepdim=True,
                            )
                            vectors = vectors / (norms + 1e-12)
                        covariance += vectors.T @ vectors.conj()
                    else:
                        windows = np.lib.stride_tricks.sliding_window_view(
                            frequency_block,
                            window_shape=self.dop_win,
                            axis=0,
                        )
                        vectors = windows.reshape(
                            num_time_slides,
                            vector_length,
                        ).astype(np.complex128)
                        if self.args.snapshot_norm:
                            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                            vectors /= norms + 1e-12
                        covariance += vectors.T @ vectors.conj()
                    snapshots += vectors.shape[0]

        covariance /= max(snapshots, 1)
        if self.device == "cuda":
            covariance = (covariance + covariance.mH) / 2
        else:
            covariance = (covariance + covariance.conj().T) / 2
        self.last_meta = {
            "context": (start, end),
            "num_tx": num_tx,
            "num_rx": num_rx,
            "num_freq_slides": num_freq_slides,
            "num_time_slides": num_time_slides,
            "num_snapshots": snapshots,
        }
        print(
            f"[MUSIC] Rxx={covariance.shape}, Tx={num_tx}, RX={num_rx}, "
            f"snapshots={snapshots}, context={start}:{end}"
        )
        return covariance

    def steering_vector(self, tau, fd):
        subcarrier = np.arange(self.subc_win)[:, None]
        time = (np.arange(self.dop_win) / self.fs)[None, :]
        phase_tof = 2 * np.pi * self.new_delta_f * subcarrier * tau
        phase_doppler = -2 * np.pi * fd * time
        vector = np.exp(-1j * (phase_tof + phase_doppler)).reshape(-1)
        return vector / np.sqrt(vector.size)

    def cal_spectrum(self, covariance):
        if self.device == "cuda":
            return self._cal_spectrum_gpu(covariance)

        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        order = np.argsort(eigenvalues)[::-1]
        eigenvalues = eigenvalues[order]
        eigenvectors = eigenvectors[:, order]

        signal_dimension = self._estimate_signal_dimension(eigenvalues)
        signal_dimension = int(
            np.clip(signal_dimension, 1, covariance.shape[0] - 1)
        )
        print(f"[MUSIC] Using signal dimension={signal_dimension} for spectrum")
        signal_subspace = eigenvectors[:, :signal_dimension]

        spectrum = np.empty(
            (len(self.tau_grid), len(self.fd_grid)),
            dtype=np.float64,
        )
        epsilon = self.epsilon
        for tau_idx, tau in enumerate(self.tau_grid):
            for fd_idx, fd in enumerate(self.fd_grid):
                steering = self.steering_vector(tau, fd)
                projection = steering.conj() @ signal_subspace
                signal_power = np.real(projection @ projection.conj())
                spectrum[tau_idx, fd_idx] = 1.0 / (
                    max(0.0, 1.0 - signal_power) + epsilon
                )
        return self.tau_grid, self.fd_grid, spectrum

    def _cal_spectrum_gpu(self, covariance):
        eigenvalues, eigenvectors = torch.linalg.eigh(covariance)
        order = torch.argsort(eigenvalues, descending=True)
        eigenvalues = eigenvalues[order]
        eigenvectors = eigenvectors[:, order]

        eigenvalues_cpu = eigenvalues.detach().cpu().numpy()
        signal_dimension = self._estimate_signal_dimension(eigenvalues_cpu)
        signal_dimension = int(
            np.clip(signal_dimension, 1, covariance.shape[0] - 1)
        )
        signal_subspace = eigenvectors[:, :signal_dimension]

        fd_tensor = torch.as_tensor(
            self.fd_grid,
            dtype=torch.float32,
            device="cuda",
        )
        subcarrier = torch.arange(
            self.subc_win,
            dtype=torch.float32,
            device="cuda",
        )[None, :, None]
        time = (
            torch.arange(
                self.dop_win,
                dtype=torch.float32,
                device="cuda",
            )
            / self.fs
        )[None, None, :]
        phase_doppler = -2 * torch.pi * fd_tensor[:, None, None] * time

        spectrum = np.empty(
            (len(self.tau_grid), len(self.fd_grid)),
            dtype=np.float32,
        )
        tau_chunk = max(1, int(getattr(self.args, "tau_chunk", 4)))
        epsilon = self.epsilon
        normalization = np.sqrt(self.subc_win * self.dop_win)

        with torch.inference_mode():
            for start in range(0, len(self.tau_grid), tau_chunk):
                end = min(start + tau_chunk, len(self.tau_grid))
                tau_tensor = torch.as_tensor(
                    self.tau_grid[start:end],
                    dtype=torch.float32,
                    device="cuda",
                )[:, None, None]
                phase_tof = (
                    2
                    * torch.pi
                    * self.new_delta_f
                    * tau_tensor
                    * subcarrier
                )
                phase = phase_tof[:, None, :, :] + phase_doppler[None, :, :, :]
                steering = torch.exp(-1j * phase).reshape(
                    (end - start) * len(self.fd_grid),
                    -1,
                )
                steering /= normalization
                projection = steering.conj() @ signal_subspace
                signal_power = torch.sum(
                    torch.abs(projection) ** 2,
                    dim=1,
                )
                chunk_spectrum = 1.0 / (
                    torch.clamp(1.0 - signal_power, min=0.0) + epsilon
                )
                spectrum[start:end] = (
                    chunk_spectrum.reshape(end - start, len(self.fd_grid))
                    .detach()
                    .cpu()
                    .numpy()
                )

        return self.tau_grid, self.fd_grid, spectrum

    def plot_heatmap(self, frame_idx, tau, fd, spectrum, output_path=None):
        spectrum_db = 10 * np.log10(spectrum + 1e-12)
        spectrum_db -= np.nanmax(spectrum_db)
        vmin = max(
            np.nanpercentile(spectrum_db, self.floor_percentile),
            -self.args.dynamic_range_db,
        )
        if abs(vmin) < 1e-9:
            vmin = -1.0

        fig, ax = plt.subplots(figsize=(8, 6))
        image = ax.pcolormesh(
            fd,
            tau * 1e9,
            spectrum_db,
            cmap="jet",
            shading="auto",
            vmin=vmin,
            vmax=0.0,
        )
        fig.colorbar(image, ax=ax, label="Relative power (dB)")
        ax.axvline(0, color="white", linestyle="--", linewidth=1, alpha=0.6)
        ax.set_xlabel("Doppler shift (Hz)")
        ax.set_ylabel("ToF (ns)")
        if self.last_meta is None:
            title = f"ToF-Doppler MUSIC @ frame {frame_idx}"
        else:
            start, end = self.last_meta["context"]
            title = (
                f"ToF-Doppler MUSIC @ frame {frame_idx} "
                f"(context {start}:{end}, "
                f"{self.last_meta['num_snapshots']} snapshots)"
            )
        ax.set_title(title)
        fig.tight_layout()

        if output_path is None:
            pics_dir = Path(
                getattr(self.args, "pics_dir", DEFAULT_PICS_DIR)
            )
            output_path = pics_dir / f"{frame_idx:04d}_ToFDop_Heatmap.png"
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        print(f"Saved: {output_path}")

    def gen_spectrum(self, csi, frame_idx, output_path):
        covariance = self.Rxx_smooth(csi, frame_idx)
        tau, fd, spectrum = self.cal_spectrum(covariance)
        self.plot_heatmap(frame_idx, tau, fd, spectrum, output_path)
        return tau, fd, spectrum


def build_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "npz",
        help="Session name, NPZ filename, or full NPZ path",
    )
    parser.add_argument(
        "--input-mode",
        choices=["amplitude", "complex"],
        default="amplitude",
        help="Use CSI amplitude or complex CSI (default: amplitude)",
    )
    parser.add_argument("--fs", type=float, default=200.0)
    parser.add_argument("--frame_idx", type=int, default=2000)
    parser.add_argument("--Sdim", type=int, default=None)
    parser.add_argument("--subc_win", type=int, default=64)
    parser.add_argument("--subc_stride", type=int, default=16)
    parser.add_argument("--subc_space", type=int, default=1)
    parser.add_argument("--dop_win", type=int, default=64)
    parser.add_argument("--time_win_sec", type=float, default=1.0) #for covariance matrix time-average
    parser.add_argument("--tau_min", type=float, default=0)
    parser.add_argument("--tau_max", type=float, default=50e-9)
    parser.add_argument("--tau_step", type=float, default=1e-9)
    parser.add_argument("--fd_min", type=float, default=-40.0)
    parser.add_argument("--fd_max", type=float, default=40.0)
    parser.add_argument("--fd_step", type=float, default=1.0)
    parser.add_argument("--dynamic-range-db", type=float, default=35.0)
    parser.add_argument("--floor-percentile", type=float, default=5.0)
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="cuda",
        help="Compute backend (default: cuda)",
    )
    parser.add_argument(
        "--tau-chunk",
        type=int,
        default=4,
        help="Number of ToF grid points per GPU spectrum batch",
    )
    parser.add_argument(
        "--no-snapshot-norm",
        action="store_false",
        dest="snapshot_norm",
    )
    parser.set_defaults(snapshot_norm=True)
    parser.add_argument(
        "--output",
        default=None,
        help="Output PNG path",
    )
    return parser


def main():
    args = build_parser().parse_args()
    relaunch_with_gpu_python_if_needed(args.device)
    if args.fs <= 0:
        raise ValueError("--fs must be positive")
    if args.subc_win <= 0 or args.subc_stride <= 0 or args.dop_win <= 0:
        raise ValueError("MUSIC windows and stride must be positive")
    if args.time_win_sec <= 0:
        raise ValueError("--time_win_sec must be positive")

    loaded = load_csi_npz(args.npz, require_frequency_spacing=True)
    npz_path = loaded.path
    csi = loaded.csi
    args.delta_f = loaded.frequency_spacing_hz
    args.new_delta_f = args.delta_f * args.subc_space
    args.time_win = max(args.dop_win, int(round(args.time_win_sec * args.fs)))
    args.time_win = min(args.time_win, csi.shape[0])
    if args.frame_idx is None:
        args.frame_idx = csi.shape[0] // 2

    session_name = npz_path.stem
    output_path = (
        Path(args.output)
        if args.output
        else DEFAULT_PICS_DIR
        / f"{session_name}_ToF_Doppler_{args.input_mode}.png"
    )

    print(f"Input: {npz_path}")
    print(
        f"CSI={csi.shape}, STS and RX preserved as snapshots, "
        f"mode={args.input_mode}, fs={args.fs:.3f} Hz, "
        f"delta_f={args.delta_f:.3f} Hz"
    )

    # Keep the validated preprocessing implementation unchanged and apply it
    # exactly once for amplitude mode.
    if args.input_mode == "amplitude":
        csi = Preprocessing.self_sanitize(csi)
        csi -= Preprocessing.MA(csi, args.fs * 1.0)

    # Keep STS/Tx as independent MUSIC snapshots.
    # csi = np.mean(csi, axis=1)
    print(f"CSI before MUSIC: {csi.shape}")

    model = MUSIC_ToF_Dop(args)
    tau, fd, spectrum = model.gen_spectrum(
        csi,
        args.frame_idx,
        output_path,
    )

    peak = np.unravel_index(np.argmax(spectrum), spectrum.shape)
    print(
        f"Peak: ToF={tau[peak[0]] * 1e9:.3f} ns, "
        f"Doppler={fd[peak[1]]:.3f} Hz"
    )


if __name__ == "__main__":
    main()
