#!/usr/bin/env bash
# Show FeitCSI GUI amplitude/phase plots for RX AX210 cards.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FEITCSI_BIN="${FEITCSI_BIN:-$ROOT/third_party/FeitCSI/bin/app}"

MODE=5
FREQUENCY=""
BANDWIDTH=""
FORMAT="HESU"
TARGET_NIC="all"
TARGET_PCI=""
FORCE=0

# Physical ULA order confirmed by manual antenna unplug test:
#   NIC 51 -> Rx1/Rx2, NIC 53 -> Rx3/Rx4, NIC 54 -> Rx5/Rx6, NIC 52 -> Rx7/Rx8
RX_CARDS=(
    "51:0000:07:00.0:Rx1/Rx2"
    "53:0000:09:00.0:Rx3/Rx4"
    "54:0000:0a:00.0:Rx5/Rx6"
    "52:0000:08:00.0:Rx7/Rx8"
)

usage() {
    cat <<'EOF'
Usage:
  sudo -E ./show_csi.sh [options]

Show FeitCSI GUI amplitude/phase plots. Stop run_4receiver.sh before using this.

Options:
  --nic 51|52|53|54|all  RX card to show (default: all)
  --pci PCI_ADDR         Show one card by PCI address
  --mode 5|6             5 GHz or 6 GHz preset (default: 5)
  --frequency MHz        Override control frequency
  --bandwidth MHz        Channel width: 20, 40, 80, or 160
  --format FORMAT        FeitCSI frame format (default: HESU)
  --force                Run even if receiver/bridge processes are active
  -h, --help             Show this help

Examples:
  sudo -E ./show_csi.sh
  sudo -E ./show_csi.sh --nic 51
  sudo -E ./show_csi.sh --pci 0000:07:00.0
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
            TARGET_NIC="$2"
            shift 2
            ;;
        --pci)
            require_value "$@"
            TARGET_PCI="$2"
            shift 2
            ;;
        --mode)
            require_value "$@"
            MODE="$2"
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
        --format)
            require_value "$@"
            FORMAT="$2"
            shift 2
            ;;
        --force)
            FORCE=1
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
        FREQUENCY="${FREQUENCY:-5520}"
        BANDWIDTH="${BANDWIDTH:-160}"
        ;;
    6)
        FREQUENCY="${FREQUENCY:-5955}"
        BANDWIDTH="${BANDWIDTH:-160}"
        ;;
    *)
        echo "--mode must be 5 or 6"
        exit 2
        ;;
esac

case "$BANDWIDTH" in
    20|40|80|160) ;;
    *)
        echo "--bandwidth must be 20, 40, 80, or 160"
        exit 2
        ;;
esac

if [[ $EUID -ne 0 ]]; then
    echo "FeitCSI GUI needs root to configure monitor interfaces."
    echo "Run: sudo -E $0 $*"
    exit 1
fi

if [[ ! -x "$FEITCSI_BIN" ]]; then
    echo "FeitCSI binary not found: $FEITCSI_BIN"
    exit 1
fi

if ((FORCE == 0)); then
    if pgrep -f 'run_4receiver.sh|feitcsi_bridge.py|FeitCSI/bin/app .*--udp-socket' >/dev/null; then
        echo "A receiver/bridge process appears to be running."
        echo "Stop run_4receiver.sh before using GUI, or rerun with --force."
        exit 1
    fi
fi

if [[ -z "${DISPLAY:-}" ]]; then
    echo "DISPLAY is empty. Run this from a graphical terminal with sudo -E."
    exit 1
fi

if [[ -n "${SUDO_USER:-}" && -z "${XAUTHORITY:-}" && -f "/home/$SUDO_USER/.Xauthority" ]]; then
    export XAUTHORITY="/home/$SUDO_USER/.Xauthority"
fi
export GDK_BACKEND="${GDK_BACKEND:-x11}"
export GTK_IM_MODULE="${GTK_IM_MODULE:-xim}"
export XDG_RUNTIME_DIR="/tmp/feitcsi-root-runtime"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

rfkill unblock wlan
if rfkill list wlan | grep -q 'Hard blocked: yes'; then
    echo "A Wi-Fi device is hard-blocked."
    rfkill list wlan
    exit 1
fi

resolve_phy() {
    local pci="$1"
    local phy_path
    for phy_path in /sys/class/ieee80211/phy*; do
        [[ -e "$phy_path" ]] || continue
        if [[ "$(basename "$(readlink -f "$phy_path/device")")" == "$pci" ]]; then
            basename "$phy_path" | sed 's/^phy//'
            return 0
        fi
    done
    return 1
}

run_gui() {
    local nic="$1"
    local pci="$2"
    local label="$3"
    local phy

    if ! phy="$(resolve_phy "$pci")"; then
        echo "Cannot find Wi-Fi PHY for NIC=$nic PCI=$pci"
        return 1
    fi

    echo "[FeitCSI GUI] NIC=$nic $label PCI=$pci -> phy$phy"
    "$FEITCSI_BIN" \
        --phy "$phy" \
        --mode measure \
        --frequency "$FREQUENCY" \
        --channel-width "$BANDWIDTH" \
        --format "$FORMAT" \
        --plot
}

selected_cards=()
if [[ -n "$TARGET_PCI" ]]; then
    selected_cards=("PCI:$TARGET_PCI:manual")
elif [[ "$TARGET_NIC" == "all" ]]; then
    selected_cards=("${RX_CARDS[@]}")
else
    found=0
    for spec in "${RX_CARDS[@]}"; do
        IFS=: read -r nic domain bus slot_func label <<< "$spec"
        if [[ "$nic" == "$TARGET_NIC" ]]; then
            selected_cards+=("$spec")
            found=1
            break
        fi
    done
    if ((found == 0)); then
        echo "Unknown --nic value: $TARGET_NIC"
        usage
        exit 2
    fi
fi

echo "[FeitCSI GUI] mode=${MODE}GHz frequency=${FREQUENCY}MHz BW=${BANDWIDTH}MHz format=$FORMAT"
echo "[FeitCSI GUI] If GUI permission fails, run once as user: xhost +SI:localuser:root"

pids=()
cleanup() {
    trap - EXIT INT TERM
    for pid in "${pids[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for spec in "${selected_cards[@]}"; do
    IFS=: read -r nic domain bus slot_func label <<< "$spec"
    pci="$domain:$bus:$slot_func"
    if ((${#selected_cards[@]} == 1)); then
        run_gui "$nic" "$pci" "$label"
    else
        run_gui "$nic" "$pci" "$label" &
        pids+=("$!")
        sleep 0.5
    fi
done

if ((${#pids[@]})); then
    wait
fi
