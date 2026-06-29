---
title: FeitCSI 5cardCSI collection quick guide

---

# Quick WorkFlow
1. Reset All NICs
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
sudo -E ./setup.sh
```
2. Terminal 1: Tx
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
python3 run_1transmitter.py
```
3. Terminal 2: Rx-publisher
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
sudo -E ./run_4receiver.sh --mode 5 --tx-mac 00:16:ea:12:34:56
```
4. Terminal 3: Rx-subscriber
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
 python datacapture-subscriber-4ax210.py
```
5. check csi file
```bash
cd ~/media/tonic/DataSSD/CSI_data_2026/db
ls
```
6. csi to .npz
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
./csi2npz.sh <DATA_NAME>
```
8. Heatmap generation
frame index =300
azimuth-tof, tof-doppler heatmap example
```
cd ~/5card_CSI_collection_codex/Exp-code
/home/tonic/miniconda3/bin/python main.py \
  --npz_dir /media/tonic/DataSSD/CSI_data_2026/npz_files \
  --npz_file <DATA_NAME>.npz \
  --plot single \
  --frame_idx 300
```
---

# Detail WorkFlow
## Step 0 Reset all NICs
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
sudo -E ./setup.sh
```

![image](https://hackmd.io/_uploads/Bk8KV017Mx.png)


## Step 1 Start Transmitter

**默認 mode 5**
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
python3 run_1transmitter.py
```
**mode 6**
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
python3 start_tx.py --mode 6
```
![image](https://hackmd.io/_uploads/Hy4lSRJQMe.png)


**mode 5, mode 6 差異**
| mode | Freq Band | Control Freq | Center Freq | BW    |
|:----:| --------- | ------------ | ----------- | ----- |
|  \-- mode 5   | 5 G       | 5520 M       | 5570 M      | 160 M |
|  \-- mode 6   | 6 G       | 5955 M       | 6025 M      | 160 M |

## Step 2 Start Publisher
另開新Terminal啟動四卡 FeitCSI receiver 
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
sudo -E ./run_4receiver.sh --mode 5 --tx-mac 00:16:ea:12:34:56
```
默認
 --mode 5
 --tx-mac 00:16:ea:12:34:56
 
![image](https://hackmd.io/_uploads/r1Y8rRkQfl.png)



:::info
這裡的 tx-mac 是 FeitCSI / 封包 header 裡實際被 RX 解析到的 src_mac。MAC filter 是用封包裡的 src_mac 過濾，不是用 Linux interface MAC 過濾。
之後要查看實際 src_mac：
sudo -E ./run_4receiver.sh --mode 5 --print-src-mac
然後開 TX，印出：
[FeitCSI] NIC=51 observed src_mac=00:16:ea:12:34:56 rssi1=... rssi2=...
:::
## Step 3 Start Subscriber
另開新 Terminal 啟動 subscriber，資料會預設存到 DataSSD：
```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
 python datacapture-subscriber-4ax210.py
```
:::info
預設輸出：
db:        /media/tonic/DataSSD/CSI_data_2026/db
artifacts: /media/tonic/DataSSD/CSI_data_2026/artifacts
:::
:::warning
偶爾會出現某張網卡收不到封包的情況，可以回到 Step 0 多 reset 幾次，還是不行的話可以重開機或是改天再做:sleepy: 
:::

![image](https://hackmd.io/_uploads/H1ZKHR1Qzg.png)


成功畫面如下:+1: ：
若Tx端 packet rate 100 PCK/s, subscriber端 收到約98.多即正常

![image](https://hackmd.io/_uploads/By93r01mGe.png)


![image](https://hackmd.io/_uploads/SknCrR1Xzl.png)


在 subscriber 畫面中：
```
N = 設定 label 輸入標題 Enter 
Q = 收 10 秒
W = 收 20 秒
E = 收 30 秒
Space = 手動開始/停止
x = 離開
```

## Step 4 將 CSI 轉成 NPZ
收到的資應該要出現在 /artifacts 跟 /db
```bash
cd ~/media/tonic/DataSSD/CSI_data_2026/db
ls
 ```
只處理單筆：

```bash
conda activate ax210test
cd ~/5card_CSI_collection_codex
./csi2npz.sh 20260629-192351_test
```
處理 `/media/tonic/DataSSD/CSI_data_2026/db` 中所有 session：

```bash
./csi2npz.sh --all
```

![image](https://hackmd.io/_uploads/rJUPUAJmzg.png)
:::info
理論上 TX 若是 100 packets/s，10 秒會是 1000 frames。
但實際接收 packet rate 通常約為 98~99 packets/s，所以 10 秒常見會得到約 980~990 frames，目前不會強制補成 1000 frames。

關於matcher:
matcher 做的是：把四張 NIC 收到的 CSI 封包，用時間戳對齊成同一條 timeline。
每一張 NIC 收到封包的時間不會完全一樣，而且有些 NIC 可能會漏封包。所以 csi_matcher.py 會先選一個 reference topic，目前預設是csi.rx.1

關於merger:
目前 merger 會做兩種 interpolation：
* 頻率軸 interpolation
將 FeitCSI 原始 HE160 CSI tones 重採樣成指定 subcarrier 數，例如 64。
* missing packet interpolation
如果某張 NIC 在 reference timeline 中少了幾個封包，會在既有的 988 個時間點內補值。
:::

## Step 5 Baseline Heatmap 生成

將 `.npz` 轉好後，可以用 `Exp-code/main.py` 產生 baseline heatmap。  

輸入 `.npz` 來源為：
/media/tonic/DataSSD/CSI_data_2026/npz_files

輸出圖片會存在：
/home/tonic/5card_CSI_collection_codex/Exp-pics
### Heatmap Type
預設同時畫：azi_tof, tof_dop
```
--heatmap_type all
```

只畫 Azimuth-ToF：
```
--heatmap_type azi_tof
```

### 產生單一 frame heatmap
指定單一 frame：
```
cd ~/5card_CSI_collection_codex/Exp-code
/home/tonic/miniconda3/bin/python main.py \
  --npz_dir /media/tonic/DataSSD/CSI_data_2026/npz_files \
  --npz_file 20260629-192351_test.npz \
  --plot single \
  --frame_idx 300
```
預設會畫資料中間的 frame: frame_idx = total frame //2

輸出檔名範例：
Exp-pics/20260629-192351_test_tof_dop_300.png
Exp-pics/20260629-192351_test_azi_tof_300.png

### 產生多個 frame heatmap
```
cd ~/5card_CSI_collection_codex/Exp-code
/home/tonic/miniconda3/bin/python main.py \
  --npz_dir /media/tonic/DataSSD/CSI_data_2026/npz_files \
  --npz_file 20260629-192351_test.npz \
  --plot multiple \
  --start_plot 100 \
  --end_plot 500 \
  --plot_interval 10
```
 這會畫：100, 110, 120, ..., 500
 multiple 模式會自動在 Exp-pics 裡新增兩個資料夾：
 Exp-pics/20260629-192351_test_tof_dop_100-500-10
Exp-pics/20260629-192351_test_azi_tof_100-500-10
