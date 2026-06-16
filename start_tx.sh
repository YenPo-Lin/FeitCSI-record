#!/usr/bin/env bash
# FeitCSI transmitter launcher for same-machine TX/RX.
# TX uses the motherboard AX210. RX can run separately with run_4receiver.sh.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FEITCSI_BIN="${FEITCSI_BIN:-$ROOT/third_party/FeitCSI/bin/app}"

PCI="0000:0b:00.0"
MODE=5
FREQUENCY=5520
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
  sudo -E ./start_tx.sh [options]

Same-machine FeitCSI TX:
  TX  = motherboard AX210, default PCI 0000:0b:00.0
  RX  = four PCIe-switch AX210 cards via ./run_4receiver.sh

Options:
  --mode 5|6          5 GHz or 6 GHz preset (default: 5)
  --pci PCI_ADDR      TX PCI address (default: 0000:0b:00.0)
  --frequency MHz     Primary/control frequency override
  --bandwidth MHz     Channel width: 20, 40, 80, or 160 (default: 160)
  --delay USEC        Delay between packets, accepts 5000 or 5e3 (default: 5000)
  --repeat COUNT      Number of packets, accepts 1000000 or 1e6 (default: 1000000)
  --mcs INDEX         HE MCS index 0-11 (default: 5)
  --sts COUNT         Spatial streams: 1 or 2 (default: 2)
  --tx-power DBM      TX power 1-22 dBm (default: 10)
  --antenna VALUE     1, 2, or 12 for both (default: 12)
  --mac ADDRESS       Transmitter MAC
  --verbose           Enable FeitCSI verbose logging
  -h, --help          Show this help

Examples:
  sudo -E ./start_tx.sh
  sudo -E ./start_tx.sh --delay 1e5 --sts 1 --antenna 1
  sudo -E ./start_tx.sh --mode 6
EOF
}

require_value() {
    if [[ $# -lt 2 ]]; then
        echo "Missing value for $1"
        exit 2
    fi
}

to_int() {
    awk -v value="$1" 'BEGIN {
        out = value + 0
        if (out <= 0) exit 1
        printf "%.0f", out
    }'
}

while (($#)); do
    case "$1" in
        --mode)
            require_value "$@"
            MODE="$2"
            shift 2
            ;;
        --pci)
            require_value "$@"
            PCI="$2"
            shift 2
            ;;
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
            DELAY_US="$(to_int "$2")"
            shift 2
            ;;
        --repeat)
            require_value "$@"
            REPEAT="$(to_int "$2")"
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

case "$MODE" in
    5)
        : "${FREQUENCY:=5520}"
        FREQUENCY="${FREQUENCY:-5520}"
        ;;
    6)
        if [[ "$FREQUENCY" == "5520" ]]; then
            FREQUENCY=5955
        fi
        ;;
    *)
        echo "--mode must be 5 or 6"
        exit 2
        ;;
esac

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
    echo "TX AX210 not found at PCI $PCI"
    exit 1
fi

if ! iw phy "phy$phy" channels 2>/dev/null | awk -v freq="$FREQUENCY" -v bw="$BANDWIDTH" '
    $2 == freq && $3 == "MHz" { in_channel = 1; next }
    in_channel && /Channel widths:/ {
        if (bw == 20 && $0 ~ /20MHz/) found = 1
        if (bw == 40 && ($0 ~ /HT40/ || $0 ~ /40MHz/)) found = 1
        if (bw == 80 && ($0 ~ /VHT80/ || $0 ~ /80MHz/)) found = 1
        if (bw == 160 && ($0 ~ /VHT160/ || $0 ~ /160MHz/)) found = 1
        exit
    }
    END { exit found ? 0 : 1 }
'; then
    echo "TX phy$phy does not support ${BANDWIDTH} MHz at ${FREQUENCY} MHz."
    echo "Check with: iw phy phy$phy channels"
    if [[ "$MODE" == "6" ]]; then
        echo "Your current 6 GHz channels appear to support 20 MHz only; try: sudo -E ./start_tx.sh --mode 6 --bandwidth 20"
    fi
    exit 1
fi

rfkill unblock wlan
if rfkill list wlan | grep -q 'Hard blocked: yes'; then
    echo "A Wi-Fi radio is hard-blocked."
    rfkill list wlan
    exit 1
fi

packet_rate="$(awk -v delay="$DELAY_US" 'BEGIN {printf "%.1f", 1000000 / delay}')"
echo "======================= FeitCSI Same-machine TX ======================="
echo "PCI/PHY:                          $PCI / phy$phy"
echo "Frequency | BW:                   $FREQUENCY MHz | $BANDWIDTH MHz"
echo "Format | Coding | LTF:            $FORMAT | $CODING | $LTF"
echo "MCS/STS | Antenna:                $MCS/$STS | $ANTENNA"
echo "Delay | Packet rate:              $DELAY_US us | $packet_rate packets/s"
echo "Repeat | TX power:                $REPEAT | $TX_POWER dBm"
echo "MAC:                              $MAC"
echo "======================================================================="
echo "Start RX in another terminal with: sudo -E ./run_4receiver.sh --mode $MODE"
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
