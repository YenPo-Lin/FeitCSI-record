import os
import json
import argparse
import csv
from typing import Dict, Any, List
import pandas as pd
import glob

from PicoSense_common_args import build_base_parser

def basename_npy(path: str) -> str:
	if not path:
		return path
	return os.path.basename(path)


def write_matched_csv(out_path: str, exp_name: str, rows: List[List[Any]], header: List[str]):
	out_fn = os.path.join(out_path, f"{exp_name}_matched.csv")
	with open(out_fn, 'w', newline='', encoding='utf-8') as f:
		writer = csv.writer(f)
		writer.writerow(header)
		writer.writerows(rows)
	print(f"Wrote matched CSV: {out_fn}")

def match_NICs(ordered_keys, nic_maps, args, header):
	rows: List[List[Any]] = []
	idx = 0
	# iterate through ordered keys and fill rows, inserting phantom rows for numeric gaps
	MAX_SEQ = 4095
	prev_seq = None
	for key in ordered_keys:
		seq, task = key

		# if we have a previous numeric seq, detect numeric gaps (consider wrap at MAX_SEQ)
		if prev_seq is not None and seq is not None:
			# compute gap count between prev_seq and seq
			if seq == prev_seq:
				gap = 0
			elif seq > prev_seq:
				gap = seq - prev_seq - 1
			else:
				# wrapped around case: e.g., prev_seq=9998, seq=1 -> missing 9999,0
				gap = (MAX_SEQ - prev_seq) + seq

			# insert phantom rows for each missing numeric sequence
			for k in range(1, gap + 1):
				missing_seq = (prev_seq + k) % (MAX_SEQ + 1)
				phantom_row = [None] * len(header)
				phantom_row[0] = idx
				phantom_row[1] = -1  # time unknown
				phantom_row[2] = missing_seq
				phantom_row[3] = -1  # task unknown
				# all NIC-specific fields are -1 for phantom
				for i in range(len(args.nic_ids)):
					col_idx = 4 + i * 2
					phantom_row[col_idx] = -1
					phantom_row[col_idx + 1] = -1
				rows.append(phantom_row)
				idx += 1

		# now the real row for current key
		per_row = [None] * len(header)
		per_row[0] = idx
		# time: NIC1 rx_system_ns if present else -1
		nic1_sample = nic_maps.get(args.nic_ids[0], {}).get(key)
		per_row[1] = nic1_sample.get('rx_system_ns', -1) if nic1_sample else -1

		# packet_seq and packet_taskId
		per_row[2] = seq
		if nic1_sample and nic1_sample.get('packet_taskId') is not None:
			per_row[3] = nic1_sample.get('packet_taskId')
		else:
			per_row[3] = task if task is not None else -1

		# NIC-specific columns using tuple-key lookup
		for i, nic in enumerate(args.nic_ids):
			col_idx = 4 + i * 2
			sample = nic_maps.get(nic, {}).get(key)
			if sample:
				per_row[col_idx] = sample.get('line_index', -1)
				per_row[col_idx + 1] = sample.get('array_saved', -1)
			else:
				per_row[col_idx] = -1
				per_row[col_idx + 1] = -1

		rows.append(per_row)
		idx += 1
		prev_seq = seq
	return rows

def inter_extrapolate_rx1_system_ns(rows: List[List[Any]]) -> List[List[Any]]:
	# Fill missing NIC1 receive times by linear interpolation/extrapolation across the
	# assembled `rows` list. We treat values `None` and `-1` as missing.
	time_col = 1
	known = []  # list of (row_index, time)
	for r_index, row in enumerate(rows):
		t = row[time_col]
		if t is not None and t != -1:
			try:
				known.append((r_index, int(t)))
			except Exception:
				pass

	if known:
		# If only one known time, propagate it to all missing entries
		if len(known) == 1:
			i0, t0 = known[0]
			for row in rows:
				if row[time_col] is None or row[time_col] == -1:
					row[time_col] = int(t0)
		else:
			# Interpolate between known points
			for k in range(len(known) - 1):
				i0, t0 = known[k]
				i1, t1 = known[k + 1]
				if i1 - i0 <= 1:
					continue
				for j in range(i0 + 1, i1):
					frac = (j - i0) / (i1 - i0)
					rows[j][time_col] = int(round(t0 + frac * (t1 - t0)))

			# Extrapolate backward before the first known
			i_first, t_first = known[0]
			if i_first > 0:
				# slope from first interval
				i_next, t_next = known[1]
				slope = (t_next - t_first) / (i_next - i_first) if (i_next - i_first) != 0 else 0
				for j in range(i_first - 1, -1, -1):
					rows[j][time_col] = int(round(t_first - slope * (i_first - j)))

			# Extrapolate forward after the last known
			i_last, t_last = known[-1]
			if i_last < len(rows) - 1:
				i_prev, t_prev = known[-2]
				slope = (t_last - t_prev) / (i_last - i_prev) if (i_last - i_prev) != 0 else 0
				for j in range(i_last + 1, len(rows)):
					rows[j][time_col] = int(round(t_last + slope * (j - i_last)))
	return rows

def main(args):
	# Process each exp_name separately and produce one CSV per exp
	if len(args.exp_names) == 1 and "all" in args.exp_names[0]:
		date = args.exp_names[0].split("-")[0]
		# list all experiment folders start with date in db_root
		args.exp_names = []
		for entry in os.listdir(args.db_root):
			full_path = os.path.join(args.db_root, entry)
			if os.path.isdir(full_path) and entry.startswith(date):
				args.exp_names.append(entry)
		args.exp_names.sort()

	for exp_name in args.exp_names:
		print(f"Processing exp: {exp_name}")

		matched_path = os.path.join(args.intermediates_root, exp_name, "matched_csi")
		os.makedirs(matched_path, exist_ok=True)

		# Build per-NIC maps keyed by (packet_seq, packet_taskId)
		nic_maps: Dict[str, Dict[Any, Dict[str, Any]]] = {}
		for nic in args.nic_ids:
			# list the files in the NIC folder
			fns = glob.glob(os.path.join(args.db_root, exp_name, f"csi.rx.{nic}", f"*.csv"))
			# check if there is one csv file for this exp
			if len(fns) == 0:
				raise ValueError(f"  Error: No CSI CSV for NIC {nic} for experiment {exp_name} found")
			
			# Concatenate all CSV files for this NIC and experiment
			df = pd.concat([pd.read_csv(fn, header=0, engine='c') for fn in fns], ignore_index=True)

			# Read file into DataFrame so we can robustly access columns and row indices
			# df = pd.read_csv(fns[0], header=0, engine='c')

			nic_maps[nic] = {}
			# iterate rows and build mapping; use the row index as line_index
			for line_index, row in df.iterrows():
				# tolerant column access; missing values -> None
				rx_system_ns = row.get('rx_system_ns') if 'rx_system_ns' in row else None
				packet_seq = row.get('packet_seq') if 'packet_seq' in row else None
				packet_taskId = row.get('packet_taskId') if 'packet_taskId' in row else None
				array_saved = basename_npy(row.get('array_saved')) if 'array_saved' in row else None

				# convert NaN to None and attempt integer conversion for seq/task
				if pd.isna(rx_system_ns):
					rx_system_ns = None
				if pd.isna(packet_seq):
					packet_seq = None
				else:
					try:
						packet_seq = int(packet_seq)
					except Exception:
						packet_seq = None
				if pd.isna(packet_taskId):
					packet_taskId = None
				else:
					try:
						packet_taskId = int(packet_taskId)
					except Exception:
						packet_taskId = None

				key = (packet_seq, packet_taskId)
				sample = {
					'rx_system_ns': int(rx_system_ns) if rx_system_ns is not None else None,
					'packet_seq': packet_seq,
					'packet_taskId': packet_taskId,
					'array_saved': array_saved,
					'line_index': int(line_index)
				}

				# If duplicate keys exist, keep the sample with the earliest rx_system_ns when available
				existing = nic_maps[nic].get(key)
				if existing is None:
					nic_maps[nic][key] = sample
				else:
					# prefer non-None/earlier timestamp
					ex_ts = existing.get('rx_system_ns')
					new_ts = sample.get('rx_system_ns')
					if ex_ts is None and new_ts is not None:
						nic_maps[nic][key] = sample
					elif ex_ts is not None and new_ts is not None and new_ts < ex_ts:
						nic_maps[nic][key] = sample

		# collect global set of (packet_seq, packet_taskId) keys across all NICs
		all_keys = set()
		for m in nic_maps.values():
			all_keys.update(m.keys())

		if not all_keys:
			print(f"  No sequences found for {exp_name} in any NIC — skipping.")
			continue

		# We need a stable ordering. Prefer ordering by NIC1 rx_system_ns when available.
		# Build a list of keys and associated NIC1 time if present.
		keys_with_time = []  # list of (key, time_or_None)
		for key in all_keys:
			# key is (packet_seq, packet_taskId)
			# collect rx_system_ns from any NIC that has this key
			time_candidates = []
			for m in nic_maps.values():
				sample = m.get(key)
				if sample:
					t = sample.get('rx_system_ns')
					if t is not None:
						time_candidates.append(t)
			# prefer the earliest observed time across NICs (this places packets seen earlier on any NIC first)
			time = min(time_candidates) if time_candidates else None
			keys_with_time.append((key, time))

		# sort: keys with NIC1 time first by time, then remaining by packet_seq, packet_taskId
		keys_with_time.sort(key=lambda kt: (kt[1] is None, kt[1] if kt[1] is not None else kt[0]))
		ordered_keys = [kt[0] for kt in keys_with_time]

		# dynamic header depending on number of NICs
		header = ['index', f'time_nic{args.nic_ids[0]}_rx_system_ns', 'packet_seq', 'packet_taskId']
		for nic in args.nic_ids:
			header.append(f'nic{nic}_line_index')
			header.append(f'nic{nic}_array_saved')

		# match CSI samples across NICs
		rows = match_NICs(ordered_keys, nic_maps, args, header)

		# If there are missing time_nic1_rx_system_ns, fill them by interpolation and extrapolation
		rows = inter_extrapolate_rx1_system_ns(rows)

		# write CSV for this exp group
		write_matched_csv(matched_path, exp_name, rows, header)


if __name__ == "__main__":
	parser = build_base_parser()
	args = parser.parse_args()

	main(args)

	#python3 csi_matcher.py --db-root ./db --intermediates-root ./intermediates --exp-names 20260115_1u_ss
	#python3 csi_matcher.py --db-root ./db --intermediates-root ./intermediates --exp-names 20260115_2u_fs_dia