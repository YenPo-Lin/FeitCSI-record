#!/usr/bin/env bash
# Show FeitCSI realtime amplitude/phase plot for one AX210 receiver.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FEITCSI_BIN="${FEITCSI_BIN:-$ROOT/third_party/FeitCSI/bin/app}"

MODE=5
NIC=51
PCI=""
VIEW="plot"
FORMAT="HESU"

usage() {
    cat <<'EOF'
Usage:
  sudo -E ./show_realtime_CSI.sh [options]

Default:
  Show NIC 51 at 5 GHz / 160 MHz with FeitCSI --plot.

Options:
  --nic ID          Receiver NIC ID: 51, 52, 53, or 54 (default: 51)
  --pci PCI_ADDR    Override PCI address, e.g. 0000:07:00.0
  --mode 5|6        5 GHz or 6 GHz preset (default: 5)
  --plot            Show realtime amplitude/phase plot (default)
  --gui             Open FeitCSI GUI
  --format FORMAT   FeitCSI frame format (default: HESU)
  -h, --help        Show this help

Examples:
  sudo -E ./show_realtime_CSI.sh
  sudo -E ./show_realtime_CSI.sh --nic 52
  sudo -E ./show_realtime_CSI.sh --nic 51 --mode 6
  sudo -E ./show_realtime_CSI.sh --pci 0000:07:00.0 --gui
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
        --nic)
            require_value "$@"
            NIC="$2"
            shift 2
            ;;
        --pci)
            require_value "$@"
            PCI="$2"
            shift 2
            ;;
        --mode)
            require_value "$@"
            MODE="$2"
            shift 2
            ;;
        --plot)
            VIEW="plot"
            shift
            ;;
        --gui)
            VIEW="gui"
            shift
            ;;
        --format)
            require_value "$@"
            FORMAT="$2"
            shift 2
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
        FREQUENCY=5520
        CENTER_FREQUENCY=5570
        BANDWIDTH=160
        ;;
    6)
        FREQUENCY=5955
        CENTER_FREQUENCY=6025
        BANDWIDTH=160
        ;;
    *)
        echo "--mode must be 5 or 6"
        exit 2
        ;;
esac

if [[ -z "$PCI" ]]; then
    case "$NIC" in
        51) PCI="0000:07:00.0" ;;
        52) PCI="0000:08:00.0" ;;
        53) PCI="0000:09:00.0" ;;
        54) PCI="0000:0a:00.0" ;;
        *)
            echo "--nic must be 51, 52, 53, or 54 unless --pci is provided"
            exit 2
            ;;
    esac
fi

if [[ $EUID -ne 0 ]]; then
    echo "FeitCSI must configure the receiver interface as root."
    echo "Run: sudo -E $0"
    exit 1
fi

if [[ ! -x "$FEITCSI_BIN" ]]; then
    echo "FeitCSI binary not found: $FEITCSI_BIN"
    exit 1
fi

rfkill unblock wlan
sleep 1

if rfkill list wlan | grep -q 'Hard blocked: yes'; then
    echo "A Wi-Fi device is hard-blocked."
    rfkill list wlan
    exit 1
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
    echo "Cannot find a Wi-Fi PHY for PCI $PCI"
    exit 1
fi

echo "==================== FeitCSI Realtime CSI ===================="
echo "NIC/PCI/PHY:                       $NIC / $PCI / phy$phy"
echo "Frequency | Center_Freq | BW:      $FREQUENCY MHz | $CENTER_FREQUENCY MHz | $BANDWIDTH MHz"
echo "Format:                            $FORMAT"
echo "View:                              --$VIEW"
echo "=============================================================="
echo "Close the plot/GUI or press Ctrl+C to stop."

command=(
    "$FEITCSI_BIN"
    --phy "$phy"
    --mode measure
    --frequency "$FREQUENCY"
    --channel-width "$BANDWIDTH"
    --format "$FORMAT"
)

case "$VIEW" in
    plot) command+=(--plot) ;;
    gui) command+=(--gui) ;;
esac

exec "${command[@]}"
