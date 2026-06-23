#!/usr/bin/env python3
"""Python wrapper for same-machine FeitCSI TX."""

import argparse
import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Optional


DEFAULT_MODE = 5
DEFAULT_DELAY = 10000            # Packet rate = 1e6 / delay
DEFAULT_BW = 160
DEFAULT_REPEAT = 1_000_000
DEFAULT_MCS = 5
DEFAULT_STS = 2                   # 2 spatial streams
DEFAULT_TX_POWER = 10
DEFAULT_ANTENNA = "12"            # TX 使用 antenna 1 + antenna 2
RX_PCIS = {"0000:07:00.0", "0000:08:00.0", "0000:09:00.0", "0000:0a:00.0"}
ONBOARD_WIFI_PCIS = {"0000:00:14.3"}


@dataclass(frozen=True)
class WifiRadio:
    phy: str
    pci: str
    mac: str


def positive_int(value: str) -> int:
    parsed = int(float(value))
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def read_first_netdev_mac(phy_path: pathlib.Path) -> Optional[str]:
    net_dir = phy_path / "device" / "net"
    if not net_dir.exists():
        return None
    for iface in sorted(net_dir.iterdir(), key=lambda p: p.name):
        address = iface / "address"
        if address.exists():
            mac = address.read_text(encoding="utf-8").strip().lower()
            if mac:
                return mac
    return None


def scan_wifi_radios() -> list[WifiRadio]:
    radios: list[WifiRadio] = []
    for phy_path in sorted(pathlib.Path("/sys/class/ieee80211").glob("phy*")):
        device = (phy_path / "device").resolve()
        pci = device.name
        mac = read_first_netdev_mac(phy_path)
        if mac:
            radios.append(WifiRadio(phy=phy_path.name, pci=pci, mac=mac))
    return radios


def resolve_tx_radio(pci_override: Optional[str] = None) -> WifiRadio:
    radios = scan_wifi_radios()
    if pci_override:
        for radio in radios:
            if radio.pci == pci_override:
                return radio
        raise RuntimeError(f"TX AX210 not found at PCI {pci_override}")

    candidates = [
        radio for radio in radios
        if radio.pci not in RX_PCIS and radio.pci not in ONBOARD_WIFI_PCIS
    ]
    if len(candidates) == 1:
        return candidates[0]

    details = ", ".join(f"{r.phy}:{r.pci}:{r.mac}" for r in radios) or "none"
    if not candidates:
        raise RuntimeError(f"Cannot find TX AX210 automatically. Radios: {details}")
    raise RuntimeError(
        "Multiple possible TX AX210 cards found. "
        f"Use --pci to choose one. Radios: {details}"
    )


def transmitter(args: argparse.Namespace) -> int:
    root = pathlib.Path(__file__).resolve().parent

    launcher = root / "start_tx.sh"
    try:
        tx_radio = resolve_tx_radio(args.pci)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    packet_rate = 1e6 / args.delay

    print("========== FeitCSI Same-machine TX ==========")
    print(f"{'PHY:':<10}{tx_radio.phy}")
    print(f"{'PCI:':<10}{tx_radio.pci}")
    print(f"{'Mode:':<10}{args.mode} GHz preset")
    print(f"{'BW:':<10}{args.bandwidth} MHz")
    print(f"{'MCS/STS:':<10}{args.mcs}/{args.sts}")
    print(f"{'Antenna:':<10}{args.antenna}")
    print(f"{'Rate:':<10}{packet_rate:.1f} packets/s")
    print(f"{'Repeat:':<10}{args.repeat}")
    print("=============================================")

    command = [
        str(launcher),
        "--mode",
        str(args.mode),
        "--pci",
        tx_radio.pci,
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
        tx_radio.mac,
    ]
    if args.frequency is not None:
        command.extend(["--frequency", str(args.frequency)])
    if args.verbose:
        command.append("--verbose")
    if os.geteuid() != 0:
        command = ["sudo", "-E", *command]

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
    parser.add_argument("--pci", default=None, help="Override TX PCI address")
    parser.add_argument("--frequency", type=int, default=None)
    parser.add_argument("--bandwidth", type=int, default=DEFAULT_BW)
    parser.add_argument("--delay", type=positive_int, default=positive_int(str(DEFAULT_DELAY)))
    parser.add_argument("--repeat", type=positive_int, default=DEFAULT_REPEAT)
    parser.add_argument("--mcs", type=int, default=DEFAULT_MCS)
    parser.add_argument("--sts", type=int, choices=[1, 2], default=DEFAULT_STS)
    parser.add_argument("--tx-power", type=int, default=DEFAULT_TX_POWER)
    parser.add_argument("--antenna", choices=["1", "2", "12"], default=DEFAULT_ANTENNA)
    parser.add_argument("--verbose", action="store_true")

    raise SystemExit(transmitter(parser.parse_args()))
