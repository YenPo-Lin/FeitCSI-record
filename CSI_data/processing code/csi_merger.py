#!/usr/bin/env python3
"""Merge matched FeitCSI arrays into a time x STS x RX x subcarrier tensor."""

import csv
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from PicoSense_common_args import build_base_parser


CSD_NS = np.asarray([0, -400, -200, -600, -350, -650, -100, -750])
HE160_BANDWIDTH_HZ = 160_000_000.0
HE_TONE_SPACING_HZ = 78_125.0
HE160_EDGE_INDEX = HE160_BANDWIDTH_HZ / (2 * HE_TONE_SPACING_HZ)


def load_rows(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def he160_subcarrier_indices(subcarrier_count: int) -> np.ndarray:
    if subcarrier_count != 1992:
        raise ValueError(
            "HE-SU 160 MHz CSD removal currently requires 1992 FeitCSI tones; "
            f"got {subcarrier_count}"
        )
    return np.concatenate([
        np.arange(-1012, -514),
        np.arange(-509, -11),
        np.arange(12, 510),
        np.arange(515, 1013),
    ]).astype(np.int16)


def remove_csd(
    array: np.ndarray,
    subcarrier_indices: np.ndarray,
    bandwidth_mhz: int = 160,
) -> np.ndarray:
    """Apply the same HE cyclic-shift correction used by PicoScenes."""
    sts_count = array.shape[0]
    if sts_count > len(CSD_NS):
        raise ValueError(f"CSD removal supports at most {len(CSD_NS)} STS")

    csd_samples = CSD_NS[:sts_count] * bandwidth_mhz * 0.001
    nfft = 64 * (bandwidth_mhz / 20) * 4
    phase = (
        2j
        * np.pi
        * csd_samples[:, np.newaxis]
        * subcarrier_indices[np.newaxis, :]
        / nfft
    )
    rotation = np.exp(phase).astype(np.complex64)
    return np.ascontiguousarray(array * rotation[:, np.newaxis, :])


def resample_full_bandwidth(
    array: np.ndarray,
    subcarrier_indices: np.ndarray,
    output_count: int,
) -> Tuple[np.ndarray, np.ndarray]:
    if output_count < 2:
        raise ValueError(f"resampled subcarrier count must be at least 2; got {output_count}")

    # The measured HE160 tones stop at +/-1012, while the nominal channel
    # edges are +/-1024 tone spacings. Use a small linear extrapolation at
    # both edges so the output frequency aperture remains exactly 160 MHz.
    target_indices = np.linspace(
        -HE160_EDGE_INDEX,
        HE160_EDGE_INDEX,
        output_count,
        dtype=np.float64,
    )

    def interpolate_with_linear_edges(values: np.ndarray) -> np.ndarray:
        interpolated = np.interp(target_indices, subcarrier_indices, values)
        left = target_indices < subcarrier_indices[0]
        right = target_indices > subcarrier_indices[-1]
        if left.any():
            slope = (
                (values[1] - values[0])
                / (subcarrier_indices[1] - subcarrier_indices[0])
            )
            interpolated[left] = values[0] + slope * (
                target_indices[left] - subcarrier_indices[0]
            )
        if right.any():
            slope = (
                (values[-1] - values[-2])
                / (subcarrier_indices[-1] - subcarrier_indices[-2])
            )
            interpolated[right] = values[-1] + slope * (
                target_indices[right] - subcarrier_indices[-1]
            )
        return interpolated

    result = np.empty((*array.shape[:-1], output_count), dtype=np.complex64)
    for sts in range(array.shape[0]):
        for rx in range(array.shape[1]):
            values = array[sts, rx]
            result[sts, rx] = (
                interpolate_with_linear_edges(values.real)
                + 1j * interpolate_with_linear_edges(values.imag)
            )
    return np.ascontiguousarray(result), target_indices


def normalize_feitcsi_array(
    path: Path,
    output_subcarriers: int,
    apply_csd_removal: bool = True,
) -> np.ndarray:
    array = np.load(path, allow_pickle=False)
    if array.ndim == 4:
        if array.shape[-1] != 1:
            raise ValueError(f"expected final CSI-frame dimension 1, got {array.shape}")
        array = array[..., 0]
    if array.ndim != 3:
        raise ValueError(f"expected (subcarrier, STS, RX[, 1]), got {array.shape}")
    # Collector stores FeitCSI arrays in C order as (subcarrier, STS, RX, 1).
    array = np.ascontiguousarray(array.transpose(1, 2, 0), dtype=np.complex64)
    indices = he160_subcarrier_indices(array.shape[2])
    if apply_csd_removal:
        array = remove_csd(array, indices)
    array, _ = resample_full_bandwidth(array, indices, output_subcarriers)
    return array


def array_path(artifacts_root: str, exp_name: str, topic: str, filename: str) -> Optional[Path]:
    if not filename:
        return None
    path = Path(artifacts_root) / exp_name / "arrays" / f"csi.rx.{topic}" / filename
    return path if path.is_file() else None


def infer_shape(rows: List[dict], args, exp_name: str) -> Tuple[int, int, int]:
    for row in rows:
        for topic in args.topics:
            path = array_path(
                args.artifacts_root,
                exp_name,
                topic,
                row.get(f"topic{topic}_array_saved", ""),
            )
            if path is not None:
                shape = normalize_feitcsi_array(
                    path,
                    output_subcarriers=args.subcarriers,
                    apply_csd_removal=not args.keep_csd,
                ).shape
                return shape
    raise ValueError(f"No FeitCSI arrays found for {exp_name}")


def interpolate_missing(data: np.ndarray, valid: np.ndarray) -> None:
    timeline = np.arange(data.shape[0])
    for topic_index in range(data.shape[1]):
        known = valid[:, topic_index]
        if not known.any():
            data[:, topic_index] = 0
            continue
        if known.all():
            continue
        known_x = timeline[known]
        source = data[known, topic_index]
        target = data[:, topic_index]
        for sts in range(data.shape[2]):
            for rx in range(data.shape[3]):
                for subcarrier in range(data.shape[4]):
                    values = source[:, sts, rx, subcarrier]
                    target[:, sts, rx, subcarrier] = (
                        np.interp(timeline, known_x, values.real)
                        + 1j * np.interp(timeline, known_x, values.imag)
                    )


def output_path(exp_name: str, args) -> Path:
    if args.dataset_type == "rt":
        return (
            Path(args.intermediates_root)
            / exp_name
            / "merged_csi"
            / f"{exp_name}_merged.npz"
        )
    date = exp_name.split("-")[0]
    basename = exp_name.split("_")[-1]
    return Path(args.data_root) / date / "csi" / f"csi_{date}-{basename}.npz"


def packet_loss_statistics(
    valid: np.ndarray,
    topics: List[str],
    nic_ids: List[str],
) -> List[dict]:
    total_per_topic = valid.shape[0]
    statistics = []
    for topic_index, (topic, nic_id) in enumerate(zip(topics, nic_ids)):
        received = int(valid[:, topic_index].sum())
        lost = total_per_topic - received
        statistics.append({
            "topic": f"csi.rx.{topic}",
            "nic_id": nic_id,
            "received_packet": received,
            "packet_loss": lost,
            "total_packet": total_per_topic,
            "loss_rate": lost / total_per_topic if total_per_topic else 0.0,
        })

    total_packet = int(valid.size)
    received_packet = int(valid.sum())
    packet_loss = total_packet - received_packet
    statistics.append({
        "topic": "overall",
        "nic_id": "all",
        "received_packet": received_packet,
        "packet_loss": packet_loss,
        "total_packet": total_packet,
        "loss_rate": packet_loss / total_packet if total_packet else 0.0,
    })
    return statistics


def topic_pcis_from_rows(rows: List[dict], topics: List[str]) -> List[str]:
    pcis = []
    for topic in topics:
        pci = ""
        key = f"topic{topic}_pci"
        for row in rows:
            pci = row.get(key, "")
            if pci:
                break
        pcis.append(pci)
    return pcis


def merge_experiment(exp_name: str, args) -> Path:
    if len(args.topics) != len(args.nic_ids):
        raise ValueError("--topics and --nic-ids must contain the same number of values")

    matched_path = (
        Path(args.intermediates_root)
        / exp_name
        / "matched_csi"
        / f"{exp_name}_matched.csv"
    )
    rows = load_rows(matched_path)
    if not rows:
        raise ValueError(f"Matched CSV is empty: {matched_path}")

    sts_count, rx_count, subcarrier_count = infer_shape(rows, args, exp_name)
    frame_count = len(rows)
    topic_count = len(args.topics)
    data = np.full(
        (frame_count, topic_count, sts_count, rx_count, subcarrier_count),
        np.nan + 1j * np.nan,
        dtype=np.complex64,
    )
    valid = np.zeros((frame_count, topic_count), dtype=bool)
    rx_seq = np.full((frame_count, topic_count), -1, dtype=np.int64)
    delta_ns = np.full((frame_count, topic_count), np.iinfo(np.int64).min, dtype=np.int64)
    timestamps_ns = np.asarray(
        [int(row["reference_rx_system_ns"]) for row in rows],
        dtype=np.int64,
    )

    for frame_index, row in enumerate(rows):
        for topic_index, topic in enumerate(args.topics):
            filename = row.get(f"topic{topic}_array_saved", "")
            path = array_path(args.artifacts_root, exp_name, topic, filename)
            if path is None:
                continue
            array = normalize_feitcsi_array(
                path,
                output_subcarriers=args.subcarriers,
                apply_csd_removal=not args.keep_csd,
            )
            if array.shape != (sts_count, rx_count, subcarrier_count):
                raise ValueError(
                    f"{path}: shape {array.shape} does not match "
                    f"{(sts_count, rx_count, subcarrier_count)}"
                )
            data[frame_index, topic_index] = array
            valid[frame_index, topic_index] = True
            rx_seq[frame_index, topic_index] = int(row[f"topic{topic}_rx_seq"])
            delta_value = row.get(f"topic{topic}_delta_ns", "")
            if delta_value != "":
                delta_ns[frame_index, topic_index] = int(delta_value)

    statistics = packet_loss_statistics(valid, args.topics, args.nic_ids)

    if args.missing_policy == "zero":
        data[~valid] = 0
    elif args.missing_policy == "interpolate":
        interpolate_missing(data, valid)

    # (time, topic, STS, RX, subcarrier) -> (time, STS, topic*RX, subcarrier)
    merged = data.transpose(0, 2, 1, 3, 4).reshape(
        frame_count,
        sts_count,
        topic_count * rx_count,
        subcarrier_count,
    )
    antenna_order = [int(value) for value in args.antenna_order]
    if sorted(antenna_order) != list(range(topic_count * rx_count)):
        raise ValueError(
            f"--antenna-order must be a permutation of "
            f"0..{topic_count * rx_count - 1}"
        )
    merged = merged[:, :, antenna_order, :]
    valid_rx = np.repeat(valid, rx_count, axis=1)[:, antenna_order]

    destination = output_path(exp_name, args)
    destination.parent.mkdir(parents=True, exist_ok=True)
    per_topic_statistics = statistics[:-1]
    overall_statistics = statistics[-1]
    pcis = topic_pcis_from_rows(rows, args.topics)
    raw_subcarrier_indices = he160_subcarrier_indices(1992)
    _, resampled_index_positions = resample_full_bandwidth(
        np.empty((1, 1, 1992), dtype=np.complex64),
        raw_subcarrier_indices,
        subcarrier_count,
    )
    frequency_offsets_hz = resampled_index_positions * HE_TONE_SPACING_HZ
    frequency_spacing_hz = float(frequency_offsets_hz[1] - frequency_offsets_hz[0])
    frequency_span_hz = float(frequency_offsets_hz[-1] - frequency_offsets_hz[0])
    np.savez_compressed(
        destination,
        csi=merged,
        valid_mask=valid_rx,
        topic_valid_mask=valid,
        reference_rx_system_ns=timestamps_ns,
        rx_seq=rx_seq,
        delta_ns=delta_ns,
        topics=np.asarray(args.topics),
        nic_ids=np.asarray(args.nic_ids),
        pcis=np.asarray(pcis),
        antenna_order=np.asarray(antenna_order),
        missing_policy=np.asarray(args.missing_policy),
        original_subcarrier_indices=raw_subcarrier_indices,
        resampled_index_positions=resampled_index_positions,
        frequency_offsets_hz=frequency_offsets_hz,
        frequency_spacing_hz=np.asarray(frequency_spacing_hz),
        frequency_span_hz=np.asarray(frequency_span_hz),
        nominal_bandwidth_hz=np.asarray(HE160_BANDWIDTH_HZ),
        frequency_edge_extrapolated=np.asarray(True),
        frequency_resampled=np.asarray(True),
        csd_removed=np.asarray(not args.keep_csd),
        packet_loss=np.asarray(
            [item["packet_loss"] for item in per_topic_statistics],
            dtype=np.int64,
        ),
        total_packet=np.asarray(
            [item["total_packet"] for item in per_topic_statistics],
            dtype=np.int64,
        ),
        loss_rate=np.asarray(
            [item["loss_rate"] for item in per_topic_statistics],
            dtype=np.float64,
        ),
        overall_packet_loss=np.asarray(overall_statistics["packet_loss"]),
        overall_total_packet=np.asarray(overall_statistics["total_packet"]),
        overall_loss_rate=np.asarray(overall_statistics["loss_rate"]),
    )
    print(f"Saved {destination} csi={merged.shape}")
    for item in statistics:
        print(
            f"{item['topic']}: loss "
            f"{item['packet_loss']}/{item['total_packet']} "
            f"({item['loss_rate'] * 100:.2f}%)"
        )
    return destination


def main(args) -> None:
    if not args.exp_names:
        raise ValueError("--exp-names is required")
    for exp_name in args.exp_names:
        merge_experiment(exp_name, args)


if __name__ == "__main__":
    parser = build_base_parser()
    parser.add_argument(
        "--dataset-type",
        choices=["data", "rt"],
        default="rt",
        help="rt writes under intermediates; data writes under data-root",
    )
    parser.add_argument(
        "--missing-policy",
        choices=["nan", "zero", "interpolate"],
        default="interpolate",
        help="How to represent a NIC frame missing from a matched row",
    )
    parser.add_argument(
        "--keep-csd",
        action="store_true",
        help="Keep transmitter cyclic shift diversity instead of removing it",
    )
    main(parser.parse_args())
