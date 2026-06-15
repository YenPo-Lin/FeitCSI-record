#!/usr/bin/env bash
# Reset the motherboard-connected AX210 to a clean FeitCSI-ready state.
# The onboard CNVi at 0000:00:14.3 is intentionally left untouched.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Root is required to reset the Wi-Fi interface."
    echo "Run: sudo -E $0"
    exit 1
fi

PCI="0000:0b:00.0"
INTERFACE="wlp11s0"

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

if ! phy="$(resolve_phy "$PCI")"; then
    echo "[ERROR] Motherboard AX210 PCI=$PCI is not available."
    exit 1
fi

echo "[1/4] Stopping FeitCSI capture processes..."
for port in 8008 8009 8010 8011; do
    printf 'stop' >"/dev/udp/127.0.0.1/$port" 2>/dev/null || true
done
sleep 1
pkill -f '/third_party/FeitCSI/bin/app' 2>/dev/null || true
pkill -f '/feitcsi_integration/feitcsi_bridge.py' 2>/dev/null || true

echo "[2/4] Unblocking Wi-Fi..."
rfkill unblock wlan
if rfkill list wlan | grep -q 'Hard blocked: yes'; then
    echo "[ERROR] A Wi-Fi radio is hard-blocked."
    rfkill list wlan
    exit 1
fi

echo "[3/4] Resetting motherboard AX210..."
mapfile -t netdevs < <(
    find "/sys/class/ieee80211/$phy/device/net" \
        -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null
)
for netdev in "${netdevs[@]:-}"; do
    [[ -n "$netdev" ]] || continue
    ip link set "$netdev" down 2>/dev/null || true
    iw dev "$netdev" del 2>/dev/null || true
done

iw phy "$phy" interface add "$INTERFACE" type managed
ip link set "$INTERFACE" down

csi_control="$(
    find /sys/kernel/debug/iwlwifi \
        -path "*$PCI*/iwlmvm/csi_enabled" -print -quit 2>/dev/null || true
)"
if [[ -n "$csi_control" ]]; then
    printf '0\n' > "$csi_control"
fi

echo "[4/4] Status..."
printf '[READY] PCI=%s %s interface=%s type=managed state=DOWN\n' \
    "$PCI" "$phy" "$INTERFACE"
iw dev "$INTERFACE" info
echo "Onboard CNVi 0000:00:14.3 was not changed."
