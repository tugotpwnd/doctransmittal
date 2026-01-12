from __future__ import annotations
import sys
from PyQt5.QtWidgets import QApplication
from doctransmittal_sub.core.settings import SettingsManager
from .ui.main_window import MainWindow

def run():
    app = QApplication(sys.argv)
    settings = SettingsManager()
    win = MainWindow(settings); win.show()
    sys.exit(app.exec_())