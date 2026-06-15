# Picoscenes Python Toolbox

The official Python toolbox for parsing **.csi** files from PicoScenes.

## Highlights

- Supports Linux, macOS, and Windows  
- Easy to use  
- Faster parsing than the MATLAB implementation  
- High scalability  
- The official toolbox for parsing **.csi** files  

## Preparation on Windows 10 or 11

1. Install [TDM-GCC-64](https://jmeubank.github.io/tdm-gcc/) (choose the MinGW-w64-based version, version 10.3+).
2. Set the compiler to MinGW64 using [mingw64ccompiler](https://github.com/imba-tjd/mingw64ccompiler):

```bash
pip install git+https://github.com/imba-tjd/mingw64ccompiler
python -m mingw64ccompiler install_specs  # Run once
python -m mingw64ccompiler install        # Works with venv
```

## Installation

1. Clone this repository with the `--recursive` option:

```bash
git clone https://github.com/Herrtian/PicoscenesToolbox.git --recursive
```

2. Install Python and dependencies:

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

- If you are a **Chinese** user, you can change the pip source to accelerate the download speed:

```bash
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

Preferrably, create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # or run venv\Scripts\activate on Windows
```

3. Build the package

```bash
pip3 install .  # add -e for an editable; so no reinstall is required on changes
```

## Quick start

**rx_by_usrpN210.csi** is a sample binary csi file created by **Picoscenes**.
Refer to `main.py` for an example usage on how to parse and plot the collected CSI.
This example shows the first frame of **rx_by_usrpN210.csi** and creates a plot
with x-axis representing **SubcarrierIndex** and y-axis showing **Magnitude**.

```bash
python3 main.py
```

On a successful run, you should see an output similar to:

![](docs/Figure_1.png)

## Documentation

For more details about **PicoscenesToolbox**, refer to the **./docs** folder or visit the [wiki]().

## Issues & Feedback

If you encounter any issues or have questions about **PicoscenesToolbox**, please submit an **issue** on GitHub.

Consider giving this project a **star** or **fork** if you find it useful!

## References

- **[PicoScenes](https://ps.zpj.io/)**: A powerful Wi-Fi sensing platform middleware for a wide range of hardware.  
  - This project was released by [Zhiping Jiang](https://zpj.io/bio/).
- [**csiread**](https://github.com/citysu/csiread): A fast channel state information parser for Intel, Atheros, Nexmon, and ESP32 in Python.  
  - This project, developed by [citysu](https://github.com/citysu/csiread), served as inspiration for **PicoscenesToolbox**.

## License

This project is licensed under the **MIT License**. If you use this toolbox in your project, citing this repository would be greatly appreciated.

```
Tian Teng. PicoscenesToolbox: An official tool plugin for parsing **.csi** from PicoScenes in Python. (2021). https://github.com/Herrtian/PicoscenesToolbox.
