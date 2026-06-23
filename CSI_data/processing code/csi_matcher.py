#!/usr/bin/env python3
"""Match FeitCSI frames from multiple NICs using host receive timestamps."""

import csv
import glob
import os
from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, List, Optional

from common_args import build_base_parser


@dataclass(frozen=True)
class Sample:
    topic: str
    nic_id: str
    pci: str
    rx_seq: int
    rx_system_ns: int
    array_saved: str


def discover_experiments(db_root: str, requested: List[str]) -> List[str]:
    if len(requested) == 1 and requested[0].endswith("-all"):
        prefix = requested[0][:-4]
        return sorted(
            entry for entry in os.listdir(db_root)
            if entry.startswith(prefix) and os.path.isdir(os.path.join(db_root, entry))
        )
    return requested


def parse_int(value) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def load_samples(db_root: str, exp_name: str, topic: str) -> List[Sample]:
    pattern = os.path.join(db_root, exp_name, f"csi.rx.{topic}", "*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        raise ValueError(f"No FeitCSI CSV files found: {pattern}")

    samples: List[Sample] = []
    for filename in files:
        with open(filename, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                timestamp = parse_int(row.get("rx_system_ns"))
                rx_seq = parse_int(row.get("rx_seq"))
                array_saved = row.get("array_saved", "")
                error = row.get("error", "")
                if timestamp is None or rx_seq is None or not array_saved or error:
                    continue
                samples.append(
                    Sample(
                        topic=topic,
                        nic_id=row.get("nic_id", ""),
                        pci=row.get("pci", ""),
                        rx_seq=rx_seq,
                        rx_system_ns=timestamp,
                        array_saved=os.path.basename(array_saved),
                    )
                )

    samples.sort(key=lambda sample: (sample.rx_system_ns, sample.rx_seq))
    if not samples:
        raise ValueError(f"No usable FeitCSI samples found for csi.rx.{topic}")
    return samples


def closest_unused(
    target_ns: int,
    samples: List[Sample],
    timestamps: List[int],
    used: set,
    tolerance_ns: int,
) -> Optional[int]:
    position = bisect_left(timestamps, target_ns)
    best_index = None
    best_delta = tolerance_ns + 1

    # Search around the insertion point. Used entries may create a small hole,
    # so inspect several neighbors on both sides.
    for index in range(max(0, position - 8), min(len(samples), position + 9)):
        if index in used:
            continue
        delta = abs(samples[index].rx_system_ns - target_ns)
        if delta < best_delta:
            best_index = index
            best_delta = delta

    return best_index if best_delta <= tolerance_ns else None


def match_experiment(exp_name: str, args) -> str:
    if len(args.topics) != len(args.nic_ids):
        raise ValueError("--topics and --nic-ids must contain the same number of values")
    if args.reference_topic not in args.topics:
        raise ValueError("--reference-topic must be listed in --topics")

    streams: Dict[str, List[Sample]] = {
        topic: load_samples(args.db_root, exp_name, topic)
        for topic in args.topics
    }
    reference = streams[args.reference_topic]
    tolerance_ns = int(args.tolerance_us * 1000)
    timestamps = {
        topic: [sample.rx_system_ns for sample in samples]
        for topic, samples in streams.items()
    }
    used = {topic: set() for topic in args.topics}

    header = ["index", "reference_topic", "reference_rx_system_ns"]
    for topic in args.topics:
        header.extend([
            f"topic{topic}_nic_id",
            f"topic{topic}_pci",
            f"topic{topic}_rx_seq",
            f"topic{topic}_rx_system_ns",
            f"topic{topic}_delta_ns",
            f"topic{topic}_array_saved",
        ])

    rows = []
    matched_counts = {topic: 0 for topic in args.topics}
    for row_index, reference_sample in enumerate(reference):
        row = {
            "index": row_index,
            "reference_topic": args.reference_topic,
            "reference_rx_system_ns": reference_sample.rx_system_ns,
        }
        for topic in args.topics:
            if topic == args.reference_topic:
                sample = reference_sample
                delta_ns = 0
            else:
                match_index = closest_unused(
                    reference_sample.rx_system_ns,
                    streams[topic],
                    timestamps[topic],
                    used[topic],
                    tolerance_ns,
                )
                if match_index is None:
                    sample = None
                    delta_ns = ""
                else:
                    used[topic].add(match_index)
                    sample = streams[topic][match_index]
                    delta_ns = sample.rx_system_ns - reference_sample.rx_system_ns

            if sample is None:
                row.update({
                    f"topic{topic}_nic_id": "",
                    f"topic{topic}_pci": "",
                    f"topic{topic}_rx_seq": "",
                    f"topic{topic}_rx_system_ns": "",
                    f"topic{topic}_delta_ns": "",
                    f"topic{topic}_array_saved": "",
                })
            else:
                matched_counts[topic] += 1
                row.update({
                    f"topic{topic}_nic_id": sample.nic_id,
                    f"topic{topic}_pci": sample.pci,
                    f"topic{topic}_rx_seq": sample.rx_seq,
                    f"topic{topic}_rx_system_ns": sample.rx_system_ns,
                    f"topic{topic}_delta_ns": delta_ns,
                    f"topic{topic}_array_saved": sample.array_saved,
                })
        rows.append(row)

    output_dir = os.path.join(args.intermediates_root, exp_name, "matched_csi")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{exp_name}_matched.csv")
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

    summary = ", ".join(
        f"csi.rx.{topic}={matched_counts[topic]}/{len(reference)}"
        for topic in args.topics
    )
    print(f"Matched {exp_name}: {summary}")
    print(f"Saved: {output_path}")
    return output_path


def main(args) -> None:
    if not args.exp_names:
        raise ValueError("--exp-names is required")
    for exp_name in discover_experiments(args.db_root, args.exp_names):
        match_experiment(exp_name, args)


if __name__ == "__main__":
    parser = build_base_parser()
    parser.add_argument(
        "--reference-topic",
        default="1",
        help="Topic suffix used as the output timeline",
    )
    parser.add_argument(
        "--tolerance-us",
        type=float,
        default=750.0,
        help="Maximum host receive-time difference for matching frames",
    )
    main(parser.parse_args())
