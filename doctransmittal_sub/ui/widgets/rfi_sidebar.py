from __future__ import annotations
from pathlib import Path
from typing import List, Tuple
from collections import Counter

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QLabel, QPushButton,
    QGridLayout, QHBoxLayout, QLineEdit
)
from PyQt5.QtGui import QFont

class Pie(QWidget):
    # (unchanged) ...
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: List[Tuple[str, int]] = []
        self.setMinimumHeight(160)
    def set(self, items: List[Tuple[str, int]]):
        self._items = [(a, int(b)) for a, b in items if int(b) > 0]
        self.update()
    def paintEvent(self, ev):
        from PyQt5.QtGui import QPainter, QColor
        from PyQt5.QtCore import QRectF
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(8, 8, -8, -8); size = min(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        outer = QRectF(cx - size/2, cy - size/2, size, size)
        total = sum(v for _, v in self._items) or 0
        if not total:
            p.setPen(Qt.NoPen); p.setBrush(self.palette().color(self.palette().WindowText)); p.drawEllipse(outer)
            p.setBrush(self.palette().color(self.palette().Window))
            inner = outer.adjusted(size*0.18, size*0.18, -size*0.18, -size*0.18)
            p.drawEllipse(inner)
            p.setPen(self.palette().color(self.palette().WindowText)); f = QFont(p.font()); f.setBold(True); p.setFont(f)
            p.drawText(inner, Qt.AlignCenter, "0"); return
        colors = ["#4F7DFF", "#22C55E", "#E11D48", "#F59E0B"]; p.setPen(Qt.NoPen); start = 90*16
        for i, (_, v) in enumerate(self._items):
            span = int(360*16*(v/total)); p.setBrush(QColor(colors[i % len(colors)])); p.drawPie(outer, start, -span); start -= span
        p.setBrush(self.palette().color(self.palette().Window))
        inner = outer.adjusted(size*0.22, size*0.22, -size*0.22, -size*0.22); p.drawEllipse(inner)
        p.setPen(self.palette().color(self.palette().WindowText)); f = QFont(p.font()); f.setBold(True); p.setFont(f)
        p.drawText(inner, Qt.AlignCenter, str(total))

class RfiSidebarWidget(QWidget):
    projectSettingsRequested = pyqtSignal()
    templatesRequested = pyqtSignal()
    generatePdfRequested = pyqtSignal()
    printRfiRegisterRequested = pyqtSignal()
    printRfiProgressRequested = pyqtSignal()
    searchTextChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self); root.setContentsMargins(10,10,10,10); root.setSpacing(10)

        # === Search (top) ===
        gb_search = QGroupBox("Search RFIs")
        vs = QVBoxLayout(gb_search)
        self.ed_search = QLineEdit(self); self.ed_search.setPlaceholderText("Find in Subject / Background / Request…")
        self.ed_search.textChanged.connect(self.searchTextChanged.emit)
        vs.addWidget(self.ed_search)
        root.addWidget(gb_search)

        # === RFI Actions (top) ===
        gb_actions = QGroupBox("RFI Actions")
        va = QVBoxLayout(gb_actions)
        btn_gen = QPushButton("Re-Generate RFI PDF…")
        btn_gen.clicked.connect(self.generatePdfRequested.emit)
        va.addWidget(btn_gen)
        btn_print_reg = QPushButton("Print RFI Register…")
        btn_print_reg.clicked.connect(self.printRfiRegisterRequested.emit)
        va.addWidget(btn_print_reg)
        root.addWidget(gb_actions)

        # push upper groups up
        root.addStretch(1)

        # === Progress (bottom) ===
        gb_prog = QGroupBox("RFI Progress")
        vp = QVBoxLayout(gb_prog); vp.setSpacing(8)
        self.lbl = QLabel("—"); self.lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.pie = Pie(); self.legend = QWidget(); grid = QGridLayout(self.legend); grid.setContentsMargins(0,0,0,0)
        vp.addWidget(self.lbl); vp.addWidget(self.pie); vp.addWidget(self.legend)
        btn_print_prog = QPushButton("Print RFI Progress…")
        btn_print_prog.clicked.connect(self.printRfiProgressRequested.emit)
        vp.addWidget(btn_print_prog)
        root.addWidget(gb_prog)

        # === Project info (bottom-most) ===
        gb_proj = QGroupBox("Project")
        vpj = QVBoxLayout(gb_proj); vpj.setSpacing(6)
        self.lbl_job = QLabel("Job No: —"); self.lbl_name = QLabel("Name: —")
        vpj.addWidget(self.lbl_job); vpj.addWidget(self.lbl_name)
        btn_proj = QPushButton("Project Settings…"); btn_proj.clicked.connect(self.projectSettingsRequested.emit); vpj.addWidget(btn_proj)
        btn_tmpl = QPushButton("Templates…"); btn_tmpl.clicked.connect(self.templatesRequested.emit); vpj.addWidget(btn_tmpl)
        root.addWidget(gb_proj)

    def set_project_info(self, job_no: str, name: str):
        self.lbl_job.setText(f"Job No: {job_no or '—'}")
        self.lbl_name.setText(f"Name: {name or '—'}")

    def refresh_progress(self, rows: list):
        from collections import Counter
        counts = Counter((r.get("response_status") or "—").strip() for r in (rows or []))
        items = [("Outstanding", counts.get("Outstanding", 0)), ("Closed", counts.get("Closed", 0))]
        self.pie.set(items)
        total = sum(v for _, v in items); self.lbl.setText(f"{total} RFIs — {items[0][1]} Outstanding / {items[1][1]} Closed")
        grid = self.legend.layout()
        while grid.count():
            w = grid.takeAt(0).widget()
            if w: w.deleteLater()
        for i, (label, cnt) in enumerate(items):
            roww = QWidget(); hr = QHBoxLayout(roww); hr.setContentsMargins(0,0,0,0); hr.setSpacing(6)
            sw = QLabel(); sw.setFixedSize(12,12)
            sw.setStyleSheet(f"background: {'#4F7DFF' if i==0 else '#22C55E'}; border-radius:3px;")
            hr.addWidget(sw); hr.addWidget(QLabel(f"{label} — {cnt}"))
            grid.addWidget(roww, i, 0)
