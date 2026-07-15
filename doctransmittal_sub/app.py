from __future__ import annotations
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
from doctransmittal_sub.core.settings import SettingsManager
from .ui.main_window import MainWindow, _app_icon_path, _close_pyinstaller_splash

def run():
    app = QApplication(sys.argv)
    try:
        icon_path = _app_icon_path()
        if icon_path:
            app.setWindowIcon(QIcon(icon_path))
    except Exception:
        pass

    settings = SettingsManager()
    win = MainWindow(settings)
    win.show()
    try:
        app.processEvents()
    except Exception:
        pass
    _close_pyinstaller_splash()
    sys.exit(app.exec_())
