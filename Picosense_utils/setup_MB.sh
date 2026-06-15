#!/bin/bash
# MB_setup.sh
# 用途：設定主板直連 PCIe 上的 AX210 網卡
# MB = Motherboard
#
# 主板直連：
# MB -> wlp11s0 -> PhyPath 6

set -e

echo "[1/4] 產生 PicoScenes 網卡對照表..."
array_status || true

echo "[2/4] 設定 /mnt/psrd 權限..."
sudo mkdir -p /mnt/psrd
sudo chown -R root:dialout /mnt/psrd
sudo chmod -R g+rwX /mnt/psrd

echo "[3/4] 關閉舊的 PicoScenes / RX publisher..."
sudo pkill -f PicoScenes || true
pkill -f run_rx_publisher.py || true

echo "[4/4] 將主板直連網卡切成 monitor mode..."
sudo rfkill unblock all || true

iface="wlp11s0"
echo "設定 $iface 為 monitor mode..."
sudo ip link set "$iface" down || true
sudo iw dev "$iface" set type monitor
sudo ip link set "$iface" up || true

echo "確認 Wi-Fi 介面模式："
iw dev | grep -E "Interface|type"

echo "MB setup 完成。"
