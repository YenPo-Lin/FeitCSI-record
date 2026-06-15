#!/bin/bash
# setup.sh
# Clean PicoScenes runtime and prepare 4 RX cards for CSI collection

set -e

# =========================
# Default settings
# =========================
NIC_IDS="${NIC_IDS:-51 52 53 54}"
CHANNEL="${CHANNEL:-5520 160 5570}"

echo "========== CSI RX Setup =========="
echo "NIC IDs : ${NIC_IDS}"
echo "Channel : ${CHANNEL}"
echo "=================================="

echo "[1/4] Stop old PicoScenes / publisher processes..."
sudo pkill -f PicoScenes 2>/dev/null || true
pkill -f run_rx_publisher.py 2>/dev/null || true

echo "[2/4] Clean PicoScenes runtime..."
sudo rm -rf /mnt/psrd/* 2>/dev/null || true
sudo mkdir -p /mnt/psrd
sudo chown -R root:dialout /mnt/psrd
sudo chmod -R g+rwX /mnt/psrd

echo "[3/4] Prepare NICs for PicoScenes..."
sudo array_prepare_for_picoscenes "${NIC_IDS}" "${CHANNEL}"

echo "[4/4] Check status..."
array_status
iw dev

echo "========== Setup Done =========="
echo "Now run:"
echo "python run_rx_publisher.py \\"
echo "  --nicNames 51 52 53 54 \\"
echo "  --nicIDs 1 2 3 4 \\"
echo "  --pico_log_level 5 \\"
echo "  --session-bus 127.0.0.1:60000 \\"
echo "  --tx_mac 70:d8:23:17:7e:38"
