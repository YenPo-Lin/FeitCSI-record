#!/usr/bin/env bash
# Transmit HE-SU frames with the motherboard-connected AX210.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FEITCSI_BIN="${FEITCSI_BIN:-$ROOT/third_party/FeitCSI/bin/app}"

PCI="0000:0b:00.0"
FREQUENCY=5520
CENTER_FREQUENCY=5570
BANDWIDTH=160
DELAY_US=5000
REPEAT=1000000
FORMAT="HESU"
CODING="LDPC"
MCS=5
STS=2
TX_POWER=10
ANTENNA=12
LTF="4xLTF+3.2"
MAC="70:d8:23:17:7e:38"
VERBOSE=0

usage() {
    cat <<'EOF'
Usage:
  sudo -E ./run_transmitter.sh [options]

Uses the motherboard-connected AX210 at PCI 0000:0b:00.0.

Options:
  --frequency MHz       Primary/control frequency (default: 5520)
  --bandwidth MHz       Channel width: 20, 40, 80, or 160 (default: 160)
  --delay USEC          Delay between packets in microseconds (default: 5000)
  --repeat COUNT        Number of packets (default: 1000000)
  --mcs INDEX           HE MCS index 0-11 (default: 5)
  --sts COUNT           Spatial streams: 1 or 2 (default: 2)
  --tx-power DBM        TX power 1-22 dBm (default: 10)
  --antenna VALUE       1, 2, or 12 for both (default: 12)
  --ltf VALUE           HE LTF/GI (default: 4xLTF+3.2)
  --mac ADDRESS         Transmitter MAC
  --verbose             Enable FeitCSI verbose logging
  -h, --help            Show this help

Example:
  sudo -E ./run_transmitter.sh --delay 2000 --mcs 5 --sts 2
EOF
}

require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1"
        exit 2
    fi
}

while (($#)); do
    case "$1" in
        --frequency)
            require_value "$@"
            FREQUENCY="$2"
            shift 2
            ;;
        --bandwidth)
            require_value "$@"
            BANDWIDTH="$2"
            shift 2
            ;;
        --delay)
            require_value "$@"
            DELAY_US="$2"
            shift 2
            ;;
        --repeat)
            require_value "$@"
            REPEAT="$2"
            shift 2
            ;;
        --mcs)
            require_value "$@"
            MCS="$2"
            shift 2
            ;;
        --sts)
            require_value "$@"
            STS="$2"
            shift 2
            ;;
        --tx-power)
            require_value "$@"
            TX_POWER="$2"
            shift 2
            ;;
        --antenna)
            require_value "$@"
            ANTENNA="$2"
            shift 2
            ;;
        --ltf)
            require_value "$@"
            LTF="$2"
            shift 2
            ;;
        --mac)
            require_value "$@"
            MAC="$2"
            shift 2
            ;;
        --verbose)
            VERBOSE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 2
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "FeitCSI must configure the TX interface as root."
    echo "Run: sudo -E $0"
    exit 1
fi

if [[ ! -x "$FEITCSI_BIN" ]]; then
    echo "FeitCSI binary not found: $FEITCSI_BIN"
    exit 1
fi

case "$BANDWIDTH" in
    20|40|80|160) ;;
    *)
        echo "--bandwidth must be 20, 40, 80, or 160"
        exit 2
        ;;
esac
if [[ ! "$DELAY_US" =~ ^[0-9]+$ ]] || ((DELAY_US <= 0)); then
    echo "--delay must be a positive integer in microseconds"
    exit 2
fi
if [[ ! "$REPEAT" =~ ^[0-9]+$ ]] || ((REPEAT <= 0)); then
    echo "--repeat must be a positive integer"
    exit 2
fi
if [[ ! "$MCS" =~ ^[0-9]+$ ]] || ((MCS < 0 || MCS > 11)); then
    echo "--mcs must be in 0..11"
    exit 2
fi
if [[ "$STS" != "1" && "$STS" != "2" ]]; then
    echo "--sts must be 1 or 2"
    exit 2
fi
if [[ ! "$TX_POWER" =~ ^[0-9]+$ ]] || ((TX_POWER < 1 || TX_POWER > 22)); then
    echo "--tx-power must be in 1..22 dBm"
    exit 2
fi
if [[ "$ANTENNA" != "1" && "$ANTENNA" != "2" && "$ANTENNA" != "12" ]]; then
    echo "--antenna must be 1, 2, or 12"
    exit 2
fi

phy=""
for phy_path in /sys/class/ieee80211/phy*; do
    [[ -e "$phy_path" ]] || continue
    if [[ "$(basename "$(readlink -f "$phy_path/device")")" == "$PCI" ]]; then
        phy="$(basename "$phy_path" | sed 's/^phy//')"
        break
    fi
done
if [[ -z "$phy" ]]; then
    echo "Motherboard AX210 not found at PCI $PCI"
    exit 1
fi

rfkill unblock wlan
if rfkill list wlan | grep -q 'Hard blocked: yes'; then
    echo "A Wi-Fi radio is hard-blocked."
    rfkill list wlan
    exit 1
fi

packet_rate="$(awk -v delay="$DELAY_US" 'BEGIN {printf "%.1f", 1000000 / delay}')"
echo "======================= FeitCSI AX210 TX ======================="
echo "PCI/PHY:                          $PCI / phy$phy"
echo "MAC:                              $MAC"
echo "Frequency | Center_Freq | BW:     $FREQUENCY MHz | $CENTER_FREQUENCY MHz | $BANDWIDTH MHz"
echo "Delay:                            $DELAY_US us"
echo "Packet rate:                      $packet_rate packets/s"
echo "TX power:                         $TX_POWER dBm"
echo "================================================================="
echo "Press Ctrl+C to stop transmission."

command=(
    "$FEITCSI_BIN"
    --phy "$phy"
    --mode inject
    --frequency "$FREQUENCY"
    --channel-width "$BANDWIDTH"
    --format "$FORMAT"
    --coding "$CODING"
    --ltf "$LTF"
    --mcs "$MCS"
    --spatial-streams "$STS"
    --antenna "$ANTENNA"
    --tx-power "$TX_POWER"
    --inject-delay "$DELAY_US"
    --inject-repeat "$REPEAT"
    --mac "$MAC"
)
if ((VERBOSE)); then
    command+=(--verbose)
fi

exec "${command[@]}"
