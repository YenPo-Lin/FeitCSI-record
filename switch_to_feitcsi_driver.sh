#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KERNEL="$(uname -r)"
PICO_PACKAGE="picoscenes-driver-modules-$KERNEL"
FEIT_DEB="$ROOT/third_party/feitcsi-packages/feitcsi-iwlwifi_2.0.0+ubuntu3_all.deb"
EXPECTED_SHA256="fa6030fe3ec74e871baf32884e20dd9e77caa4207ef9db6293f85351dfd23e32"

if [[ ! -f "$FEIT_DEB" ]]; then
    echo "Missing official FeitCSI driver package: $FEIT_DEB"
    exit 1
fi

actual_sha256="$(sha256sum "$FEIT_DEB" | awk '{print $1}')"
if [[ "$actual_sha256" != "$EXPECTED_SHA256" ]]; then
    echo "FeitCSI driver package checksum mismatch."
    exit 1
fi

echo "Kernel: $KERNEL"
echo "Remove: $PICO_PACKAGE"
echo "Install: $FEIT_DEB"
echo "Build dependencies: flex bison linux-headers-$KERNEL"
echo

if [[ "${1:-}" != "--apply" ]]; then
    echo "Dry run only. The following package operations would be performed:"
    apt-get -s install flex bison "linux-headers-$KERNEL"
    apt-get -s remove "$PICO_PACKAGE"
    apt-get -s install "$FEIT_DEB"
    echo
    echo "Run sudo $0 --apply to switch drivers, then reboot."
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "Apply mode requires root: sudo $0 --apply"
    exit 1
fi

if pgrep -f 'FeitCSI/bin/app|feitcsi_bridge.py' >/dev/null; then
    echo "FeitCSI capture is still running. Stop run_4card_feitcsi.sh with Ctrl+C first."
    exit 1
fi

apt-get install -y flex bison "linux-headers-$KERNEL"
if dpkg-query -W -f='${db:Status-Abbrev}' "$PICO_PACKAGE" 2>/dev/null | grep -q '^ii'; then
    apt-get remove -y "$PICO_PACKAGE"
else
    echo "PicoScenes driver package is already absent: $PICO_PACKAGE"
fi
apt-get install -y "$FEIT_DEB"
update-initramfs -u -k "$KERNEL"

echo
echo "FeitCSI driver installed for $KERNEL."
echo "Reboot, then verify that at least four csi_enabled files exist:"
echo "  find /sys/kernel/debug/iwlwifi -path '*/iwlmvm/csi_enabled'"
