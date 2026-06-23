#!/usr/bin/env bash
# Reset AX210 interfaces to a clean FeitCSI-ready managed/DOWN state.

set -euo pipefail

MB_CARD="MB:0000:0b:00.0:wlp11s0"
SW_CARDS=(
    "51:0000:07:00.0:wlp7s0"
    "52:0000:08:00.0:wlp8s0"
    "53:0000:09:00.0:wlp9s0"
    "54:0000:0a:00.0:wlp10s0"
)

usage() {
    cat <<'EOF'
Usage:
  sudo -E ./setup.sh [mb|sw|all]

Modes:
  mb   reset motherboard AX210 only, for TX
  sw   reset four PCIe-switch AX210 cards only, for RX
  all  reset both motherboard TX card and four RX cards

Default:
  sw
EOF
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Root is required to reset Wi-Fi interfaces."
        echo "Run: sudo -E $0 $*"
        exit 1
    fi
}

stop_feitcsi() {
    echo "[1/4] Stopping FeitCSI processes..."
    for port in 8008 8009 8010 8011; do
        printf 'stop' >"/dev/udp/127.0.0.1/$port" 2>/dev/null || true
    done
    sleep 1
    pkill -f '/third_party/FeitCSI/bin/app' 2>/dev/null || true
    pkill -f '/feitcsi_integration/feitcsi_bridge.py' 2>/dev/null || true
}

unblock_wifi() {
    echo "[2/4] Unblocking Wi-Fi..."
    rfkill unblock wlan
    if rfkill list wlan | grep -q 'Hard blocked: yes'; then
        echo "[ERROR] A Wi-Fi radio is hard-blocked."
        rfkill list wlan
        exit 1
    fi
}

resolve_phy() {
    local pci="$1"
    local phy_path
    for phy_path in /sys/class/ieee80211/phy*; do
        [[ -e "$phy_path" ]] || continue
        if [[ "$(basename "$(readlink -f "$phy_path/device")")" == "$pci" ]]; then
            basename "$phy_path"
            return 0
        fi
    done
    return 1
}

reset_card() {
    local nic="$1"
    local pci="$2"
    local interface="$3"
    local phy
    local netdev
    local csi_control
    local netdevs=()

    if ! phy="$(resolve_phy "$pci")"; then
        echo "[ERROR] NIC=$nic PCI=$pci is not available."
        return 1
    fi

    mapfile -t netdevs < <(
        find "/sys/class/ieee80211/$phy/device/net" \
            -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null
    )
    for netdev in "${netdevs[@]:-}"; do
        [[ -n "$netdev" ]] || continue
        ip link set "$netdev" down 2>/dev/null || true
        iw dev "$netdev" del 2>/dev/null || true
    done

    iw phy "$phy" interface add "$interface" type managed
    ip link set "$interface" down

    csi_control="$(
        find /sys/kernel/debug/iwlwifi \
            -path "*$pci*/iwlmvm/csi_enabled" -print -quit 2>/dev/null || true
    )"
    if [[ -n "$csi_control" ]]; then
        printf '0\n' > "$csi_control"
    fi

    printf '[READY] NIC=%s PCI=%s %s interface=%s type=managed state=DOWN\n' \
        "$nic" "$pci" "$phy" "$interface"
}

reset_specs() {
    local label="$1"
    shift

    echo "[3/4] Resetting $label..."
    local spec
    for spec in "$@"; do
        IFS=: read -r nic domain bus slot_func interface <<< "$spec"
        reset_card "$nic" "$domain:$bus:$slot_func" "$interface"
    done
}

show_status() {
    echo "[4/4] Status..."
    iw dev | awk '/phy#|Interface|type|channel/'
}

mode="${1:-all}"
case "$mode" in
    mb|sw|all) ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        echo "Unknown setup mode: $mode"
        usage
        exit 2
        ;;
esac

require_root "$mode"
stop_feitcsi
unblock_wifi

case "$mode" in
    mb)
        reset_specs "motherboard AX210" "$MB_CARD"
        show_status
        echo "Motherboard TX card is ready. Start TX with: python3 run_1transmitter.py"
        ;;
    sw)
        reset_specs "four PCIe-switch AX210 cards" "${SW_CARDS[@]}"
        show_status
        echo "SW RX cards are ready. Start RX with: sudo -E ./run_4receiver.sh"
        ;;
    all)
        reset_specs "motherboard TX card and four SW RX cards" "$MB_CARD" "${SW_CARDS[@]}"
        show_status
        echo "All FeitCSI AX210 cards are ready."
        ;;
esac
