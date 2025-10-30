# launch.py
import sys
from pathlib import Path
import runpy

def _ensure_project_on_path():
    # When frozen (PyInstaller), sys._MEIPASS exists in onefile;
    # in onedir builds, the EXE dir is already on sys.path, but this is harmless.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    # Ensure the folder that contains 'doctransmittal_sub' is importable
    sys.path.insert(0, str(base))

if __name__ == "__main__":
    _ensure_project_on_path()
    # Run your UI module *as a module* so relative imports (from .foo import bar) work
    runpy.run_module("doctransmittal_sub.ui.main_window", run_name="__main__")
