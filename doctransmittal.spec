# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Collect all modules inside the doctransmittal_sub package
hiddenimports = collect_submodules('doctransmittal_sub')

# Collect resources (png, ico, jpeg, templates, etc.)
datas = collect_data_files('doctransmittal_sub')

a = Analysis(
    ['launch.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    name='DocumentTransmittal'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='DocumentTransmittal'
)
