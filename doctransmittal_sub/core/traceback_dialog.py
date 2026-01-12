import sys
import traceback
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QApplication
from PyQt5.QtCore import Qt
from .logger import get_logger


class TracebackDialog(QDialog):
    def __init__(self, exc_type, exc_value, tb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("An error occurred")
        self.resize(800, 500)

        layout = QVBoxLayout(self)
        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.text)

        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

        trace_str = "".join(traceback.format_exception(exc_type, exc_value, tb))
        self.text.setPlainText(trace_str)


_log = get_logger()


def install_excepthook():
    def handle(exc_type, exc, tb):
        _log.exception("Unhandled exception:")
        app = QApplication.instance()
        if app is None:
            # Fallback to console
            traceback.print_exception(exc_type, exc, tb)
        else:
            dlg = TracebackDialog(exc_type, exc, tb)
            dlg.exec_()
    sys.excepthook = handle

