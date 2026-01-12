import sys
from pathlib import Path

def fix_path():
    # Running under PyInstaller?
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent

    sys.path.insert(0, str(base))
    sys.path.insert(0, str(base / "doctransmittal_sub"))

fix_path()

# Import main window safely
from doctransmittal_sub.ui.main_window import main_window_entry

if __name__ == "__main__":
    main_window_entry()
