# doctransmittal.spec

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

# -------------------------------------------------------------------
# PACKAGES THAT MUST BE INCLUDED
# -------------------------------------------------------------------

hidden = collect_submodules("doctransmittal_sub")

datas = []
# include non-Python files inside doctransmittal_sub/resources
datas += collect_data_files("doctransmittal_sub", includes=["resources/*"])


# -------------------------------------------------------------------
# 🔥 ADD THIS BLOCK (Qt platform plugins & support files)
# -------------------------------------------------------------------
# Ensures PyQt5 loads properly in the built EXE (fixes blank window / no UI)

qt_platforms = collect_data_files(
    "PyQt5",
    includes=["Qt/plugins/platforms/*"]
)

qt_svg = collect_data_files(
    "PyQt5",
    includes=["Qt/plugins/iconengines/*"]
)

qt_styles = collect_data_files(
    "PyQt5",
    includes=["Qt/plugins/styles/*"]
)

datas += qt_platforms + qt_svg + qt_styles
# -------------------------------------------------------------------


# -------------------------------------------------------------------
# ANALYSIS
# -------------------------------------------------------------------

a = Analysis(
    ["launch.py"],              # ENTRY POINT
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden + [
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PyQt5.QtCore",
        "PyQt5.QtPrintSupport",
        "PyQt5.QtSvg",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# -------------------------------------------------------------------

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="DocumentTransmittal",
    debug=True,        # change to True if you want console logging
    strip=False,
    upx=False,
    console=True,      # temporarily set to True to see errors
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="DocumentTransmittal",
)
