# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ('assets', 'assets'),
    ('.venv/Lib/site-packages/cv2/config.py', 'cv2'),
    ('.venv/Lib/site-packages/cv2/config-3.py', 'cv2'),
    ('THIRD_PARTY_NOTICES.md', '.'),
]
binaries = []
hiddenimports = []

# Keep the desktop package focused on the model actually used. In particular,
# collecting every Transformers architecture creates a massive and slow build.
for package in ('cv2', 'anime_seg', 'vtracer'):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += collect_submodules('transformers.models.mask2former')
hiddenimports += collect_submodules('transformers.models.swin')
hiddenimports += [
    'transformers.models.auto.configuration_auto',
    'transformers.models.auto.modeling_auto',
    'safetensors.torch',
    'huggingface_hub.file_download',
    'huggingface_hub.utils._http',
    'peft',
    'PIL.Image',
]

a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Anime to Desmos Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Anime to Desmos Studio',
)
