import sys
from pathlib import Path
import runpy

def _ensure_project_on_path():
    # _MEIPASS path for PyInstaller temporary extraction
    # or script directory when running normally
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

    # Add MEIPASS (for resources)
    sys.path.insert(0, str(base))

    # Add the actual folder containing doctransmittal_sub
    sys.path.insert(0, str(Path(__file__).resolve().parent))


if __name__ == "__main__":
    _ensure_project_on_path()
    runpy.run_module("doctransmittal_sub.ui.main_window", run_name="__main__")
