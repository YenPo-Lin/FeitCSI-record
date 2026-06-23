import argparse
import os
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda iterable, **kwargs: iterable

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


def _resolve_Sdim(args, Rxx, eig_val, label=""):
    if getattr(args, "Sdim", None) is not None:
        return max(1, min(int(args.Sdim), eig_val.shape[0] - 1))

    threshold = np.median(eig_val)
    Sdim = int(np.sum(eig_val > threshold))
    return max(1, min(Sdim, eig_val.shape[0] - 1))


class Preprocessing:
    def __init__(self, csi, fs):
        self.csi = csi
        self.fs = fs

    def self_sanitize(self, x):
        mag = np.abs(x)
        mag[mag == 0] = 1
        return x * np.conj(x) / mag
    
    def MA(self, csi_amp, window_size):
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
        csi_pp -= self.MA(csi_pp, self.fs * 1.0)
        return csi_pp

class SteeringVector:
    def __init__(self, args):
        #self.args = args
        self.rx_win = args.rx_win
        self.subc_win = args.subc_win
        self.time_avg_win = getattr(args, 'time_avg_win', 1.0)
        self.dop_win = getattr(args, 'time_dop_win', getattr(args, 'dop_win', 64))
        self.c = 3e8
        self.f_c = args.f_c
        self.fs = args.fs
        self.d = getattr(args, 'antenna_spacing', getattr(args, 'd', 0.015))
        self.subc_stride = args.subc_stride
        self.delta_f = args.delta_f

    def steering_vector_Azi(self, theta_i):
        # ‼️ ULA
        # Uniform linear array steering vector (standard ULA model)
        theta_i = np.deg2rad(theta_i)
        # 長度為 rx_win (天線數)
        idx = np.arange(self.rx_win)
        # 使用 sin(theta) 的相位差： exp(-1j*2*pi*(d*sin(theta)/lambda) * idx)
        wavelength = self.c / self.f_c
        phase = -2.0 * np.pi * (self.d * np.cos(theta_i) / wavelength) * idx
        sv = np.exp(1j * phase)
        return sv.flatten()

    def steering_vector_ToF(self, tau_i):
        """
        做了subcarrier smoothing， steering vector ToF 的長度 = subc_win 
        """
        sub_idx = np.arange(self.subc_win)
        phase_tof = -2 * np.pi * self.delta_f * sub_idx[:, None] * tau_i
        sv = np.exp(1j * phase_tof).astype(np.complex128)
        return sv.flatten()
    
    def steering_vector_Azi_ToF(self, theta_i, tau_j):
        sv_Azi = self.steering_vector_Azi(theta_i)
        sv_ToF = self.steering_vector_ToF(tau_j)
        # Match flattening order used when forming snapshots in `smooth_Rxx`.
        # Snapshots are created by `sub_h.flatten()` where `sub_h` has shape (Lr, Ls)
        # (rx-major, subcarrier-minor). So steering should be kron(sv_Azi, sv_ToF)
        # to produce the same ordering: for each rx index, the block over subcarriers.
        return np.kron(sv_Azi, sv_ToF)
    
    def steering_vector_Doppler(self, fd_i):
        time_idx = np.arange(self.dop_win) / self.fs 
        phase_dop = -2 * np.pi * fd_i * time_idx[:, None]
        sv_dop = np.exp(1j * phase_dop).astype(np.complex128)
        return sv_dop.flatten()

    def steering_vector_ToF_Dop(self, tau_i, fd_j):
        sv_ToF = self.steering_vector_ToF(tau_i)
        sv_Dop = self.steering_vector_Doppler(fd_j)
        return np.kron(sv_Dop, sv_ToF)

class MUSIC_AoA_ToF:
    def __init__(self, args):
        self.args = args
        self.pics_dir = args.pics_dir
        self.antenna_spacing = args.antenna_spacing
        self.subc_win = args.subc_win
        self.rx_win = args.rx_win
        self.rx_stride = getattr(args, 'rx_stride', 1)
        self.time_avg_win = args.time_avg_win
        self.fs = args.fs
        self.tx_index = getattr(args, 'tx_index', 0)

        self.theta_grid = np.arange(args.theta_min, args.theta_max + 1, args.theta_step)
        self.tau_grid = np.arange(args.tau_min, args.tau_max, args.tau_step)

    def smooth_Rxx(self, CSI, frame_idx):
        """
        單幀 RX/Subcarrier Smoothing
        """
        n_frames = CSI.shape[0]

        # window length in frames (at least 1)
        win_frames = max(1, int(round(self.time_avg_win * self.fs)))
        half = win_frames // 2
        start_f = max(0, frame_idx - half)
        end_f = min(n_frames, frame_idx + half + 1)
        frames = range(start_f, end_f)

        # peek at first selected frame to determine dimensions
        sample = CSI[frames[0]]
        if sample.ndim == 3:
            # [tx, rx, subc]
            sample = sample[self.tx_index]
        elif sample.ndim != 2:
            raise ValueError(f"Unsupported CSI frame dimensionality: {sample.shape}")

        M_total, K = sample.shape

        Lr = self.rx_win
        Ls = self.subc_win
        rx_stride = max(1, getattr(self, 'rx_stride', 1))
        subc_stride = max(1, getattr(self.args, 'subc_stride', 1))

        if M_total < Lr or K < Ls:
            raise ValueError(
                f"CSI shape {sample.shape} is smaller than Rx/subcarrier smoothing windows "
                f"(rx_win={Lr}, subc_win={Ls})"
            )

        num_rx_slides = ((M_total - Lr) // rx_stride) + 1
        num_subc_slides = ((K - Ls) // subc_stride) + 1

        num_snapshots = len(frames) * num_rx_slides * num_subc_slides
        X = np.empty((Lr * Ls, num_snapshots), dtype=complex)
        col = 0

        for f in frames:
            csi_frame = CSI[f]
            if csi_frame.ndim == 3:
                csi_frame = csi_frame[self.tx_index]
            elif csi_frame.ndim != 2:
                raise ValueError(f"Unsupported CSI frame shape {csi_frame.shape}")

            for i in range(num_rx_slides):
                start_rx = i * rx_stride
                rx_block = csi_frame[start_rx : start_rx + Lr, :]

                for j in range(num_subc_slides):
                    start_subc = j * subc_stride
                    sub_h = rx_block[:, start_subc : start_subc + Ls]
                    v = sub_h.flatten()
                    X[:, col] = v / (np.linalg.norm(v) + 1e-12)
                    col += 1

        Rxx = (X @ X.conj().T) / num_snapshots
        return Rxx

    def cal_spectrum(self, Rxx):
        print(f"AoA-ToF Covariance Matrix shape = {Rxx.shape}")
        # 1. Steering vector generator
        sv_generator = SteeringVector(self.args)

        # 2. Eigendecomposition
        eig_val, eig_vec = np.linalg.eigh(Rxx)
        idx_order = eig_val.argsort()[::-1]
        eig_val, eig_vec = eig_val[idx_order], eig_vec[:, idx_order]

        # 3. Noise subspace
        Sdim = _resolve_Sdim(self.args, Rxx, eig_val, label=self.__class__.__name__)
        N_dim = eig_val.shape[0] - Sdim
        if N_dim <= 0:
            raise ValueError("No noise subspace: check Sdim/eigenvalues")
        E_n = eig_vec[:, -N_dim:]

        theta = self.theta_grid
        tau = self.tau_grid
        sv_len = Rxx.shape[0]

        # 4. Chunked projection for memory efficiency
        PP = np.empty((len(theta), len(tau)), dtype=float)
        total_grid = PP.size
        chunk_size = 2048

        for start in tqdm(range(0, total_grid, chunk_size), desc="Calculating AoA-ToF Spectrum"):
            end = min(start + chunk_size, total_grid)
            SV_chunk = np.empty((end - start, sv_len), dtype=complex)

            for row, flat_idx in enumerate(range(start, end)):
                i = flat_idx // len(tau)
                j = flat_idx % len(tau)
                # 使用 SteeringVector 的 Azi-ToF 合成向量
                sv = sv_generator.steering_vector_Azi_ToF(theta[i], tau[j])
                SV_chunk[row, :] = sv

            A = SV_chunk @ E_n.conj()
            denom = np.sum(np.abs(A)**2, axis=1)
            PP.reshape(-1)[start:end] = denom

        # 5. 轉換線性頻譜並轉 dB
        P_linear = 1.0 / (PP + 1e-12)
        P_aoa_tof = 10 * np.log10(P_linear + 1e-12)

        return theta, tau, P_linear, P_aoa_tof

    def plot_heatmap(self, frame_idx, theta, tau, P_aoa_tof, args=None, title="AoA-ToF"):
        fig, ax = plt.subplots(figsize=(8, 6))
        c = ax.pcolormesh(tau * 1e9, theta, P_aoa_tof, cmap='jet', shading='auto')
        fig.colorbar(c, ax=ax, label='Power (dB)')
        ax.set_xlabel('ToF (τ) [ns]')
        ax.set_ylabel('AoA (θ) [deg]')
        ax.set_title(f'{title} Heatmap @ Frame {frame_idx}')
        target_dir = getattr(self, 'pics_dir', None) or (getattr(args, 'pics_dir', None) if args is not None else None)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
            out_path = os.path.join(target_dir, f"{frame_idx:04d}_AoAToF_Heatmap.png")
            plt.savefig(out_path, dpi=150)
        else:
            # fallback: show figure
            plt.show()
       
    def gen_spectrum(self, CSI, frame_idx):
        Rxx = self.smooth_Rxx(CSI, frame_idx)
        theta, tau, _, P_aoa_tof = self.cal_spectrum(Rxx)
        self.plot_heatmap(frame_idx, theta, tau, P_aoa_tof, self.args)


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
    parser.add_argument("--fs", type=float, default=100.0) # sampling rate in Hz
    parser.add_argument("--f_c", type=float, default=5.32e9) # 5.57 GHz
    parser.add_argument("--BW", type=float, default=160e6) # 160 MHz # 160e6 / 511 = 313.112 k
    parser.add_argument("--delta_f", type=float, default=313.112e3) # 313.112 kHz

    parser.add_argument("--Sdim", type=int, default=20)
    parser.add_argument("--antenna_spacing", type=float, default=0.015)
    parser.add_argument("--rx_win", type=int, default=3) # for RX smoothing
    parser.add_argument("--subc_win", type=int, default=128) # for subcarrier smoothing
    parser.add_argument("--subc_stride", type=int, default=4)
    parser.add_argument("--rx_stride", type=int, default=1)
    parser.add_argument("--tx-index", type=int, default=0,
        help="Select TX index when CSI has shape [frame, tx, rx, subcarrier]")
    parser.add_argument("--time_dop_win", type=int, default=64) # for doppler smoothing
    parser.add_argument("--time_avg_win", type=float, default=1.0) #for covariance matrix time-average
    # Azimuth range    
    parser.add_argument("--theta-min", type=float, dest="theta_min", default=0)
    parser.add_argument("--theta-max", type=float, dest="theta_max", default=180)
    parser.add_argument("--theta-step", type=float, dest="theta_step", default=1)
    # Tof range
    parser.add_argument("--tau_min", type=float, default=0)
    parser.add_argument("--tau_max", type=float, default=20e-9)
    parser.add_argument("--tau_step", type=float, default=1e-9)
    # Doppler range
    parser.add_argument("--fd_min", type=float, default=-40.0)
    parser.add_argument("--fd_max", type=float, default=40.0)
    parser.add_argument("--fd_step", type=float, default=1.0)
    
    # pics directory
    parser.add_argument("--pics_dir", type=Path, default=DEFAULT_PICS_DIR)
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="cuda",
        help="Compute backend (default: cuda)",
    )
    return parser



if __name__ == "__main__":
    # build argument parser and parse command-line arguments
    parser = build_parser()
    args = parser.parse_args()

    # loading npz file
    data = load_csi_npz(args.npz, expected_ndim=(3, 4))
    csi = data.csi

    # If input-mode == amplitude, convert complex CSI to amplitude
    if args.input_mode == "amplitude":
        csi = np.abs(csi)

    # instantiate MUSIC and run one frame as smoke test
    music = MUSIC_AoA_ToF(args)
    n_frames = csi.shape[0]
    for frame_idx in range(100, 1900, 100):
        music.gen_spectrum(csi, frame_idx)
        plt.close('all')  # close figures to avoid memory issues


    

