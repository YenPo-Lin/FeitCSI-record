## Step 0 Start Transmitter Host
cd ~/Ax210test
conda activate ax210test
python3 transmitter_test.py
## Step 1 Start Receiver Host
### Step 1.1 Terminal 1
執行
```bash
conda activate ax210test && \
cd ~/5card_CSI_collection_codex && \
sudo -E ./run_4card_FeitCSI.sh
```
成功會看到以下輸出
```
[FeitCSI] Resolved NIC=51 PCI=0000:07:00.0 -> phy1
[FeitCSI] Resolved NIC=52 PCI=0000:08:00.0 -> phy3
[FeitCSI] Resolved NIC=53 PCI=0000:09:00.0 -> phy4
[FeitCSI] Resolved NIC=54 PCI=0000:0a:00.0 -> phy2
[FeitCSI] Four-card publisher is running on tcp://0.0.0.0:5556
[FeitCSI] Keep this terminal open. Press Ctrl+C only when capture is finished.
[FeitCSI] NIC=51 phy1 UDP=8008 -> csi.rx.1 rcvbuf=100000000
[FeitCSI] NIC=52 phy3 UDP=8009 -> csi.rx.2 rcvbuf=100000000
[FeitCSI] NIC=53 phy4 UDP=8010 -> csi.rx.3 rcvbuf=100000000
[FeitCSI] NIC=54 phy2 UDP=8011 -> csi.rx.4 rcvbuf=100000000
[ZMQ] Publisher ready. Waiting for CSI frames...
```


### Step 1.2 Terminal 2
執行
```bash
conda activate ax210test && \
cd ~/5card_CSI_collection_codex && \
python datacapture-subscriber-4ax210.py
```

## Step 2 將 CSI 轉成 NPZ

收集完成後，先停止 subscriber，再執行資料處理。請勿使用 `sudo`。

目前兩筆 session：

```text
20260614-224214_test_0614
20260614-224259_test_0614-2
```

一次處理兩筆：

```bash
cd ~/5card_CSI_collection_codex

./csi2npz.sh \
  20260614-224214_test_0614 \
  20260614-224259_test_0614-2
```

只處理單筆：

```bash
./csi2npz.sh 20260614-224214_test_0614
```

處理 `CSI_data/db` 中所有 session：

```bash
./csi2npz.sh --all
```

預設處理內容：

```text
四張卡時間配對容許誤差：750 us
遺失封包：沿時間軸插值
輸出頻率點：完整 160 MHz 頻寬等間距 512 點
HE-SU CSD 相位：移除
```

輸出檔案：

```text
CSI_data/intermediates/<session>/matched_csi/<session>_matched.csv
CSI_data/intermediates/<session>/merged_csi/<session>_merged.npz
CSI_data/npz_dataset/<session>.npz
```

`CSI_data/npz_dataset/<session>.npz` 是後續繪圖使用的簡化檔名。

NPZ 中的 CSI shape：

```text
(時間, 2 STS, 8 RX, 512 頻率點)
```

查看已產生的 NPZ：

```bash
ls -lh CSI_data/npz_dataset
```

如需調整參數：

```bash
# 配對容許誤差改成 300 us
./csi2npz.sh SESSION --tolerance-us 300

# 遺失封包保留為 NaN
./csi2npz.sh SESSION --missing-policy nan

# 重採樣為 512 個頻率點
./csi2npz.sh SESSION --subcarriers 512
```

重跑相同 session 會覆寫該 session 的 matched CSV 與 merged NPZ。

## Step 3 畫振幅與相位

`plot_subc1_amp_phase.py` 預設從 `CSI_data/npz_dataset` 讀取 NPZ，
處理順序為：

```text
每個 RX 的前 10 個 subcarrier 做複數平均
→ 減去 1 秒 moving average
→ 計算振幅與 unwrap phase
```

圖片包含上下兩張熱圖：

```text
上圖：RX1 到 RX8 的動態 CSI 振幅
下圖：RX1 到 RX8 沿時間 unwrap 後的動態 CSI 相位
X 軸：時間
Y 軸：RX
```

畫第一筆資料：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py \
  20260614-224214_test_0614
```

畫第二筆資料：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py \
  20260614-224259_test_0614-2
```

預設使用 `STS 1`，圖片存到：

```text
/home/tonic/5card_CSI_collection_codex/Heatmap_validation_pics/top_subcarriers_amp_phase.png
```

預設參數：

```text
top_avg = 10
fs = 100 Hz
MA window = 1 * fs = 100 frames
```

### 其他選項

調整平均的 subcarrier 數量：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py SESSION \
  --top-avg 20
```

不減去 moving average，改畫原始 CSI：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py SESSION \
  --no-remove-ma
```

畫 `STS 2`：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py SESSION \
  --sts_idx 1
```

自訂圖片名稱，避免第二筆覆蓋第一筆：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py SESSION \
  --output Heatmap_validation_pics/session_name_amp_phase.png
```

也可以直接傳入 NPZ 檔名或完整路徑：

```bash
python Heatmap_validation_code/plot_subc1_amp_phase.py \
  20260614-224259_test_0614-2.npz
```

## Step 4 畫 RX Pair 振幅 STFT

`plot_STFT.py` 會先從複數 CSI 減去 moving average，再取振幅，最後將
每兩個 RX 的振幅相加後計算 STFT。

四張子圖由上到下為：

```text
RX1 + RX2
RX3 + RX4
RX5 + RX6
RX7 + RX8
```

預設參數：

```text
fs = 100 Hz
MA window = 1 * fs = 100 frames
top_avg = 前 10 個 subcarrier
STS = 1
圖片尺寸 = 12 x 5
```

執行：

```bash
python Heatmap_validation_code/plot_STFT.py \
  20260614-224259_test_0614-2
```

預設輸出：

```text
Heatmap_validation_pics/STFT.png
```

調整平均 subcarrier 數量或 STS：

```bash
python Heatmap_validation_code/plot_STFT.py SESSION \
  --top-avg 20 \
  --sts_idx 1
```

調整取樣率與 MA 時間：

```bash
python Heatmap_validation_code/plot_STFT.py SESSION \
  --fs 100 \
  --ma-seconds 2
```

自訂輸出名稱：

```bash
python Heatmap_validation_code/plot_STFT.py SESSION \
  --output Heatmap_validation_pics/session_name_STFT.png
```

## Step 5 畫所有 RX 平均的 ToF-Doppler MUSIC

`MUSIC.py` 可以獨立從 `CSI_data/npz_dataset` 讀取 NPZ。八個 RX 不會
直接做複數相加，而是各自建立 snapshots，最後平均 covariance。

預設使用：

```text
STS = 1
輸入 = CSI 振幅
fs = 100 Hz
MA window = 1 * fs = 100 frames
subcarrier window = 16
Doppler window = 16
```

執行：

```bash
python Heatmap_validation_code/MUSIC.py \
  20260614-224214_test_0614
```

圖片預設存入：

```text
Heatmap_validation_pics/<session>_ToF_Doppler_amplitude.png
```

指定分析 frame：

```bash
python Heatmap_validation_code/MUSIC.py SESSION \
  --frame_idx 2000
```

使用複數 CSI 相位：

```bash
python Heatmap_validation_code/MUSIC.py SESSION \
  --input-mode complex
```

### 使用 GPU

`MUSIC.py` 預設使用 `--device cuda`。在 `ax210test` 執行時，如果該
環境沒有 PyTorch，程式會自動切換到已有 CUDA PyTorch 的 base Python，
並使用 NVIDIA GPU。

強制使用 GPU：

```bash
python Heatmap_validation_code/MUSIC.py SESSION \
  --device cuda \
  --fs 100
```

強制使用 CPU：

```bash
python Heatmap_validation_code/MUSIC.py SESSION \
  --device cpu \
  --fs 100
```

只有明確指定 `--device cpu` 才會使用 CPU。若 GPU 無法使用，預設
CUDA 模式會直接報錯，不會靜默回退 CPU。

目前 RTX 3060 Ti 8GB 已測試可執行：

```text
subc_win = 64
dop_win = 64
Rxx shape = 4096 x 4096
估計 GPU matrix/workspace = 0.50 GiB
```

若顯示 GPU 記憶體不足，先降低：

```bash
python Heatmap_validation_code/MUSIC.py SESSION \
  --device cuda \
  --subc_win 32 \
  --dop_win 32
```

`--tau-chunk` 控制每次送入 GPU 的 ToF grid 數量，預設為 `4`。降低它
可以減少 spectrum 計算的暫存顯存。

振幅模式可以產生動作特徵 heatmap，但 ToF 軸不代表可靠的絕對飛行
時間。要估計物理 ToF，需使用 complex 模式並完成 SFO、PDD、CFO 與
硬體相位校正。
