from __future__ import annotations
from typing import Dict
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QColorDialog, QScrollArea, QWidget, QFrame
)
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtCore import Qt, pyqtSignal
from ..core.settings import SettingsManager

class ColorSwatch(QFrame):
    clicked = pyqtSignal()

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.color = QColor(color_hex)
        self.setStyleSheet(f"background-color: {color_hex}; border: 1px solid #888; border-radius: 4px;")

    def set_color(self, color_hex: str):
        self.color = QColor(color_hex)
        self.setStyleSheet(f"background-color: {color_hex}; border: 1px solid #888; border-radius: 4px;")

    def mousePressEvent(self, event):
        self.clicked.emit()

class ColorSettingsDialog(QDialog):
    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Custom Colours")
        self.resize(400, 500)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.container_layout = QVBoxLayout(container)
        
        # CheckPrint section
        self.container_layout.addWidget(QLabel("<b>CheckPrint Statuses</b>"))
        self.cp_swatches = {}
        cp_items = {
            "pending": "Pending",
            "rejected": "Rejected",
            "accepted_minor": "Accepted Minor / Approved Minor",
            "accepted": "Accepted",
            "approved": "Approved"
        }
        for key, label in cp_items.items():
            self._add_color_row("ui.colors.checkprint." + key, label, self.cp_swatches)

        self.container_layout.addSpacing(20)

        # Matching section
        self.container_layout.addWidget(QLabel("<b>File Matching</b>"))
        self.match_swatches = {}
        match_items = {
            "match": "Match (Auto)",
            "manual": "Match (Manual)",
            "duplicate": "Duplicate",
            "not_found": "Couldn't be found / Missing"
        }
        for key, label in match_items.items():
            self._add_color_row("ui.colors.matching." + key, label, self.match_swatches)

        self.container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Buttons
        btn_box = QHBoxLayout()
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_box.addWidget(reset_btn)
        btn_box.addStretch()
        btn_box.addWidget(close_btn)
        layout.addLayout(btn_box)

    def _add_color_row(self, settings_key: str, label_text: str, swatch_dict: dict):
        row = QHBoxLayout()
        color_hex = self.settings.get(settings_key, "#888888")
        
        swatch = ColorSwatch(color_hex)
        swatch.clicked.connect(lambda: self._pick_color(settings_key, swatch))
        swatch_dict[settings_key] = swatch
        
        row.addWidget(swatch)
        row.addWidget(QLabel(label_text))
        row.addStretch()
        self.container_layout.addLayout(row)

    def _pick_color(self, settings_key: str, swatch: ColorSwatch):
        current_color = QColor(self.settings.get(settings_key, "#888888"))
        new_color = QColorDialog.getColor(current_color, self, "Select Colour")
        if new_color.isValid():
            hex_name = new_color.name().upper()
            self.settings.set(settings_key, hex_name)
            swatch.set_color(hex_name)

    def _reset_defaults(self):
        defaults = self.settings.DEFAULTS["ui"]["colors"]
        
        # CheckPrint
        for key, val in defaults["checkprint"].items():
            skey = "ui.colors.checkprint." + key
            self.settings.set(skey, val)
            if skey in self.cp_swatches:
                self.cp_swatches[skey].set_color(val)
        
        # Matching
        for key, val in defaults["matching"].items():
            skey = "ui.colors.matching." + key
            self.settings.set(skey, val)
            if skey in self.match_swatches:
                self.match_swatches[skey].set_color(val)
