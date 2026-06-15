#!/bin/bash

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT_DIR}/realtime_collection_adin_fix"

python run_rx_publisher-4ax210.py \
  --nicNames 51 52 53 54 \
  --nicIDs 1 2 3 4 \
  --pico_log_level 5 \
  --session-bus 127.0.0.1:60000 \
  --tx_mac 70:d8:23:17:7e:38
