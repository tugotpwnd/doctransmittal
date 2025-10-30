from __future__ import annotations
import traceback
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
from PyQt5.QtCore import Qt

class TracebackDialog(QDialog):
    def __init__(self, exc_type, exc_value, tb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("An error occurred")
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        self.text = QTextEdit(self); self.text.setReadOnly(True); self.text.setLineWrapMode(QTextEdit.NoWrap)
        lay.addWidget(self.text)
        btn = QPushButton("Close"); btn.clicked.connect(self.accept)
        lay.addWidget(btn, alignment=Qt.AlignRight)
        trace_str = "".join(traceback.format_exception(exc_type, exc_value, tb))
        self.text.setPlainText(trace_str)