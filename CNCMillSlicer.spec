# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

block_cipher = None

BASE_DIR = Path.cwd()

datas = [
    (str(BASE_DIR / 'ui'), 'ui'),
    (str(BASE_DIR / 'assets'), 'assets'),
    (str(BASE_DIR / 'tests' / 'fixtures'), 'tests/fixtures'),
]

hidden_imports = [
    'engine',
    'engine.lidar',
    'engine.lidar.mock_sensor',
    'engine.lidar.scanner',
    'engine.slicer',
    'engine.slicer.stock',
    'engine.slicer.tool',
    'engine.slicer.subtractive',
    'engine.slicer.analyzer',
    'engine.slicer.auto_pilot',
    'engine.slicer.machine',
    'engine.gcode',
    'engine.gcode.generator',
    'engine.gcode.validator',
    'server',
    'server.app',
    'shapely',
    'shapely.geometry',
    'pyclipper',
    'trimesh',
    'trimesh.exchange',
    'trimesh.exchange.stl',
    'scipy',
    'scipy.spatial',
    'cv2',
    'flask',
    'jinja2',
    'werkzeug',
    'webview',
    'webview.platforms.winforms',
    'clr_loader',
    'pythonnet',
]

a = Analysis(
    ['desktop_app.py'],
    pathex=[str(BASE_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'torch', 'transformers', 'accelerate'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CNCMillSlicer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE_DIR / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='CNCMillSlicer',
)
