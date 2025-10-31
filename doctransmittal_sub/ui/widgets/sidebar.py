# widgets/sidebar.py
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import hashlib
from collections import Counter

from PyQt5.QtCore import pyqtSignal, Qt, QRectF, QSize
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QPalette
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QToolButton, QMenu,
    QAction, QHBoxLayout, QPushButton, QGroupBox, QListWidget, QListWidgetItem,
    QComboBox, QFormLayout, QGridLayout, QSizePolicy
)

# --- Small helper for collapsible sections -----------------------------------
class CollapsibleSection(QWidget):
    def __init__(self, title: str, collapsed: bool = False, parent=None):
        super().__init__(parent)
        self.toggle = QToolButton(self)
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(not collapsed)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.DownArrow if not collapsed else Qt.RightArrow)
        self.toggle.setStyleSheet("QToolButton{border:none;font-weight:600;padding:2px 0;}")

        self.content = QWidget(self)
        self.content.setVisible(not collapsed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.toggle)
        lay.addWidget(self.content)

        self.toggle.toggled.connect(self._on_toggled)

    def setContentLayout(self, layout):
        self.content.setLayout(layout)

    def _on_toggled(self, checked: bool):
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.content.setVisible(checked)

    def setTitle(self, title: str):
        self.toggle.setText(title)

# --- Tiny, dependency-free donut/pie widget ----------------------------------
class PieChartWidget(QWidget):
    """
    Minimal donut chart. Call set_data([("Issued for Review", 12), ("Approved", 5), ...]).
    Colors are stable per label across runs.
    """
    _PALETTE = [
        "#4F7DFF", "#22C55E", "#F59E0B", "#E11D48", "#14B8A6",
        "#A78BFA", "#F97316", "#06B6D4", "#84CC16", "#EC4899"
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: List[Tuple[str, int]] = []
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)

    def sizeHint(self) -> QSize:
        return QSize(260, 180)

    def _color_for(self, label: str) -> QColor:
        key = (label or "—").encode("utf-8", "ignore")
        idx = int(hashlib.md5(key).hexdigest(), 16) % len(self._PALETTE)
        return QColor(self._PALETTE[idx])

    def set_data(self, items: List[Tuple[str, int]]):
        # items: list of (label, count), ignore zeros
        self._items = [(lbl or "—", int(cnt)) for lbl, cnt in items if int(cnt) > 0]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # background/foreground from palette
        pal = self.palette()
        bg = pal.color(QPalette.Window)
        fg = pal.color(QPalette.WindowText)

        rect = self.rect().adjusted(8, 8, -8, -8)
        size = min(rect.width(), rect.height())
        cx = rect.center().x()
        cy = rect.center().y()
        outer = QRectF(cx - size/2, cy - size/2, size, size)

        total = sum(cnt for _, cnt in self._items) or 0
        if total == 0:
            # Empty ring
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawEllipse(outer)
            p.setBrush(bg)
            p.drawEllipse(outer.adjusted(size*0.18, size*0.18, -size*0.18, -size*0.18))
            # hint text
            p.setPen(fg)
            f = QFont(p.font()); f.setBold(True); p.setFont(f)
            p.drawText(outer, Qt.AlignCenter, "No documents")
            return

        # Draw slices
        start = 90 * 16  # start at 12 o'clock
        p.setPen(Qt.NoPen)
        for label, cnt in self._items:
            span = int(360 * 16 * (cnt / total))
            p.setBrush(self._color_for(label))
            p.drawPie(outer, start, -span)  # clockwise
            start -= span

        # Donut hole
        p.setBrush(bg)
        hole = outer.adjusted(size*0.22, size*0.22, -size*0.22, -size*0.22)
        p.drawEllipse(hole)

        # Center count
        p.setPen(fg)
        f = QFont(p.font()); f.setBold(True); f.setPointSizeF(f.pointSizeF() + 2); p.setFont(f)
        p.drawText(hole, Qt.AlignCenter, str(total))

# --- Sidebar widget -----------------------------------------------------------
class SidebarWidget(QWidget):

    # ==== Signals expected by MainWindow wiring ====
    filtersChanged = pyqtSignal(str, list)                      # search, statuses
    showOnlySelectedToggled = pyqtSignal(bool)
    selectAllRequested = pyqtSignal()
    clearSelectionRequested = pyqtSignal()
    clearAllRequested = pyqtSignal()

    savePresetRequested = pyqtSignal(str)
    loadPresetRequested = pyqtSignal(str)
    unloadPresetRequested = pyqtSignal(str)
    renamePresetRequested = pyqtSignal(str, str)
    deletePresetRequested = pyqtSignal(str)

    bulkApplyRequested = pyqtSignal(str, str, str)              # type, file type, status
    revisionIncrementRequested = pyqtSignal()
    revisionSetRequested = pyqtSignal()
    importBatchRequested = pyqtSignal()
    projectSettingsRequested = pyqtSignal()
    templatesRequested = pyqtSignal()
    printProgressRequested = pyqtSignal()
    printRegisterRequested = pyqtSignal()
    migrateExcelRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status_actions = []
        self._selected_count = 0

        self._db_path: Optional[Path] = None
        self._highlighted_docs: List[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # User box
        gb_user = QGroupBox("User", self)  # parent it to the sidebar
        vb_u = QVBoxLayout(gb_user)
        self.lbl_user = QLabel("—", gb_user)
        vb_u.addWidget(self.lbl_user)

        # ADD THIS LINE so the group stays alive:
        root.addWidget(gb_user)

        # Filters (kept expanded)
        gb_filters = QGroupBox("Quick Filters")
        vb = QVBoxLayout(gb_filters)

        self.le_search = QLineEdit()
        self.le_search.setPlaceholderText("Search (Doc ID / Type / Desc / Status)")
        self.le_search.textChanged.connect(self._emit_filters)
        vb.addWidget(self.le_search)

        hb = QHBoxLayout()
        self.menu_status = QMenu(self)
        self.menu_status.setMinimumWidth(260)
        self.menu_status.setStyleSheet(
            "QMenu{padding:6px 8px;} QMenu::item{padding:6px 12px;}"
        )
        hb.addStretch(1)
        vb.addLayout(hb)

        root.addWidget(gb_filters)

        # Selection (collapsible, default collapsed)
        sec_actions = CollapsibleSection("Selection Utils", collapsed=True, parent=self)
        vb2 = QVBoxLayout()
        b_all = QPushButton("Select ALL (filtered)")
        b_all.clicked.connect(self.selectAllRequested.emit)
        vb2.addWidget(b_all)

        b_clear_filtered = QPushButton("Clear selection (filtered)")
        b_clear_filtered.clicked.connect(self.clearSelectionRequested.emit)
        vb2.addWidget(b_clear_filtered)

        b_clear_all = QPushButton("Clear ALL (all rows)")
        b_clear_all.clicked.connect(self.clearAllRequested.emit)
        vb2.addWidget(b_clear_all)

        self.btn_only_sel = QPushButton("Show only selected")
        self.btn_only_sel.setCheckable(True)
        self.btn_only_sel.toggled.connect(self.showOnlySelectedToggled.emit)
        vb2.addWidget(self.btn_only_sel)

        self.lbl_selected = QLabel("0 selected")
        self.lbl_selected.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vb2.addWidget(self.lbl_selected)

        sec_actions.setContentLayout(vb2)
        root.addWidget(sec_actions)

        # Presets (collapsible, default collapsed)
        self.sec_presets = CollapsibleSection("Saved Presets", collapsed=True, parent=self)
        vp = QVBoxLayout()

        self.lst_presets = QListWidget()
        vp.addWidget(self.lst_presets)

        row = QHBoxLayout()
        self.le_preset_name = QLineEdit()
        self.le_preset_name.setPlaceholderText("Preset name…")
        row.addWidget(self.le_preset_name, 1)
        btn_save = QPushButton("Save As / Overwrite")
        btn_save.clicked.connect(self._on_save_clicked)
        row.addWidget(btn_save)
        vp.addLayout(row)

        row2 = QHBoxLayout()
        btn_load = QPushButton("Load")
        btn_load.clicked.connect(self._on_load_clicked)
        row2.addWidget(btn_load)

        btn_unload = QPushButton("Unload")
        btn_unload.clicked.connect(lambda: self.unloadPresetRequested.emit(self._current_preset_name() or ""))
        row2.addWidget(btn_unload)
        vp.addLayout(row2)

        row3 = QHBoxLayout()
        btn_rename = QPushButton("Rename…")
        btn_rename.clicked.connect(self._on_rename_clicked)
        row3.addWidget(btn_rename)

        btn_delete = QPushButton("Delete")
        btn_delete.clicked.connect(self._on_delete_clicked)
        row3.addWidget(btn_delete)

        vp.addLayout(row3)

        self.sec_presets.setContentLayout(vp)
        root.addWidget(self.sec_presets)

        # Row changes (collapsible, default collapsed)
        sec_bulk = CollapsibleSection("Batch changes", collapsed=True, parent=self)
        vb_bulk = QVBoxLayout()

        form = QFormLayout()
        self.cb_apply_type = QComboBox(self);  self.cb_apply_type.setEditable(True)
        self.cb_apply_file = QComboBox(self);  self.cb_apply_file.setEditable(True)
        self.cb_apply_status = QComboBox(self);self.cb_apply_status.setEditable(True)
        _placeholder = "— no change —"
        for _cb in (self.cb_apply_type, self.cb_apply_file, self.cb_apply_status):
            _cb.addItem(_placeholder)

        form.addRow("Type", self.cb_apply_type)
        form.addRow("File type", self.cb_apply_file)
        form.addRow("Status", self.cb_apply_status)
        vb_bulk.addLayout(form)

        row_bulk = QHBoxLayout()
        btn_apply = QPushButton("Apply to highlighted")
        btn_apply.clicked.connect(lambda:
                                  self.bulkApplyRequested.emit(
                                      self.cb_apply_type.currentText().strip(),
                                      self.cb_apply_file.currentText().strip(),
                                      self.cb_apply_status.currentText().strip()
                                  ))
        row_bulk.addWidget(btn_apply)

        more = QToolButton(self)
        more.setText("More ▾")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more); menu.setObjectName("BulkMoreMenu")

        act_imp_batch = QAction("Import Revisions/Descriptions…", menu); menu.addAction(act_imp_batch)
        menu.addSeparator()
        act_rev_inc = QAction("Revision: increment (selected)", menu)
        act_rev_set = QAction("Revision: set…", menu)
        menu.addAction(act_rev_inc); menu.addAction(act_rev_set)
        menu.addSeparator()
        act_migrate_excel = QAction("Migrate Excel Register…", menu)
        menu.addAction(act_migrate_excel)
        act_migrate_excel.triggered.connect(self.migrateExcelRequested.emit)

        act_imp_batch.triggered.connect(self.importBatchRequested.emit)
        act_rev_inc.triggered.connect(self.revisionIncrementRequested.emit)
        act_rev_set.triggered.connect(self.revisionSetRequested.emit)

        more.setMenu(menu)
        row_bulk.addWidget(more)

        vb_bulk.addLayout(row_bulk)
        sec_bulk.setContentLayout(vb_bulk)
        root.addWidget(sec_bulk)

        # Doc history (collapsible, default collapsed)
        self.sec_history = CollapsibleSection("Doc History (0)", collapsed=True, parent=self)
        vb_h = QVBoxLayout()
        self.lbl_history = QLabel("Highlight rows to see transmittal history.")
        self.lst_history = QListWidget()
        vb_h.addWidget(self.lbl_history)
        vb_h.addWidget(self.lst_history)
        self.sec_history.setContentLayout(vb_h)
        root.addWidget(self.sec_history)

        # Push everything above up; keep Progress + Project pinned near the bottom
        root.addStretch(1)

        # === NEW: Progress (status breakdown) ===
        gb_prog = QGroupBox("Progress")
        vb_prog = QVBoxLayout(gb_prog); vb_prog.setSpacing(8)
        self.lbl_prog = QLabel("—")
        self.lbl_prog.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.pie = PieChartWidget()
        self._legend = QWidget(); self._legend.setLayout(QGridLayout())
        self._legend.layout().setContentsMargins(0, 0, 0, 0)
        self._legend.layout().setSpacing(6)
        vb_prog.addWidget(self.lbl_prog)
        vb_prog.addWidget(self.pie)
        vb_prog.addWidget(self._legend)
        root.addWidget(gb_prog)

        vb_prog.setContentsMargins(8,8,8,8); vb_prog.setSpacing(6)
        self.btn_print_progress = QPushButton("Print Progress…", gb_prog)
        self.btn_print_progress.setToolTip("Export a Progress Tracker PDF with chart and full document list")
        self.btn_print_progress.clicked.connect(self.printProgressRequested.emit)
        vb_prog.addWidget(self.btn_print_progress)

        # in the Progress group setup (right after self.btn_print_progress):
        self.btn_print_register = QPushButton("Print Register…", gb_prog)
        self.btn_print_register.setToolTip("Export a client-friendly Document Register PDF (landscape)")
        self.btn_print_register.clicked.connect(self.printRegisterRequested.emit)
        vb_prog.addWidget(self.btn_print_register)

        # Project box (kept expanded)
        gb_proj = QGroupBox("Project")
        vb_p = QVBoxLayout(gb_proj)
        self.lbl_job = QLabel("Job No: —")
        self.lbl_proj = QLabel("Name: —")
        vb_p.addWidget(self.lbl_job)
        vb_p.addWidget(self.lbl_proj)

        btn_proj = QPushButton("Project Settings…")
        btn_proj.clicked.connect(self.projectSettingsRequested.emit)
        vb_p.addWidget(btn_proj)

        btn_tpl = QPushButton("Templates…")
        btn_tpl.clicked.connect(self.templatesRequested.emit)
        vb_p.addWidget(btn_tpl)

        root.addWidget(gb_proj)

        self.lst_presets.itemDoubleClicked.connect(lambda _: self._on_load_clicked())
        self.set_loaded_preset_hint("")

    # --- setters / helpers ---

    def set_project_info(self, job_no: str, project_name: str):
        self.lbl_job.setText(f"Job No: {job_no or '—'}")
        self.lbl_proj.setText(f"Name: {project_name or '—'}")

    def set_user_name(self, name: str):
        self.lbl_user.setText(name or "—")

    def set_selected_count(self, n: int):
        self._selected_count = max(0, int(n))
        self.lbl_selected.setText(f"{self._selected_count} selected")

    def set_preset_names(self, names):
        self.lst_presets.clear()
        for n in sorted(names):
            self.lst_presets.addItem(QListWidgetItem(n))

    def _current_preset_name(self) -> str:
        it = self.lst_presets.currentItem()
        return it.text().strip() if it else ""

    def _emit_filters(self):
        search = self.le_search.text()
        statuses = [a.text() for a in self._status_actions if a.isChecked()]
        self.filtersChanged.emit(search, statuses)

    def set_apply_option_lists(self, row_options: dict):
        def _fill(cb: QComboBox, items):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("— no change —")
            for it in (items or []): cb.addItem(it)
            cb.setCurrentIndex(0)
            cb.blockSignals(False)
        _fill(self.cb_apply_type, (row_options or {}).get("doc_types"))
        _fill(self.cb_apply_file, (row_options or {}).get("file_types"))
        _fill(self.cb_apply_status, (row_options or {}).get("statuses"))

    def _on_save_clicked(self):
        name = self.le_preset_name.text().strip()
        if name:
            self.savePresetRequested.emit(name)

    def _on_load_clicked(self):
        name = self._current_preset_name()
        if name:
            self.loadPresetRequested.emit(name)

    def _on_rename_clicked(self):
        old = self._current_preset_name()
        new = self.le_preset_name.text().strip()
        if old and new:
            self.renamePresetRequested.emit(old, new)

    def _on_delete_clicked(self):
        name = self._current_preset_name()
        if name:
            self.deletePresetRequested.emit(name)

    # called by MainWindow when a DB is opened
    def set_db_path(self, db_path: Path):
        self._db_path = Path(db_path) if db_path else None
        self._refresh_doc_history()
        self.refresh_progress()

    # called by RegisterTab whenever blue-highlight selection changes
    def update_doc_history_selection(self, doc_ids: List[str]):
        self._highlighted_docs = [d.strip().upper() for d in (doc_ids or []) if d]
        self._refresh_doc_history()

    def set_loaded_preset_hint(self, name: str):
        title = "Saved Presets" + (f" ({name})" if (name or "").strip() else "")
        try:
            self.sec_presets.setTitle(title)
        except Exception:
            self.sec_presets.toggle.setText(title)

    def _refresh_doc_history(self):
        n = len(self._highlighted_docs)
        try:
            self.sec_history.setTitle(f"Doc History ({n})")
        except Exception:
            if hasattr(self.sec_history, "toggle"):
                self.sec_history.toggle.setText(f"Doc History ({n})")
        self.lst_history.clear()

        if not self._db_path or n == 0:
            self.lbl_history.setText("Highlight rows in the register to see history here.")
            return

        try:
            # Try dotted imports like elsewhere in the project
            try:
                from ...services.db import get_doc_submission_history
            except Exception:
                from ..services.db import get_doc_submission_history
        except Exception as e:
            self.lbl_history.setText(f"Import error: {e}")
            return

        try:
            if n == 1:
                did = self._highlighted_docs[0]
                rows = get_doc_submission_history(self._db_path, 0, did) or []
                self.lbl_history.setText(f"History for {did}: {len(rows)} transmittal(s)")
                for r in rows:
                    num = (r.get("number") or "")
                    date = (r.get("created_on") or "")
                    rev = r.get("revision")
                    txt = f"{num} — {date}" + (f"  (Rev {rev})" if rev not in (None, "", "—") else "")
                    self.lst_history.addItem(QListWidgetItem(txt))
            else:
                per_doc = []
                # Build per-doc map: {transmittal_number: created_on}
                for did in self._highlighted_docs:
                    rows = get_doc_submission_history(self._db_path, 0, did) or []
                    per_doc.append({(r.get("number") or ""): (r.get("created_on") or "") for r in rows if r.get("number")})
                common = set(per_doc[0].keys())
                for d in per_doc[1:]:
                    common &= set(d.keys())
                common_sorted = sorted(common, key=lambda k: per_doc[0].get(k, ""), reverse=True)
                self.lbl_history.setText(f"Transmittals common to all {n} documents: {len(common_sorted)}")
                for num in common_sorted:
                    self.lst_history.addItem(QListWidgetItem(f"{num} — {per_doc[0].get(num, '')}"))
        except Exception as e:
            self.lbl_history.setText(f"History error: {e}")

    # === NEW: progress (status breakdown) ====================================
    def refresh_progress(self):
        """Recompute and redraw the pie + legend for active documents."""
        if not self._db_path:
            self.pie.set_data([])
            self._set_legend([])
            self.lbl_prog.setText("—")
            return
        try:
            try:
                from ...services.db import get_project, list_documents_with_latest
            except Exception:
                from ..services.db import get_project, list_documents_with_latest
            proj = get_project(self._db_path)
            if not proj:
                self.pie.set_data([]); self._set_legend([]); self.lbl_prog.setText("—"); return
            rows = list_documents_with_latest(self._db_path, proj["id"], state="active") or []
        except Exception:
            rows = []

        counts = Counter((r.get("status") or "—").strip() for r in rows)
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(c for _, c in items)
        self.lbl_prog.setText(f"{total} active documents")
        self.pie.set_data(items)
        self._set_legend(items)

    def _set_legend(self, items: List[Tuple[str, int]]):
        grid: QGridLayout = self._legend.layout()
        # Clear existing
        while grid.count():
            w = grid.takeAt(0).widget()
            if w:
                w.deleteLater()

        if not items:
            return

        # Build simple 2-column legend if many items
        cols = 1 if len(items) <= 5 else 2
        r = c = 0
        for label, cnt in items:
            swatch = QLabel(); swatch.setFixedSize(12, 12)
            # Match pie color
            color = PieChartWidget._PALETTE[int(hashlib.md5((label or '—').encode('utf-8')).hexdigest(), 16) % len(PieChartWidget._PALETTE)]
            swatch.setStyleSheet(f"background:{color}; border-radius:3px;")
            txt = QLabel(f"{label or '—'} — {cnt}")
            txt.setToolTip(f"{cnt} document(s)")
            roww = QWidget(); hl = QHBoxLayout(roww); hl.setContentsMargins(0,0,0,0); hl.setSpacing(6)
            hl.addWidget(swatch); hl.addWidget(txt, 1)
            grid.addWidget(roww, r, c)
            r += 1
            if r >= (len(items)+1)//cols:
                r = 0; c += 1
