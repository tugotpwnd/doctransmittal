from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QSpinBox, QDialogButtonBox, QWidget, QSizePolicy
)

class AppearanceDialog(QDialog):
    def __init__(self, theme: str, font_delta: int, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("AppearanceDialog")
        self.setWindowTitle("Appearance")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # --- Theme slider (0=Light, 1=Dark) ---
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        self.theme_slider = QSlider(Qt.Horizontal, self)
        self.theme_slider.setMinimum(0); self.theme_slider.setMaximum(1)
        self.theme_slider.setTickInterval(1)
        self.theme_slider.setTickPosition(QSlider.TicksBelow)
        self.theme_slider.setValue(1 if (theme or "dark").lower() == "dark" else 0)
        theme_row.addWidget(QLabel("Light"))
        theme_row.addWidget(self.theme_slider, 1)
        theme_row.addWidget(QLabel("Dark"))

        # --- Font size spinbox (delta in pt) ---
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Text size offset"))
        self.font_spin = QSpinBox(self)
        self.font_spin.setRange(-6, 8)
        self.font_spin.setSingleStep(1)
        self.font_spin.setValue(int(font_delta or 0))
        self.font_spin.setSuffix(" pt")
        size_row.addWidget(self.font_spin, 0)

        # --- Buttons ---
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel |
            QDialogButtonBox.Apply | QDialogButtonBox.RestoreDefaults,
            parent=self
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.Apply).clicked.connect(self._apply_clicked)
        btns.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._reset_defaults)

        # --- Layout ---
        root = QVBoxLayout(self)
        root.addLayout(theme_row)
        root.addLayout(size_row)
        root.addSpacing(6)
        root.addWidget(btns)

    def values(self):
        theme = "dark" if self.theme_slider.value() == 1 else "light"
        delta = int(self.font_spin.value())
        return theme, delta

    def _apply_clicked(self):
        # Ask parent window (MainWindow) to apply + persist immediately
        parent = self.parent()
        if parent and hasattr(parent, "_apply_appearance_values"):
            parent._apply_appearance_values(*self.values())

    def _reset_defaults(self):
        self.theme_slider.setValue(1)  # dark
        self.font_spin.setValue(0)
