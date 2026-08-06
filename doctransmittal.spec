# doctransmittal.spec

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.building.splash import Splash


# -------------------------------------------------------------------
# BUILD ASSETS
# -------------------------------------------------------------------

SPEC_DIR = Path(globals().get("__file__", "doctransmittal.spec")).resolve().parent

_ICON_CANDIDATES = [
    SPEC_DIR / "doctransmittal_sub" / "resources" / "logo_small.ico",
]
APP_ICON = next((str(p) for p in _ICON_CANDIDATES if p.exists()), None)

SPLASH_IMAGE = str(SPEC_DIR / "splash.png")


# -------------------------------------------------------------------
# APPLICATION CONTENT
# -------------------------------------------------------------------

hidden = collect_submodules("doctransmittal_sub")
hidden += collect_submodules("comtypes")

datas = []

# Include non-Python files inside doctransmittal_sub/resources
datas += collect_data_files(
    "doctransmittal_sub",
    includes=["resources/*"],
)


# -------------------------------------------------------------------
# QT PLUGINS
# -------------------------------------------------------------------

datas += collect_data_files(
    "PyQt5",
    includes=["Qt/plugins/platforms/*"],
)

datas += collect_data_files(
    "PyQt5",
    includes=["Qt/plugins/iconengines/*"],
)

datas += collect_data_files(
    "PyQt5",
    includes=["Qt/plugins/styles/*"],
)


# -------------------------------------------------------------------
# ANALYSIS
# -------------------------------------------------------------------

a = Analysis(
    ["launch.py"],
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
# PYTHON ARCHIVE
# -------------------------------------------------------------------

pyz = PYZ(a.pure)


# -------------------------------------------------------------------
# SPLASH SCREEN
# -------------------------------------------------------------------
# Place splash.png beside this spec file.
# PNG is the preferred format.

splash = Splash(
    SPLASH_IMAGE,
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    always_on_top=True,
    center="active",
)


# -------------------------------------------------------------------
# EXECUTABLE
# -------------------------------------------------------------------

exe = EXE(
    pyz,
    splash,
    a.scripts,

    # Critical for one-directory mode:
    exclude_binaries=True,

    name="DocumentTransmittal",
    debug=False,
    strip=False,
    upx=False,

    # Removes the terminal/console window:
    console=False,

    # Windows EXE icon. To override, place app.ico in doctransmittal_sub/resources/.
    icon=APP_ICON,
)


# -------------------------------------------------------------------
# ONE-DIRECTORY OUTPUT
# -------------------------------------------------------------------

coll = COLLECT(
    exe,
    splash.binaries,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="DocumentTransmittal",
)