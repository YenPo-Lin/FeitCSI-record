#!/usr/bin/env python3
"""Python wrapper for same-machine FeitCSI TX."""

import argparse
import pathlib
import subprocess


DEFAULT_MODE = 5
DEFAULT_PCI = "0000:0b:00.0"
DEFAULT_DELAY = 1e4
DEFAULT_BW = 160
DEFAULT_REPEAT = 1_000_000
DEFAULT_MCS = 5
DEFAULT_STS = 2
DEFAULT_TX_POWER = 10
DEFAULT_ANTENNA = "12"
DEFAULT_MAC = "70:d8:23:17:7e:38"


def positive_int(value: str) -> int:
    parsed = int(float(value))
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def transmitter(args: argparse.Namespace) -> int:
    root = pathlib.Path(__file__).resolve().parent
    launcher = root / "start_tx.sh"
    packet_rate = 1e6 / args.delay

    print("========== FeitCSI Same-machine TX ==========")
    print(f"PCI:      {args.pci}")
    print(f"Mode:     {args.mode} GHz preset")
    print(f"BW:       {args.bandwidth} MHz")
    print(f"MCS/STS:  {args.mcs}/{args.sts}")
    print(f"Antenna:  {args.antenna}")
    print(f"Rate:     {packet_rate:.1f} packets/s")
    print(f"Repeat:   {args.repeat}")
    print("=============================================")

    command = [
        "sudo",
        "-E",
        str(launcher),
        "--mode",
        str(args.mode),
        "--pci",
        args.pci,
        "--bandwidth",
        str(args.bandwidth),
        "--delay",
        str(args.delay),
        "--repeat",
        str(args.repeat),
        "--mcs",
        str(args.mcs),
        "--sts",
        str(args.sts),
        "--tx-power",
        str(args.tx_power),
        "--antenna",
        args.antenna,
        "--mac",
        args.mac,
    ]
    if args.frequency is not None:
        command.extend(["--frequency", str(args.frequency)])
    if args.verbose:
        command.append("--verbose")

    try:
        return subprocess.call(command)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt: Stop the transmitter")
        return 130


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FeitCSI TX launcher for same-machine AX210 capture"
    )
    parser.add_argument("--mode", type=int, choices=[5, 6], default=DEFAULT_MODE)
    parser.add_argument("--pci", default=DEFAULT_PCI)
    parser.add_argument("--frequency", type=int, default=None)
    parser.add_argument("--bandwidth", type=int, default=DEFAULT_BW)
    parser.add_argument("--delay", type=positive_int, default=positive_int(str(DEFAULT_DELAY)))
    parser.add_argument("--repeat", type=positive_int, default=DEFAULT_REPEAT)
    parser.add_argument("--mcs", type=int, default=DEFAULT_MCS)
    parser.add_argument("--sts", type=int, choices=[1, 2], default=DEFAULT_STS)
    parser.add_argument("--tx-power", type=int, default=DEFAULT_TX_POWER)
    parser.add_argument("--antenna", choices=["1", "2", "12"], default=DEFAULT_ANTENNA)
    parser.add_argument("--mac", default=DEFAULT_MAC)
    parser.add_argument("--verbose", action="store_true")

    raise SystemExit(transmitter(parser.parse_args()))
