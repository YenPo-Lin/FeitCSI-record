#!/bin/bash
# SW_setup.sh
# 用途：用 PicoScenes 官方建議方式設定 PCIe 擴充板上的 4 張 AX210 網卡
# SW = PCIe Switch / PCIe Expansion Board
#
# 擴充板對應：
# P1 -> wlp7s0  -> PhyPath 51
# P2 -> wlp8s0  -> PhyPath 52
# P3 -> wlp9s0  -> PhyPath 53
# P4 -> wlp10s0 -> PhyPath 54

set -e

NIC_IDS="${NIC_IDS:-51 52 53 54}"
CHANNEL="${CHANNEL:-5520 160 5570}"

echo "[1/4] 產生 PicoScenes 網卡對照表..."
array_status || true

echo "[2/4] 設定 /mnt/psrd 權限..."
sudo mkdir -p /mnt/psrd
sudo chown -R root:dialout /mnt/psrd
sudo chmod -R g+rwX /mnt/psrd

echo "[3/4] 關閉舊的 PicoScenes / RX publisher..."
sudo pkill -f PicoScenes || true
pkill -f run_rx_publisher.py || true

echo "[4/4] 用 array_prepare_for_picoscenes 準備 4 張 AX210..."
sudo rfkill unblock all || true
sudo array_prepare_for_picoscenes "${NIC_IDS}" "${CHANNEL}"

echo "確認 PicoScenes / Wi-Fi 介面狀態："
array_status || true
iw dev | grep -E "Interface|type"

echo "SW setup 完成。"
