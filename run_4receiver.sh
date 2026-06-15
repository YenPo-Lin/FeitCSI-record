#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FEITCSI_BIN="${FEITCSI_BIN:-$ROOT/third_party/FeitCSI/bin/app}"
DEFAULT_PYTHON="/home/tonic/miniconda3/envs/ax210test/bin/python"
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"

MODE=5
BRIDGE_ARGS=()

usage() {
    cat <<'EOF'
Usage:
  sudo -E ./run_4receiver.sh [--mode 5|6] [bridge options]

Modes:
  5  control 5520 MHz, center 5570 MHz, bandwidth 160 MHz (default)
  6  control 5955 MHz, center 6025 MHz, bandwidth 160 MHz

Examples:
  sudo -E ./run_4receiver.sh
  sudo -E ./run_4receiver.sh --mode 5
  sudo -E ./run_4receiver.sh --mode 6
EOF
}

while (($#)); do
    case "$1" in
        --mode)
            if [[ $# -lt 2 ]]; then
                echo "Missing value for --mode"
                exit 2
            fi
            MODE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            BRIDGE_ARGS+=("$1")
            shift
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

if [[ $EUID -ne 0 ]]; then
    echo "FeitCSI must configure monitor interfaces as root."
    echo "Run: sudo -E $0"
    exit 1
fi

if [[ ! -x "$FEITCSI_BIN" ]]; then
    echo "FeitCSI binary not found: $FEITCSI_BIN"
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python environment not found: $PYTHON_BIN"
    exit 1
fi

rfkill unblock wlan
sleep 1

if rfkill list wlan | grep -q 'Hard blocked: yes'; then
    echo "A Wi-Fi device is hard-blocked."
    rfkill list wlan
    exit 1
fi

if [[ "$(find /sys/kernel/debug/iwlwifi -path '*/iwlmvm/csi_enabled' 2>/dev/null | wc -l)" -lt 4 ]]; then
    echo "The active iwlwifi module does not expose FeitCSI csi_enabled controls."
    exit 1
fi

pids=()
bridge_cards=()
cleanup() {
    trap - EXIT INT TERM
    "$PYTHON_BIN" - <<'PY' 2>/dev/null || true
import socket

for port in range(8008, 8012):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"stop", ("127.0.0.1", port))
    sock.close()
PY
    sleep 1
    for pid in "${pids[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

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

for spec in \
    "51:0000:07:00.0:8008:csi.rx.1" \
    "52:0000:08:00.0:8009:csi.rx.2" \
    "53:0000:09:00.0:8010:csi.rx.3" \
    "54:0000:0a:00.0:8011:csi.rx.4"
do
    IFS=: read -r nic domain bus slot_func port topic <<< "$spec"
    pci="$domain:$bus:$slot_func"
    if ! phy="$(resolve_phy "$pci")"; then
        echo "Cannot find a Wi-Fi PHY for NIC=$nic PCI=$pci"
        exit 1
    fi
    echo "[FeitCSI] Resolved NIC=$nic PCI=$pci -> phy$phy"
    "$FEITCSI_BIN" --phy "$phy" --udp-socket --udp-port "$port" &
    pids+=("$!")
    bridge_cards+=(--card "$nic:$phy:$port:$topic")
done

sleep 1
for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "A FeitCSI process exited during startup."
        exit 1
    fi
done

echo "[FeitCSI] Four-card publisher is running on tcp://0.0.0.0:5556"
echo "[FeitCSI] Mode=${MODE}GHz control=${FREQUENCY}MHz center=${CENTER_FREQUENCY}MHz BW=${BANDWIDTH}MHz"
echo "[FeitCSI] Keep this terminal open. Press Ctrl+C only when capture is finished."
"$PYTHON_BIN" "$ROOT/feitcsi_integration/feitcsi_bridge.py" \
    "${bridge_cards[@]}" \
    --frequency "$FREQUENCY" \
    --center-frequency "$CENTER_FREQUENCY" \
    --bandwidth "$BANDWIDTH" \
    "${BRIDGE_ARGS[@]}"
