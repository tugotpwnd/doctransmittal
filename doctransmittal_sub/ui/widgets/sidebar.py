# widgets/sidebar.py
from pathlib import Path
from typing import Optional, List

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QToolButton, QMenu,
    QAction, QHBoxLayout, QPushButton, QGroupBox, QListWidget, QListWidgetItem,
    QComboBox, QFormLayout
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
        gb_user = QGroupBox("User")
        vb_u = QVBoxLayout(gb_user)
        self.lbl_user = QLabel("—")

        # Filters (kept expanded)
        gb_filters = QGroupBox("Quick Filters")
        vb = QVBoxLayout(gb_filters)

        self.le_search = QLineEdit()
        self.le_search.setPlaceholderText("Search (Doc ID / Type / Desc / Status)")
        self.le_search.textChanged.connect(self._emit_filters)
        vb.addWidget(self.le_search)

        # Status dropdown
        hb = QHBoxLayout()
        self.menu_status = QMenu(self)
        self.menu_status.setMinimumWidth(260)
        self.menu_status.setStyleSheet(
            "QMenu{padding:6px 8px;}"
            "QMenu::item{padding:6px 12px;}"
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

        # Show only selected (toggle)
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

        # Editors for applying to highlighted rows
        form = QFormLayout()
        self.cb_apply_type = QComboBox(self);
        self.cb_apply_type.setEditable(True)
        self.cb_apply_file = QComboBox(self);
        self.cb_apply_file.setEditable(True)
        self.cb_apply_status = QComboBox(self);
        self.cb_apply_status.setEditable(True)

        # Placeholder / no-change option
        _placeholder = "— no change —"
        for _cb in (self.cb_apply_type, self.cb_apply_file, self.cb_apply_status):
            _cb.addItem(_placeholder)

        form.addRow("Type", self.cb_apply_type)
        form.addRow("File type", self.cb_apply_file)
        form.addRow("Status", self.cb_apply_status)
        vb_bulk.addLayout(form)

        # Row of buttons: Apply + "More" dropdown
        row_bulk = QHBoxLayout()
        btn_apply = QPushButton("Apply to highlighted")
        btn_apply.clicked.connect(lambda:
                                  self.bulkApplyRequested.emit(
                                      self.cb_apply_type.currentText().strip(),
                                      self.cb_apply_file.currentText().strip(),
                                      self.cb_apply_status.currentText().strip()
                                  )
                                  )
        row_bulk.addWidget(btn_apply)

        more = QToolButton(self)
        more.setText("More ▾")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        menu.setObjectName("BulkMoreMenu")

        # --- Single import action (batch revisions/descriptions) ---
        act_imp_batch = QAction("Import Revisions/Descriptions…", menu)
        menu.addAction(act_imp_batch)

        menu.addSeparator()

        act_rev_inc = QAction("Revision: increment (selected)", menu)
        act_rev_set = QAction("Revision: set…", menu)
        menu.addAction(act_rev_inc)
        menu.addAction(act_rev_set)

        # wiring
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

        # Project box
        # Push everything above up; keep Project pinned to the bottom
        root.addStretch(1)

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

        # NEW: Templates viewer button
        btn_tpl = QPushButton("Templates…")
        btn_tpl.clicked.connect(self.templatesRequested.emit)
        vb_p.addWidget(btn_tpl)

        root.addWidget(gb_proj)

        # Double-click preset to load
        self.lst_presets.itemDoubleClicked.connect(lambda _: self._on_load_clicked())

        self.set_loaded_preset_hint("")  # none loaded on boot

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

    # widgets/sidebar.py
    def _emit_filters(self):
        search = self.le_search.text()
        # was: statuses = {a.text() for a in self._status_actions if a.isChecked()}
        statuses = [a.text() for a in self._status_actions if a.isChecked()]
        # (optional) deterministic order:
        # statuses = sorted(a.text() for a in self._status_actions if a.isChecked())
        self.filtersChanged.emit(search, statuses)

    def set_apply_option_lists(self, row_options: dict):
        """Fill the 'Row changes' combos with project row options."""

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

    # --- preset button handlers ---

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

     # NEW: called by MainWindow when a DB is opened
    def set_db_path(self, db_path: Path):
        self._db_path = Path(db_path) if db_path else None
        self._refresh_doc_history()

    # NEW: called by RegisterTab whenever blue-highlight selection changes
    def update_doc_history_selection(self, doc_ids: List[str]):
        self._highlighted_docs = [d.strip().upper() for d in (doc_ids or []) if d]
        self._refresh_doc_history()

    def set_loaded_preset_hint(self, name: str):
        title = "Saved Presets" + (f" ({name})" if (name or "").strip() else "")
        try:
            self.sec_presets.setTitle(title)
        except Exception:
            # Fallback if CollapsibleSection API changes
            self.sec_presets.toggle.setText(title)

    def _refresh_doc_history(self):
           """Populate the Doc History list based on blue-highlighted doc_ids.
           Single-doc: show all its transmittals (with date).
           Multi-doc: show only transmittals that contain ALL highlighted docs."""
           n = len(self._highlighted_docs)
           try:
               self.sec_history.setTitle(f"Doc History ({n})")
           except Exception:
               # Fallback for older builds (shouldn't be needed)
               if hasattr(self.sec_history, "toggle"):
                   self.sec_history.toggle.setText(f"Doc History ({n})")
           self.lst_history.clear()

           if not self._db_path or n == 0:
               self.lbl_history.setText("Highlight rows in the register to see history here.")
               return

           # Import here to avoid breaking layouts if package paths differ
           try:
               from ...services.db import get_doc_submission_history
           except Exception:
               try:
                   from ...services.db import get_doc_submission_history
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
                   # Build per-doc map: {transmittal_number: created_on}
                   per_doc = []
                   for did in self._highlighted_docs:
                       rows = get_doc_submission_history(self._db_path, 0, did) or []
                       per_doc.append({(r.get("number") or ""): (r.get("created_on") or "") for r in rows if r.get("number")})
                   common = set(per_doc[0].keys())
                   for d in per_doc[1:]:
                       common &= set(d.keys())
                   # Sort newest first by the first doc's date field
                   common_sorted = sorted(common, key=lambda k: per_doc[0].get(k, ""), reverse=True)
                   self.lbl_history.setText(f"Transmittals common to all {n} documents: {len(common_sorted)}")
                   for num in common_sorted:
                       self.lst_history.addItem(QListWidgetItem(f"{num} — {per_doc[0].get(num, '')}"))
           except Exception as e:
               self.lbl_history.setText(f"History error: {e}")