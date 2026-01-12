# ui/widgets/toast.py
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QLabel, QGraphicsOpacityEffect

class _ToastLabel(QLabel):
    def __init__(self, parent, text: str):
        # Top-level, transient child of the main window
        super().__init__(parent)
        self.setText(text)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumWidth(160)
        self.setMaximumWidth(320)
        self.setMargin(10)
        self.setFont(QFont(self.font().family(), self.font().pointSize()))
        self.setStyleSheet(
            "QLabel {"
            "  color: #E7ECF4;"
            "  background: rgba(15, 23, 36, 220);"
            "  border: 1px solid #233044;"
            "  border-radius: 10px;"
            "}"
        )
        # Fade effect
        self._eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._eff)
        self._eff.setOpacity(0.0)

    def popup_at(self, parent_widget, margin: int = 20):
        # Size to content first
        self.adjustSize()
        p = parent_widget.window() if parent_widget else self
        if not p:
            return
        # Place bottom-right inside the window
        w = p.width()
        h = p.height()
        tw = self.width()
        th = self.height()
        x = max(0, w - tw - margin)
        y = max(0, h - th - margin)
        self.move(x, y)
        self.show()

    def animate(self, visible_ms=1200):
        # fade in
        fade_in = QPropertyAnimation(self._eff, b"opacity", self)
        fade_in.setDuration(180)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)

        # hold
        def _hold_then_fade():
            QTimer.singleShot(visible_ms, _do_fade_out)

        # fade out
        def _do_fade_out():
            fade_out = QPropertyAnimation(self._eff, b"opacity", self)
            fade_out.setDuration(220)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.InCubic)
            fade_out.finished.connect(self.deleteLater)
            fade_out.start()

        fade_in.finished.connect(_hold_then_fade)
        fade_in.start()

def toast(parent, message: str, msec: int = 1200):
    """
    Show a small floating notice at the bottom-right of the parent window.
    """
    if not parent:
        return
    host = parent.window() if hasattr(parent, "window") else parent
    t = _ToastLabel(host, message)
    t.popup_at(host)
    t.animate(visible_ms=msec)
