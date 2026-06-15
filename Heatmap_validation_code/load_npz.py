"""Shared NPZ loading helpers for heatmap validation scripts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NPZ_DATASET_DIR = PROJECT_ROOT / "CSI_data" / "npz_dataset"


@dataclass(frozen=True)
class CSIData:
    path: Path
    csi: np.ndarray
    timestamps_ns: Optional[np.ndarray]
    frequency_spacing_hz: Optional[float]


def resolve_npz_path(value) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    name = candidate.name
    if not name.endswith(".npz"):
        name += ".npz"
    dataset_path = NPZ_DATASET_DIR / name
    if dataset_path.is_file():
        return dataset_path.resolve()

    raise FileNotFoundError(
        f"NPZ not found: {value}\n"
        f"Expected a file path or {dataset_path}"
    )


def load_csi_npz(
    value,
    *,
    expected_ndim: Tuple[int, ...] = (4,),
    require_frequency_spacing: bool = False,
    replace_nonfinite: bool = True,
) -> CSIData:
    path = resolve_npz_path(value)
    with np.load(path, allow_pickle=False) as data:
        if "csi" not in data:
            raise ValueError("NPZ does not contain a 'csi' array")

        csi = np.asarray(data["csi"])
        if csi.ndim not in expected_ndim:
            expected = " or ".join(str(value) for value in expected_ndim)
            raise ValueError(
                f"Expected CSI with {expected} dimensions, got {csi.shape}"
            )
        if csi.shape[0] == 0:
            raise ValueError("CSI contains no frames")
        if replace_nonfinite:
            csi = np.nan_to_num(csi, nan=0.0, posinf=0.0, neginf=0.0)

        timestamps_ns = None
        if "reference_rx_system_ns" in data:
            timestamps_ns = np.asarray(
                data["reference_rx_system_ns"],
                dtype=np.float64,
            )
            if timestamps_ns.ndim != 1 or timestamps_ns.size != csi.shape[0]:
                raise ValueError(
                    "reference_rx_system_ns must contain one value per CSI frame"
                )

        frequency_spacing_hz = None
        if "frequency_spacing_hz" in data:
            frequency_spacing_hz = float(
                np.asarray(data["frequency_spacing_hz"]).reshape(-1)[0]
            )
        elif require_frequency_spacing:
            raise ValueError("NPZ does not contain frequency_spacing_hz")

    return CSIData(
        path=path,
        csi=csi,
        timestamps_ns=timestamps_ns,
        frequency_spacing_hz=frequency_spacing_hz,
    )
