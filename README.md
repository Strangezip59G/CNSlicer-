# CNSlicer — Subtractive Studio

[![Tests](https://img.shields.io/badge/Tests-58%20Passing-brightgreen.svg)](tests/)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20x64-lightgrey.svg)](dist/)
[![Design](https://img.shields.io/badge/Design-Apple%20HIG%20Glass-000000.svg)](ui/)

**CNSlicer** is an intelligent, high-precision subtractive 3D CAM slicer and desktop studio tailored for CNC routers and industrial vertical machining centers. It transforms arbitrary 3D models (`.STL`, `.OBJ`) into production-ready G-code toolpaths with material physics optimization, tool wear modeling, live 3D bed simulation, and Apple-grade engineering UI.

---

## Key Features

### 1. 1-Click Auto-Pilot CAM
* **Automatic Geometry Analysis:** Automatically measures thin walls, overhang angles, draft slopes, deep pockets, and minimum internal corner radii.
* **Smart Tool Selection:** Chooses optimal flat endmills and ballnose cutters from the tooling library based on part geometry.
* **Adaptive Multi-Pass Slicing:** Generates constant Z-level roughing paths, pocket clearing offsets, and 3D surface finishing passes automatically.
* **Feeds & Speeds Optimization:** Automatically calculates spindle RPM, cutting feed rates, plunge rates, and stepover distances tailored to selected stock material.

### 2. Multi-Axis Subtractive CAM Engine
* **Constant Z-Level Contouring:** High-speed 2D cross-section planar slicing with PyClipper offset buffering.
* **Collinear & Mitre Join Filtering:** Eliminates micro-move polygon bloat and machine stuttering through polygon simplification.
* **Surface Scallop Finishing:** Constant scallop-height finishing passes using ballnose tooling.
* **Toolpath Simulator:** 3D animated toolhead traversal with color-coded moves:
  * <span style="color:#22c55e;">Green</span>: Cutting feed moves
  * <span style="color:#ef4444;">Red</span>: Rapid travel moves ($G0$)
  * <span style="color:#3b82f6;">Cyan</span>: Plunge moves

### 3. Machining Physics & Optimizer
* **Kienzle Specific Cutting Force Model:** Predicts cutting forces ($F_c$), deflection, and spindle power consumption ($P_c$) across materials.
* **Taylor Tool Life & Wear Modeling:** Computes tool wear index ($V_B$) and warns when feed/speed combinations risk premature cutter breakage.
* **Multi-Objective Trade-Off Modes:**
  * **Fastest:** Maximum material removal rate (MRR) for rapid prototyping.
  * **Balanced:** Optimum balance between production cycle time and tool longevity.
  * **Tool Preserving:** Conservative chip loads and lower surface footage to maximize endmill life.
  * **Fine Finish:** Micro-stepovers for mirror-finish surfaces and minimal hand polishing.
* **G2/G3 Bi-Arc Compressor:** Converts dense linear point-to-point sequences ($G1$) into smooth circular arcs ($G2/G3$), shrinking G-code file sizes by up to 70% while improving CNC controller lookahead.

### 4. Comprehensive Machine Profiles & 3D Spoilboards
Dynamically scales 3D bed envelopes, spoilboard geometry, clamping guides, wireframe travel boundary cages, and spindle limits:
* **Industrial Machining Centers:**
  * **Haas VF-2:** $762 \times 406 \times 508\text{ mm}$ ($30 \times 16 \times 20\text{ in}$), 12,000 RPM inline direct-drive, cast-iron table with 14 mm T-slots and coolant troughs.
  * **DMG MORI CMX 600 V:** $600 \times 560 \times 510\text{ mm}$, 12,000 RPM, heavy production VMC table.
  * **Brother SPEEDIO S500X2:** $500 \times 400 \times 300\text{ mm}$, 16,000 RPM, $50\text{ m/min}$ rapids.
  * **Datron neo:** $520 \times 420 \times 220\text{ mm}$, 40,000 RPM high-speed mill with integrated vacuum chuck bed.
  * **Tormach 1100M:** $457 \times 279 \times 412\text{ mm}$, 7,500 RPM, cast-iron precision ground table.
  * **Avid CNC PRO4848:** $1250 \times 1250 \times 200\text{ mm}$ (4×4 ft) industrial gantry router.
* **Desktop & Benchtop Mills:**
  * **Makera Carvera:** $360 \times 240 \times 140\text{ mm}$ (Automatic Tool Changer)
  * **Carbide Nomad 3:** $200 \times 200 \times 76\text{ mm}$ (Enclosed precision benchtop)
  * **Bantam Tools Desktop CNC:** $178 \times 127 \times 89\text{ mm}$ (PCB & precision aluminum prototyping)
  * **Shapeoko 4 Standard:** $444 \times 444 \times 101\text{ mm}$
  * **Inventables X-Carve 1000mm:** $750 \times 750 \times 65\text{ mm}$
  * **Genmitsu 3018-PROVer & PROVerXL 4030**
* **Custom Spoilboard:** Fully configurable custom dimensions, spindle RPM caps, and feed rates.

### 5. Apple Human Interface Design
* **Pure Vector Geometry:** Zero raw unicode emojis; handcrafted SVG iconography throughout.
* **Glass Sliding Tabs:** Interactive segmented controls with frosted glass sliding indicators.
* **Dark / Light Dynamic Theme:** Engineered contrast with Cupertino dark slate and silver highlights.
* **Collapsible & Resizable Sidebars:** Drag-to-resize side inspector with double-click auto-collapse.

---

## Installation & Downloads

### Option A: Standard Windows Installer (Recommended)
Download and run the installer wizard (like Creality Print / Cura):
* **Installer:** `dist/CNSlicer-Setup-v1.0-Windows-x64.exe`
* Installs to Program Files or User Apps
* Creates Start Menu and Desktop shortcuts
* Associates `.stl` and `.gcode` files

### Option B: Portable Release ZIP
* **Package:** `dist/CNSlicer-v1.0-Windows-x64.zip`
* Unzip and run `CNSlicer.exe` directly without administrative privileges.

---

## Developer Setup

### Prerequisites
* Windows 10/11 (64-bit)
* Python 3.10+
* Microsoft Edge WebView2 (built into Windows 10/11)

### 1. Clone & Setup Virtual Environment
```powershell
git clone https://github.com/CNSlicer/CNSlicer.git
cd CNSlicer

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Run Desktop Application
```powershell
.\.venv\Scripts\python.exe desktop_app.py
```

### 3. Run Test Suite
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
*(All 58 test cases passing)*

### 4. Build Standalone Executable & Installer
```powershell
# 1. Build PyInstaller bundle
.\.venv\Scripts\pyinstaller --noconfirm --clean CNSlicer.spec

# 2. Compile Windows Setup Installer (Requires Inno Setup 6)
& "C:\Users\Friday\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\cnslicer.iss
```

---

## Architecture

```
CNSlicer/
├── assets/                  # Application icons (.ico, .png) and visual assets
├── engine/                  # Core subtractive computational geometry engine
│   ├── slicer/              # Slicing logic, stock models, tools, machine profiles
│   ├── gcode/               # G-code generator and formal syntax validator
│   ├── optimizer/           # Kienzle physics, tool wear model, G2/G3 arc compressor
│   └── lidar/               # Toolhead surface scanning simulation
├── installer/               # Inno Setup compiler scripts (.iss)
├── server/                  # Flask backend providing REST APIs for local IPC
├── ui/                      # Apple HIG frontend
│   ├── index.html           # Single-page studio interface
│   └── static/              # CSS styles, Three.js shaders, client application logic
├── tests/                   # 58 unit tests covering engine, physics, UI & server
├── desktop_app.py           # Native Edge WebView2 desktop application entry point
├── CNSlicer.spec            # PyInstaller build specification
└── requirements.txt         # Python package dependencies
```

---

## License

This project is licensed under the [MIT License](LICENSE) — see the LICENSE file for details.
