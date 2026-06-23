"""Shared NPZ loading helpers for heatmap validation scripts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NPZ_DATASET_DIR = PROJECT_ROOT / "CSI_data" / "npz_dataset"


@dataclass(frozen=True)
class CSIData:
    path: Path
    csi: np.ndarray
    timestamps_ns: Optional[np.ndarray] = None
    frequency_spacing_hz: Optional[float] = None


def resolve_npz_path(value) -> Path:
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve()

    filename = path.name if path.name.endswith(".npz") else f"{path.name}.npz"
    dataset_path = NPZ_DATASET_DIR / filename
    if dataset_path.is_file():
        return dataset_path.resolve()

    raise FileNotFoundError(f"NPZ not found: {value}")


def load_csi_npz(
    value,
    *,
    expected_ndim=(4,),
    require_frequency_spacing: bool = False,
    replace_nonfinite: bool = True,
) -> CSIData:
    path = resolve_npz_path(value)

    with np.load(path, allow_pickle=False) as data:
        if "csi" not in data:
            raise ValueError("NPZ does not contain a 'csi' array")

        csi = np.asarray(data["csi"])
        expected = tuple(expected_ndim)
        if csi.ndim not in expected:
            raise ValueError(f"Expected CSI ndim {expected}, got shape {csi.shape}")
        if csi.shape[0] == 0:
            raise ValueError("CSI contains no frames")

        if replace_nonfinite:
            csi = np.nan_to_num(csi, nan=0.0, posinf=0.0, neginf=0.0)

        timestamps_ns = (
            np.asarray(data["reference_rx_system_ns"], dtype=np.float64)
            if "reference_rx_system_ns" in data
            else None
        )
        if timestamps_ns is not None and timestamps_ns.shape != (csi.shape[0],):
            raise ValueError("reference_rx_system_ns must match CSI frame count")

        if require_frequency_spacing and "frequency_spacing_hz" not in data:
            raise ValueError("NPZ does not contain frequency_spacing_hz")

        frequency_spacing_hz = (
            float(np.ravel(data["frequency_spacing_hz"])[0])
            if "frequency_spacing_hz" in data
            else None
        )

    return CSIData(path, csi, timestamps_ns, frequency_spacing_hz)
