import os
import csv
import argparse
import numpy as np
from typing import List

from pathlib import Path
from PicoSense_common_args import build_base_parser


def load_matched_csv(csv_path: str) -> List[dict]:
	rows = []
	with open(csv_path, 'r', encoding='utf-8') as f:
		reader = csv.DictReader(f)
		for r in reader:
			rows.append(r)
	return rows


def build_array_paths(row: dict, arrays_path: str, rx_nics: List[str]) -> List[str]:
	"""Return list of full paths (or None) to npy files for each NIC in order."""
	paths = []
	for i, nic in enumerate(rx_nics):
		key = f"nic{i+1}_array_saved"
		fname = row.get(key)
		if not fname or fname == "-1":
			paths.append(None)
		else:
			candidate = os.path.join(arrays_path, f"csi.rx.{nic}", fname)
			if os.path.exists(candidate):
				paths.append(candidate)
			else:
				print(f"Warning: file not found for NIC {nic}: {fname} (expected under {os.path.join(arrays_path, 'csi.rx.' + str(nic))})")
				paths.append(None)
	return paths


def load_npy_file(path: str, expected_subcarriers: int) -> np.ndarray:
	arr = np.load(path)
	arr = np.array(arr) # (tx, rx, sub) but in column-major order

	arr = np.squeeze(arr)
	sub, tx, rx = arr.shape
	if sub != expected_subcarriers:
		print(f"Warning: subcarrier count mismatch for {path}: expected {expected_subcarriers}, got {sub}")

	# TODO:
	# !!! Fix column-major order issue
	# !!! This should not be used if we fixed the ZMQ part
	# !!! Please remember to fix the column-major issue in the data collection step
	# !!! otherwise the CSI data will be wrong
	FIX_LEGACY_ZMQ = True
	if FIX_LEGACY_ZMQ:
		arr_bad = arr
		shape = arr_bad.shape
		flat = np.ravel(arr_bad, order='F')
		arr_fix = flat.reshape(shape, order='C')
		arr = arr_fix
	# From here on, order is fixed to row-major and you should not be worried about it anymore

	# Debug plot
	# !!!
	# MAKE SURE YOU VERIFY THE LOADING IS CORRECT IF YOU MAKE ANY CHANGE IN 
	# THE PUBLISHER AND SUBSCRIBER CODE THAT MAY AFFECT THE DATA ORDERING
	# Ideally, the plotted phase and magnitude should look continuous without jumps
	# and there will be equally spacing zero values (Magnitude ~ 5)
	# the spacing is around 60 subcarriers
	# The following listed results mean the loading is incorrect
	# 1. Four similar part, each span around sub // 4 subcarriers
	# 2. Amplitude jumps significantly and cause a blur plot
	# 3. Amplitude seems like have a 4 subcarrier periodicity
	# If you see any of these, please check the whole pipeline for the data collection
	# and make sure the data ordering is correct.
	# !!!
	# print(arr.shape)
	# y = arr[:, 0, 0]
	# phase = np.angle(y)
	# # unwrapped
	# phase = np.unwrap(phase)
	# amp = np.abs(y)
	# x = np.arange(sub)
	# import matplotlib.pyplot as plt
	# plt.figure()
	# plt.subplot(2, 1, 1)
	# plt.plot(x, phase, label='Phase')
	# plt.subplot(2, 1, 2)
	# plt.plot(x, amp, label='Magnitude')
	# plt.legend()
	# plt.show()

	# transpose to (tx, rx, sub)
	arr_tr = arr.transpose(1, 2, 0)
	return arr_tr.astype(np.complex64)


def interp_timeseries(series_real: np.ndarray, series_imag: np.ndarray) -> np.ndarray:
	"""Interpolate real/imag 1D series with NaNs; fill edges by nearest value."""
	T = series_real.shape[0]
	x = np.arange(T)
	mask = ~np.isnan(series_real)
	if mask.sum() == 0:
		return np.zeros(T, dtype=np.complex64)
	if mask.sum() == 1:
		val = series_real[mask][0] + 1j * series_imag[mask][0]
		return np.full(T, val, dtype=np.complex64)
	xp = x[mask]
	fr = series_real[mask]
	fi = series_imag[mask]
	real_interp = np.interp(x, xp, fr)
	imag_interp = np.interp(x, xp, fi)
	return (real_interp + 1j * imag_interp).astype(np.complex64)


def merge_for_exp(exp_name, args):
	csv_path = os.path.join(args.intermediates_root, exp_name, "matched_csi", f"{exp_name}_matched.csv")
	array_path = os.path.join(args.artifacts_root, exp_name, "arrays")

	if args.dataset_type == "rt":
		merged_path = Path(args.intermediates_root) / exp_name / "merged_csi" / f"{exp_name}_merged.npz"
		merged_path.parent.mkdir(parents=True, exist_ok=True)
	elif args.dataset_type == "data":
		date = exp_name.split('-')[0]
		exp_basename = exp_name.split('_')[-1]
		merged_path = Path(args.data_root) / date / "csi" / f"csi_{date}-{exp_basename}.npz"
		merged_path.parent.mkdir(parents=True, exist_ok=True)

	nic_ids = args.nic_ids
	subcarriers = args.subcarriers
	antenna_order = args.antenna_order

	rows = load_matched_csv(csv_path)
	T = len(rows)
	N = len(nic_ids)
	Tx = 2
	Rx = 2
	S = subcarriers

	data_nic = [[None] * T for _ in range(N)]

	for t, row in enumerate(rows):
		paths = build_array_paths(row, array_path, nic_ids)
		for n, path in enumerate(paths):
			if path is None:
				data_nic[n][t] = None
			else:
				try:
					arr_tr = load_npy_file(path, S)
					data_nic[n][t] = arr_tr
				except Exception as e:
					print(f"Error loading {path}: {e}")
					data_nic[n][t] = None

	merged = np.zeros((T, N, Tx, Rx, S), dtype=np.complex64)

	for n in range(N):
		real = np.full((T, Tx, Rx, S), np.nan, dtype=np.float32)
		imag = np.full((T, Tx, Rx, S), np.nan, dtype=np.float32)
		for t in range(T):
			arr = data_nic[n][t]
			if arr is not None:
				real[t] = arr.real
				imag[t] = arr.imag
		for tx in range(Tx):
			for rx in range(Rx):
				for s in range(S):
					r_series = real[:, tx, rx, s]
					i_series = imag[:, tx, rx, s]
					merged_series = interp_timeseries(r_series, i_series)
					merged[:, n, tx, rx, s] = merged_series

	# Antenna order correction if needed can be done here
	merged = merged.transpose(0, 2, 1, 3, 4)
	merged = merged.reshape(T, Tx, N * Rx, S)
	antenna_order = [int(e) for e in antenna_order]
	merged = merged[:, :, antenna_order, :]

	np.savez_compressed(merged_path, csi=merged)
	print(f"Saved merged array to {merged_path} (shape {merged.shape})")

def main(args):
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
		merge_for_exp(exp_name, args)

if __name__ == '__main__':
	parser = build_base_parser()
	parser.add_argument("--dataset-type", type=str, help="Type of dataset", choices=["data", "rt"], default="data")
	args = parser.parse_args()
	
	main(args)

# python3 csi_merger.py --artifacts-root ./artifacts --intermediates-root ./intermediates --exp-names 20260115_1u_ss --dataset-type rt
# python3 csi_merger.py --artifacts-root ./artifacts --intermediates-root ./intermediates --exp-names 20260115_2u_fs_dia --dataset-type rt