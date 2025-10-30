# doctransmittal.spec — PyInstaller 6.x, ONEDIR (fast)
# Build:  pyinstaller --clean doctransmittal.spec

# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.building.datastruct import Tree

app_name = "DocumentTransmittal"
entry_script = "main.py"                 # <-- uses doctransmittal_sub.app.run()

pathex = [os.getcwd()]

# Freeze your whole package into the PYZ (so relative imports work normally)
hiddenimports = []
hiddenimports += collect_submodules("doctransmittal_sub")
hiddenimports += collect_submodules("PyQt5")
hiddenimports += collect_submodules("docx")
# If you use COM (Word/Excel), uncomment:
# hiddenimports += collect_submodules("win32com"); hiddenimports += ["pythoncom", "pywintypes"]

# Datas into Analysis must be (src, dest) tuples only
datas = []
datas += collect_data_files("PyQt5")     # Qt plugins/styles etc.

# Qt DLLs
binaries = collect_dynamic_libs("PyQt5")

# Optional icon
icon_path = os.path.join("doctransmittal_sub", "resources", "logo.ico")
if not os.path.exists(icon_path):
    icon_path = None

# Resource folders go into COLLECT (PyInstaller 6.x)
extra_trees = []
res_dir = os.path.join("doctransmittal_sub", "resources")
if os.path.isdir(res_dir):
    extra_trees.append(Tree(res_dir, prefix=res_dir))
if os.path.isdir("DM-Logos"):
    extra_trees.append(Tree("DM-Logos", prefix="DM-Logos"))

block_cipher = None

a = Analysis(
    [entry_script],
    pathex=pathex,
    binaries=binaries,
    datas=datas,                 # tuples only
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,               # True if you want a console window
    icon=icon_path,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    *extra_trees,                # resources beside the EXE
    strip=False, upx=True, name=app_name,
)
