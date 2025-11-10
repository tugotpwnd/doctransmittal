# ui/register_tab.py — RegisterTab (v3)
# - Robust against missing model helpers (no selected_items / set_rows required)
# - Dropdown editors for Type / File Type / Status / Description, text for Latest Rev
# - Bulk Apply for highlighted rows (Shift/Ctrl selection)
# - Revisions: Increment by mode or Set specific value
# - Doc ID locked (read-only)
# - Areas + New Document flow compatible with AddDocumentDialog

from __future__ import annotations
from pathlib import Path
import re
import string
from typing import List, Optional, Dict, Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
    QLineEdit, QTableView, QMessageBox, QInputDialog, QComboBox,
    QStyledItemDelegate, QHeaderView, QCheckBox, QDialog, QTableWidgetItem, QTableWidget, QDialogButtonBox,
    QListWidgetItem, QListWidget
)

# ---- Local imports (adjust if your package layout differs) ------------------
from doctransmittal_sub.core.settings import SettingsManager
from .widgets.filter_proxy import RegisterFilterProxy
from .widgets.register_model import RegisterTableModel
from .row_attributes_editor import RowAttributesEditor, DEFAULT_ROW_OPTIONS
from .add_document_dialog import AddDocumentDialog
from .manage_areas_dialog import ManageAreasDialog
from ..services.db import (
    init_db, get_project, upsert_project,
    list_documents_with_latest, update_document_fields,
    add_revision_by_docid, list_statuses_for_project,
    list_areas, upsert_area, delete_area, bulk_update_docs,
    get_row_options, set_row_options,               # NEW
    list_presets, get_preset_doc_ids, save_preset,  # NEW
    rename_preset as db_rename_preset, delete_preset as db_delete_preset,
    get_doc_submission_history, rename_document_id,# NEW
)
from .widgets.register_model import RegisterTableModel  # for column constants
from .widgets.register_model import RegisterTableModel as RM
from .widgets.register_model import QModelIndex  # type hints only
from .widgets.register_model import Qt as _Qt  # avoid naming clash
from .widgets.toast import toast
from PyQt5.QtCore import Qt, pyqtSignal, QTimer



_ALPHA = string.ascii_uppercase
DB_LAST_KEY = "last.db_path"
KEEP_VALUE = "— no change —"
WIDTHS_SETTINGS_KEY = "ui.tables.register.widths"


# Regex to extract a displayable token from a revision string (e.g., "Rev A" -> "A")
_REV_TOKEN_RE = re.compile(r"(?i)(?:rev\s*)?([A-Za-z]+\d*|\d+[A-Za-z]*|[A-Za-z]|\d+)$")

# --- Revision helpers (alpha / numeric / alphanumeric) -----------------------
def _alpha_next(tok: str) -> str:
    s = (tok or "").strip().upper()
    if not s or not s.isalpha():
        s = _parse_latest_token(s)
        s = (s or "A").upper() if s.isalpha() else "A"
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)   # A=1..Z=26
    n += 1
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out

def _alpha_prev(tok: str) -> str:
    s = (tok or "").strip().upper()
    if not s or not s.isalpha():
        s = _parse_latest_token(s).upper()
        if not s.isalpha():
            return ""
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    if n <= 1:
        return "A"                     # clamp at A
    n -= 1
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out

def _numeric_next(tok: str) -> str:
    try:
        return str(int(str(tok).strip()) + 1)
    except Exception:
        return "1"

def _numeric_prev(tok: str) -> str:
    try:
        v = int(str(tok).strip())
        return str(max(0, v - 1))
    except Exception:
        return "0"

def _alphanum_next(tok: str) -> str:
    s = (tok or "").strip().upper()
    import re as _re
    m = _re.match(r"^(\d+)([A-Z]+)$", s)
    if m:
        num, let = m.groups()
        nxt = _alpha_next(let)
        if len(nxt) > len(let):        # e.g. 1Z -> 2A
            return f"{int(num)+1}A"
        return f"{num}{nxt}"
    m = _re.match(r"^([A-Z]+)(\d+)$", s)
    if m:
        let, num = m.groups()
        return f"{let}{int(num)+1}"
    # fallback: if mixed but weird, bump alpha part
    return _alpha_next(s)

def _alphanum_prev(tok: str) -> str:
    s = (tok or "").strip().upper()
    import re as _re
    m = _re.match(r"^(\d+)([A-Z]+)$", s)
    if m:
        num, let = m.groups()
        if let != "A":
            return f"{num}{_alpha_prev(let)}"
        return f"{max(0,int(num)-1)}Z" if int(num) > 0 else "A"
    m = _re.match(r"^([A-Z]+)(\d+)$", s)
    if m:
        let, num = m.groups()
        return f"{let}{max(0,int(num)-1)}"
    # fallback
    return _alpha_prev(s)


def _parse_latest_token(raw: Optional[str]) -> str:
    if not raw:
        return ""
    m = _REV_TOKEN_RE.search(str(raw).strip())
    return m.group(1).upper() if m else str(raw).strip()

# ---- Column index helpers (don’t assume the model has our exact numbers) ----
def _col(name: str, default: int) -> int:
    return getattr(RegisterTableModel, name, default)

COL_SELECT       = _col('COL_SELECT', 0)
COL_DOC_ID       = _col('COL_DOC_ID', 1)
COL_TYPE         = _col('COL_TYPE', 2)
COL_FILETYPE     = _col('COL_FILETYPE', 3)
COL_DESCRIPTION  = _col('COL_DESCRIPTION', 4)  # NEW
COL_STATUS       = _col('COL_STATUS', 5)
COL_LATEST_REV   = _col('COL_LATEST_REV', 6)
COL_COMMENTS     = _col('COL_COMMENTS', 7)


# ---- Delegates ---------------------------------------------------------------
from PyQt5.QtWidgets import QComboBox, QLineEdit

from PyQt5.QtWidgets import QTextEdit

class MultiLineDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        te = QTextEdit(parent)
        te.setWordWrapMode(te.wordWrapMode())  # keep Qt defaults
        te.setAcceptRichText(False)
        te.setPlaceholderText("Add comments…")
        te.setMinimumHeight(80)
        return te
    def setEditorData(self, editor, index):
        editor.setPlainText(str(index.data() or ""))
    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.EditRole)


class ComboDelegate(QStyledItemDelegate):
    def __init__(self, options_provider: Callable[[], List[str]], editable=False, parent=None):
        super().__init__(parent)
        self._opts = options_provider
        self._editable = editable

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(self._editable)
        cb.setInsertPolicy(QComboBox.NoInsert)
        cb.addItems(self._opts())
        return cb

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data() or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

class LineDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        le = QLineEdit(parent)
        le.setMaxLength(16)
        return le
    def setEditorData(self, editor, index):
        editor.setText(str(index.data() or ""))
    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), Qt.EditRole)


# -------------------- RegisterTab --------------------

# ---- Main widget -------------------------------------------------------------
class RegisterTab(QWidget):
    statusesReady = pyqtSignal(list)
    highlightedDocIdsChanged = pyqtSignal(list)
    presetsReady = pyqtSignal(list)
    selectionCountChanged = pyqtSignal(int)
    projectInfoReady = pyqtSignal(str, str, object, object)
    rowOptionsReady = pyqtSignal(dict)  # emits {"doc_types":[...], "file_types":[...], "statuses":[...]}
    matchingPresetChanged = pyqtSignal(str)  # emits "" when no exact match

    def __init__(self, settings: SettingsManager, on_proceed, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.on_proceed = on_proceed

        self.db_path: Optional[Path] = None
        self.project_id: Optional[int] = None
        self.project_root: Optional[Path] = None
        self.project_code: Optional[str] = None

        self.row_options: Dict[str, List[str]] = DEFAULT_ROW_OPTIONS.copy()
        self._rev_mode_idx = 0  # 0=Alpha, 1=AlphaNumeric, 2=Numeric (used when cb_rev_mode is not present)

        lay = QVBoxLayout(self)

        # --- OLD (comment out) ---
        # top = QHBoxLayout()
        # self.le_db = QLineEdit(self)
        # self.le_db.setPlaceholderText("Select Project Database (*.db)")
        # btn_browse = QPushButton("Open…", self); btn_browse.clicked.connect(self._browse_db)
        # btn_new = QPushButton("New…", self); btn_new.clicked.connect(self._new_db)
        # top.addWidget(QLabel("Database:")); top.addWidget(self.le_db, 1); top.addWidget(btn_browse); top.addWidget(btn_new)
        # lay.addLayout(top)

        # --- NEW: same controls, wrapped so MainWindow can hide the entire row ---
        top = QHBoxLayout()
        self.le_db = QLineEdit(self)
        self.le_db.setPlaceholderText("Select Project Database (*.db)")
        btn_browse = QPushButton("Open…", self);
        btn_browse.clicked.connect(self._browse_db)
        btn_new = QPushButton("New…", self);
        btn_new.clicked.connect(self._new_db)
        top.addWidget(QLabel("Database:"));
        top.addWidget(self.le_db, 1);
        top.addWidget(btn_browse);
        top.addWidget(btn_new)
        self.db_row = QWidget(self);
        self.db_row.setLayout(top)
        lay.addWidget(self.db_row)

        self.le_db.returnPressed.connect(self._load_db)
        self.le_db.editingFinished.connect(self._maybe_load_on_edit)

        # Row 2: Minimal toolbar (only the buttons you want left on top)
        tools = QHBoxLayout()
        self.btn_add = QPushButton("New Document", self);
        self.btn_add.clicked.connect(self._add_document)
        tools.addWidget(self.btn_add)

        tools.addStretch(1)
        lay.addLayout(tools)

        # Table
        self.table = QTableView(self)
        self.model = RegisterTableModel([])
        self.proxy = RegisterFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)

        # --- NEW: debounce timer for persisting column widths
        self._widths_timer = QTimer(self)
        self._widths_timer.setSingleShot(True)
        self._widths_timer.timeout.connect(self._save_column_widths)

        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.sectionResized.connect(lambda *_: self._widths_timer.start(300))
        hdr.sectionCountChanged.connect(lambda *_: self._widths_timer.start(300))

        # Add this assert temporarily to confirm we have the real class
        assert type(self.model).__name__ == "RegisterTableModel", type(self.model)
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.selectionModel().selectionChanged.connect(self._on_view_selection_changed)
        lay.addWidget(self.table, 1)
        self._apply_default_column_widths()  # <-- add this line




        # Option providers for delegates
        def _opts_doc_types():
            return list(self.row_options.get("doc_types", []))
        def _opts_file_types():
            return list(self.row_options.get("file_types", []))
        def _opts_statuses():
            return list(self.row_options.get("statuses", []))
        def _opts_descriptions():
            vals = {(getattr(r, 'description', '') or '').strip() for r in getattr(self.model, '_rows', []) if getattr(r, 'description', None)}
            return sorted(vals)

        # Delegates
        self.table.setItemDelegateForColumn(COL_TYPE,        ComboDelegate(_opts_doc_types,   editable=False, parent=self))
        self.table.setItemDelegateForColumn(COL_FILETYPE,    ComboDelegate(_opts_file_types,  editable=False, parent=self))
        self.table.setItemDelegateForColumn(COL_STATUS,      ComboDelegate(_opts_statuses,    editable=False, parent=self))
        self.table.setItemDelegateForColumn(COL_DESCRIPTION, ComboDelegate(_opts_descriptions,editable=True,  parent=self))
        self.table.setItemDelegateForColumn(COL_LATEST_REV,  LineDelegate(self))
        self.table.setItemDelegateForColumn(COL_COMMENTS, MultiLineDelegate(self))  # NEW
        self.table.setEditTriggers(QTableView.DoubleClicked | QTableView.SelectedClicked | QTableView.EditKeyPressed)

        # Make long text wrap and rows auto-size
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)  # don’t shorten with "…"
        vh = self.table.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.ResizeToContents)  # auto height from contents

        # When the user resizes a column, reflow rows (keeps wrapping accurate)
        self.table.horizontalHeader().sectionResized.connect(
            lambda *_: self.table.resizeRowsToContents()
        )

        # Model persistence callbacks if supported
        if hasattr(self.model, 'set_save_callbacks'):
            try:
                self.model.set_save_callbacks(
                    save_fields=lambda doc_id, fields: update_document_fields(self.db_path, self.project_id, doc_id, fields),
                    add_revision=lambda doc_id, rev: add_revision_by_docid(self.db_path, self.project_id, doc_id, rev),
                    rename_doc_id=lambda old, new: rename_document_id(self.db_path, self.project_id, old, new)

                )
            except Exception:
                pass

        # --- after self.model is constructed and callbacks wired ---
        self.model.renameRejected.connect(self._on_docid_rename_rejected)

        # Footer
        bottom = QHBoxLayout()

        # LEFT side (deleted controls)
        self.chk_show_deleted = QCheckBox("Show deleted", self)
        self.chk_show_deleted.setToolTip("Show only soft-deleted documents")
        self.chk_show_deleted.toggled.connect(self._reload_rows)

        self.btn_delete = QPushButton("Delete Selected…", self)
        self.btn_delete.setToolTip("Soft delete: set is_active=0")
        self.btn_delete.clicked.connect(self._delete_selected)

        self.btn_restore = QPushButton("Restore Selected…", self)
        self.btn_restore.setToolTip("Restore: set is_active=1")
        self.btn_restore.clicked.connect(self._restore_selected)

        bottom.addWidget(self.chk_show_deleted)
        bottom.addSpacing(8)
        bottom.addWidget(self.btn_delete)
        bottom.addSpacing(6)
        bottom.addWidget(self.btn_restore)

        # Spacer to push Proceed to the right
        bottom.addStretch(1)

        # RIGHT side (proceed)
        self.btn_proceed = QPushButton("Proceed → Build Transmittal", self)
        self.btn_proceed.clicked.connect(self._proceed_clicked)
        bottom.addWidget(self.btn_proceed)

        lay.addLayout(bottom)

        # Hooks
        self.model.dataChanged.connect(self._on_model_changed)
        self.model.modelReset.connect(self._on_model_changed)
        # View-level persistence (only used if model lacks callbacks)
        self.table.model().dataChanged.connect(self._on_cell_edited)


        # last db
        last_db = self.settings.get(DB_LAST_KEY, "")
        if last_db:
            self.le_db.setText(last_db)

        # Areas cache
        self._areas_cache: List[tuple[str, str]] = []
        self._reload_areas()

    def hide_db_controls(self, hide: bool = True) -> None:
        try:
            self.db_row.setVisible(not hide)
        except Exception:
            pass

    def load_db_from_path(self, path: str) -> None:
        self.le_db.setText(str(path or "").strip())
        self._load_db()

    def _on_docid_rename_rejected(self, message: str) -> None:
        # Optional: ensure the table keeps focus on the editing cell
        view = self.table  # or whatever your QTableView is named
        # Show a blocking warning; you can swap to a non-blocking tooltip if you prefer
        QMessageBox.warning(self, "Cannot Rename Document", message)

    def _restore_selected(self):
        if not (self.db_path and self.project_id is not None):
            QMessageBox.information(self, "Project", "Open a project database first.");
            return

        sel = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel:
            QMessageBox.information(self, "Select rows", "Highlight one or more rows, then try again.")
            return

        rows = getattr(self.model, '_rows', [])
        doc_ids = []
        for vix in sel:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                did = getattr(rows[srow], 'doc_id', '').strip()
                if did: doc_ids.append(did)
        doc_ids = list(dict.fromkeys(doc_ids))
        if not doc_ids: return

        n = len(doc_ids)
        resp = QMessageBox.question(
            self, "Restore Documents",
            f"Restore {n} document(s) (set is_active=1)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if resp != QMessageBox.Yes: return

        for did in doc_ids:
            update_document_fields(self.db_path, self.project_id, did, {"is_active": 1})

        self._reload_rows()
        self.proxy.invalidateFilter()
        self._recount()
        QMessageBox.information(self, "Restored", f"Restored {n} document(s).")


    # ----------------- Delete and restore helpers ---------------

    def _delete_selected(self):
        if not (self.db_path and self.project_id is not None):
            QMessageBox.information(self, "Project", "Open a project database first.");
            return

        sel = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel:
            QMessageBox.information(self, "Select rows", "Single-click to highlight one or more rows, then try again.")
            return

        rows = getattr(self.model, '_rows', [])
        doc_ids = []
        for vix in sel:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                did = getattr(rows[srow], 'doc_id', '').strip()
                if did: doc_ids.append(did)

        doc_ids = list(dict.fromkeys(doc_ids))
        if not doc_ids:
            QMessageBox.information(self, "Nothing selected", "No valid rows selected.");
            return

        n = len(doc_ids)
        resp = QMessageBox.question(
            self,
            "Delete Documents",
            f"Delete {n} selected document(s) from this project?\n\n"
            "This is a soft delete (they can be restored later).",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return

        # Soft delete = set is_active=0 (permitted by update_document_fields)
        for did in doc_ids:
            update_document_fields(self.db_path, self.project_id, did, {"is_active": 0})

        self._reload_rows()
        self.proxy.invalidateFilter()
        self._recount()
        QMessageBox.information(self, "Deleted", f"Moved {n} document(s) to inactive.")

    def _restore_selected(self):
        if not (self.db_path and self.project_id is not None):
            QMessageBox.information(self, "Project", "Open a project database first.");
            return

        sel = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel:
            QMessageBox.information(self, "Select rows", "Highlight one or more rows, then try again.")
            return

        rows = getattr(self.model, '_rows', [])
        doc_ids = []
        for vix in sel:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                did = getattr(rows[srow], 'doc_id', '').strip()
                if did: doc_ids.append(did)
        doc_ids = list(dict.fromkeys(doc_ids))
        if not doc_ids: return

        n = len(doc_ids)
        resp = QMessageBox.question(
            self, "Restore Documents",
            f"Restore {n} document(s) (set is_active=1)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if resp != QMessageBox.Yes: return

        for did in doc_ids:
            update_document_fields(self.db_path, self.project_id, did, {"is_active": 1})

        self._reload_rows()
        self.proxy.invalidateFilter()
        self._recount()
        QMessageBox.information(self, "Restored", f"Restored {n} document(s).")

    # ----------------- Helper for setting default col widths ---------------
    def _apply_default_column_widths(self):
        """
        Load saved widths from settings, apply them.
        Any columns not found fall back to sensible hard defaults.
        """
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)

        # Hard defaults (your existing numbers)
        defaults = {
            COL_SELECT: 28,
            COL_DOC_ID: 200,
            COL_TYPE: 160,
            COL_FILETYPE: 160,
            COL_DESCRIPTION: 800,
            COL_COMMENTS: 380,  # NEW
            COL_STATUS: 200,
            COL_LATEST_REV: 160,
        }

        # Pull saved widths (dict of str/int -> int)
        saved = self.settings.get(WIDTHS_SETTINGS_KEY, {}) or {}

        # Apply saved first
        try:
            count = header.count()
        except Exception:
            count = max(defaults) + 1

        for col in range(count):
            w = saved.get(str(col))
            if isinstance(w, int) and w > 0:
                try:
                    self.table.setColumnWidth(col, w)
                except Exception:
                    pass

        # Fill any missing with defaults
        for col, w in defaults.items():
            try:
                if self.table.columnWidth(col) <= 0:
                    self.table.setColumnWidth(col, int(w))
            except Exception:
                pass

    def _save_column_widths(self):
        """Persist current logical column widths to settings.json."""
        try:
            header = self.table.horizontalHeader()
            count = header.count()
            data = {}
            for col in range(count):
                try:
                    data[str(col)] = int(header.sectionSize(col))
                except Exception:
                    pass
            self.settings.set(WIDTHS_SETTINGS_KEY, data)
        except Exception:
            # don't let UI crash on any failure
            pass

    # ----------------- Helpers for ticked rows (model-agnostic) ---------------
    def _model_has(self, name: str) -> bool:
        return hasattr(self.model, name)

    def _ticked_items(self):
        # Preferred: model.selected_items()
        if self._model_has('selected_items'):
            try:
                return list(self.model.selected_items())
            except Exception:
                pass
        # Fallback: private flags
        rows = getattr(self.model, '_rows', [])
        flags = getattr(self.model, '_selected', None)
        if isinstance(flags, list) and len(flags) == len(rows):
            return [r for r, s in zip(rows, flags) if s]
        # Last resort: highlighted rows
        out = []
        try:
            sel = self.table.selectionModel().selectedRows(COL_DOC_ID)
            for view_idx in sel:
                srow = self.proxy.mapToSource(view_idx).row()
                if 0 <= srow < len(rows):
                    out.append(rows[srow])
        except Exception:
            pass
        return out

    def _set_ticked_ids(self, ids: set[str]):
        if self._model_has('set_selected_doc_ids'):
            try:
                self.model.set_selected_doc_ids(set(ids)); return
            except Exception:
                pass
        rows = getattr(self.model, '_rows', [])
        if hasattr(self.model, '_selected') and isinstance(self.model._selected, list):
            self.model._selected = [(getattr(r, 'doc_id', '') in ids) for r in rows]
            if rows:
                tl = self.model.index(0, 0)
                br = self.model.index(len(rows)-1, 0)
                self.model.dataChanged.emit(tl, br, [Qt.CheckStateRole])

    def _clear_all_ticks(self):
        if self._model_has('clear_all_selection'):
            try:
                self.model.clear_all_selection(); return
            except Exception:
                pass
        if hasattr(self.model, '_selected') and isinstance(self.model._selected, list):
            self._set_ticked_ids(set())

    # ----------------- Sidebar hooks ------------------------------------------
    def apply_filters(self, search: str = "", statuses=None):
        self.proxy.set_search_text(search or "")
        self.proxy.set_statuses(set(statuses or []))

    def set_only_selected_filter(self, on: bool):
        self.proxy.set_only_selected(bool(on))

    def select_all_in_view(self):
        self._set_check_for_filtered(True)

    def clear_selection_in_view(self):
        self._set_check_for_filtered(False)

    def clear_selection_all(self):
        self._clear_all_ticks()
        self._recount()

    # ----------------- Presets API -------------------------------------------
    def save_preset_as(self, name: str):
        name = (name or "").strip()
        if not name:
            QMessageBox.information(self, "Presets", "Please provide a preset name.");
            return
        if not self.db_path:
            QMessageBox.information(self, "Presets", "Open a project database first.");
            return

        ids = [getattr(r, 'doc_id', '') for r in self._ticked_items()]
        ids = [i for i in ids if i]
        if not ids:
            QMessageBox.information(self, "Presets", "No documents are ticked.");
            return

        # Normalize current selection (case/space-insensitive)
        cur_set = {(i or "").strip().upper() for i in ids}

        # Check against existing presets for an exact match
        try:
            existing_names = list_presets(self.db_path, self.project_id) or []
        except Exception:
            existing_names = []

        matched_name = ""
        for nm in existing_names:
            try:
                nm_ids = get_preset_doc_ids(self.db_path, self.project_id, (nm or "").strip()) or []
            except Exception:
                nm_ids = []
            if {(x or "").strip().upper() for x in nm_ids} == cur_set:
                matched_name = nm
                break

        if matched_name:
            # Tell the user and stop — nothing to save
            QMessageBox.information(
                self, "Preset already exists",
                f"These items are already saved as preset “{matched_name}”."
            )
            # Nice UX: show quick toast + update the (Saved Presets (name)) hint
            self._toast(f"Already saved as “{matched_name}”")
            # If you added the matchingPresetChanged signal earlier:
            try:
                self.matchingPresetChanged.emit(matched_name)
            except Exception:
                pass
            return

        # Overwrite guard if the *same name* already exists (contents differ)
        try:
            existing_name_set = set(existing_names)
        except Exception:
            existing_name_set = set()

        if name in existing_name_set:
            resp = QMessageBox.question(
                self, "Overwrite preset?",
                f"Preset '{name}' already exists.\n\nOverwrite its contents?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if resp != QMessageBox.Yes:
                return

        ok = save_preset(self.db_path, self.project_id, name, ids)
        if not ok:
            QMessageBox.information(self, "Presets", f"Could not save preset '{name}'.")
            return

        self._emit_presets()
        self._toast(f"Preset “{name}” saved")
        self._recompute_matching_preset()

    def load_preset(self, name: str):
        if not self.db_path: return
        ids = get_preset_doc_ids(self.db_path, self.project_id, (name or "").strip()) or []
        self._set_ticked_ids(set(ids))
        self._toast(f"Preset “{name}” loaded")
        self._recompute_matching_preset()

    def unload_preset(self, name: str):
        self._set_ticked_ids(set())
        self._toast("Preset unloaded")
        self._recompute_matching_preset()

    def refresh_presets(self):
        self._emit_presets()

    def _emit_presets(self):
        try:
            names = list_presets(self.db_path, self.project_id)
        except Exception:
            names = []
        self.presetsReady.emit(names or [])
        self._recompute_matching_preset()

    def _recompute_matching_preset(self):
        """If the current tick set exactly matches a saved preset, emit its name; else emit ''."""
        name_hit = ""
        try:
            names = list_presets(self.db_path, self.project_id) or []
            # current selection, normalized
            current_ids = sorted({getattr(r, 'doc_id', '').strip().upper()
                                  for r in self._ticked_items() if getattr(r, 'doc_id', '')})
            for nm in names:
                ids = get_preset_doc_ids(self.db_path, self.project_id, (nm or "").strip()) or []
                if sorted({(i or "").strip().upper() for i in ids}) == current_ids:
                    name_hit = nm
                    break
        except Exception:
            name_hit = ""
        self.matchingPresetChanged.emit(name_hit)

    # ----------------- DB load / creation ------------------------------------
    def _browse_db(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project DB", "", "Database (*.db)")
        if not path: return
        self.le_db.setText(path)
        self._load_db()

    # --- drop-in replacement for RegisterTab._new_db ---
    def _new_db(self):
        # Delegate to MainWindow's central "New…" flow
        try:
            mw = self.window()
            if hasattr(mw, "_new_db_dialog"):
                mw._new_db_dialog()
                return
        except Exception:
            pass
        # Fallback (shouldn't be hit): original inline new-db flow removed on purpose.
        QMessageBox.information(self, "New DB", "Please use the global New… button.")

    def _maybe_load_on_edit(self):
        text = self.le_db.text().strip()
        if text and Path(text).exists():
            self._load_db()

    def _load_db(self):
        p = Path(self.le_db.text().strip())
        if not p.exists():
            QMessageBox.warning(self, "Not found", "Please select a valid project database (.db)."); return
        self.settings.set(DB_LAST_KEY, str(p))
        # NEW: snapshot BEFORE we touch schema (so we capture the incoming state)
        try:
            from ..services.db import create_db_backup
        except Exception:
            create_db_backup = None
        if create_db_backup:
            try:
                snap = create_db_backup(p, history_dir_name="DB History", keep=50)
                if snap:
                    print(f"[backup] DB snapshot -> {snap}")
            except Exception as e:
                print(f"[backup] snapshot failed: {e}")

        # proceed with normal init / schema ensure
        init_db(p)
        proj = get_project(p)
        if not proj:
            code, ok = QInputDialog.getText(self, "Project code", "8-digit job number / code:")
            if not ok or not code.strip(): return
            name, ok = QInputDialog.getText(self, "Project name", "Project name:")
            if not ok or not name.strip(): return
            upsert_project(p, code.strip(), name.strip(), str(p.parent))
            proj = get_project(p)
            if not proj:
                QMessageBox.information(self, "Empty DB", "Could not create project identity."); return
        self.db_path = p
        self.project_id = proj["id"]
        self.project_code = proj["project_code"]
        self.project_root = Path(proj.get("root_path") or p.parent)

        try:
            self.row_options = {**DEFAULT_ROW_OPTIONS, **(get_row_options(p, self.project_id) or {})}
        except Exception:
            self.row_options = DEFAULT_ROW_OPTIONS.copy()

        self._refresh_option_widgets()
        self._reload_rows()
        try:
            statuses = list_statuses_for_project(p, self.project_id)
        except Exception:
            statuses = []
        self.statusesReady.emit(statuses)
        self._recount()
        self.projectInfoReady.emit(proj["project_code"], proj["project_name"], self.project_root, self.db_path)
        self._emit_presets()
        self._reload_areas()

    def _reload_rows(self):
        print("Loaded rows:", self.model.rowCount())
        state = "deleted" if getattr(self, "chk_show_deleted", None) and self.chk_show_deleted.isChecked() else "active"
        rows = list_documents_with_latest(self.db_path, self.project_id, state=state)
        # Expect dicts with keys: doc_id, doc_type, file_type, description, status, latest_rev
        doc_rows = []
        for r in rows:
            latest_raw = r.get("latest_rev") or ""
            token = _parse_latest_token(latest_raw)
            # Minimal row object matching what RegisterTableModel.data() expects
            class _Row:
                __slots__ = ("doc_id", "doc_type", "file_type", "description", "comments", "status", "latest_rev_raw",
                             "latest_rev_token")

                def __init__(self, d, t, f, desc, comm, s, lr_raw, lt):
                    self.doc_id = d;
                    self.doc_type = t;
                    self.file_type = f
                    self.description = desc;
                    self.comments = comm  # <— NEW
                    self.status = s;
                    self.latest_rev_raw = lr_raw;
                    self.latest_rev_token = lt

            doc_rows.append(
                _Row(
                    r["doc_id"], r["doc_type"], r["file_type"],
                    r.get("description", ""), r.get("comments", ""),  # <— NEW
                    r.get("status", ""),
                    latest_raw, token
                )
            )

        # If model has a setter, use it; else reset underlying lists
        if hasattr(self.model, 'set_rows'):
            self.model.set_rows(doc_rows)
        else:
            try:
                self.model.beginResetModel()
                if hasattr(self.model, '_rows'):
                    self.model._rows = doc_rows
                if hasattr(self.model, '_selected'):
                    self.model._selected = [False]*len(doc_rows)
                self.model.endResetModel()
            except Exception:
                pass

        # Default sort by Doc ID
        self.table.sortByColumn(COL_DOC_ID, Qt.AscendingOrder)
        self._apply_default_column_widths()
        try:
            self.table.selectionModel().selectionChanged.connect(self._on_view_selection_changed)
        except Exception:
            pass
        self._on_view_selection_changed()

    def _refresh_option_widgets(self):
        """Emit current option lists so the sidebar can refresh its combos."""
        self.rowOptionsReady.emit({
            "doc_types": self.row_options.get("doc_types", []),
            "file_types": self.row_options.get("file_types", []),
            "statuses": self.row_options.get("statuses", [])
        })

    # ----------------- Bulk apply --------------------------------------------
    def _highlighted_doc_ids(self) -> List[str]:
        """Return doc_ids for the currently highlighted (selectedRows) entries."""
        sel = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel:
            return []
        rows = getattr(self.model, "_rows", [])
        out: List[str] = []
        for vix in sel:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                did = getattr(rows[srow], "doc_id", "").strip()
                if did:
                    out.append(did)
        # dedupe, preserve order
        return list(dict.fromkeys(out))

    def apply_bulk_to_selected(self, type_text: str, file_text: str, status_text: str) -> None:
        """
        Apply Type/File type/Status to HIGHLIGHTED rows (ignores checkboxes entirely).
        Values come from the sidebar; '— no change —' means skip that field.
        """
        if not (self.db_path and self.project_id):
            QMessageBox.information(self, "Project", "Open a project database first.")
            return

        doc_ids = self._highlighted_doc_ids()
        if not doc_ids:
            QMessageBox.information(self, "Nothing selected",
                                    "Highlight one or more rows (use Shift/Ctrl), then click Apply.")
            return

        def _norm(x: str) -> str:
            x = (x or "").strip()
            if x in ("— no change —", globals().get("KEEP_VALUE", "— no change —"), ""):
                return ""
            return x

        fields: Dict[str, str] = {}
        t = _norm(type_text)
        f = _norm(file_text)
        s = _norm(status_text)

        if t:
            # If items look like "DWG — Drawing", keep the code left of the em dash
            fields["doc_type"] = t.split("—", 1)[0].strip().upper()
        if f:
            fields["file_type"] = f.strip().upper()
        if s:
            fields["status"] = s.strip()

        if not fields:
            QMessageBox.information(self, "No values",
                                    "Choose a Type / File type / Status (or leave as “— no change —”).")
            return

        try:
            # Safe & simple: per-row update
            for did in doc_ids:
                update_document_fields(self.db_path, self.project_id, did, fields)

            # Refresh & re-highlight same rows
            prev = set(doc_ids)
            self._reload_rows()
            sel_model = self.table.selectionModel()
            sel_model.clearSelection()
            rows = getattr(self.model, "_rows", [])
            for r, row in enumerate(rows):
                if getattr(row, "doc_id", "") in prev:
                    vix = self.proxy.mapFromSource(self.model.index(r, COL_DOC_ID))
                    sel_model.select(vix, sel_model.Select | sel_model.Rows)

            self._recount()
            QMessageBox.information(self, "Apply", f"Updated {len(doc_ids)} document(s).")

        except Exception as e:
            QMessageBox.warning(self, "Apply failed", str(e))

    def current_paths(self) -> tuple[str, str]:
        return (str(self.db_path or ""), str(self.project_root or ""))

    def _set_check_for_filtered(self, checked: bool):
        rows = getattr(self.model, '_rows', [])
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.proxy.rowCount()):
            srow = self.proxy.mapToSource(self.proxy.index(r, COL_SELECT)).row()
            if 0 <= srow < len(rows):
                self.model.setData(self.model.index(srow, COL_SELECT), state, Qt.CheckStateRole)
        self._recount()

    def _on_model_changed(self, *args, **kwargs):
        self._recount(); self.proxy.invalidateFilter()

    def _recount(self):
        n = len(self._ticked_items())
        self.selectionCountChanged.emit(n)
        self.matchingPresetChanged.emit("")  # default, then recompute
        self._recompute_matching_preset()

    def _proceed_clicked(self):
        if not self.db_path or self.project_id is None:
            QMessageBox.information(self, "Open DB", "Please load a project database first."); return
        items = self._ticked_items()
        if not items:
            QMessageBox.information(self, "No selection", "Please tick one or more documents to submit."); return
        self.on_proceed(items, self.db_path)

    # ----------------- Revisions ---------------------------------------------

    def _compute_next(self, curr: str) -> str:
        raw = (curr or "").strip()
        has_alpha = any(ch.isalpha() for ch in raw)
        has_digit = any(ch.isdigit() for ch in raw)

        # Use UI mode if present; else fall back to stored idx (0=Alpha,1=Alphanum,2=Numeric)
        try:
            mode_idx = (self.cb_rev_mode.currentIndex()
                        if getattr(self, "cb_rev_mode", None) is not None
                        else int(getattr(self, "_rev_mode_idx", 0)))
        except Exception:
            mode_idx = 0

        if has_digit and not has_alpha:
            return _numeric_next(raw)
        if has_alpha and not has_digit:
            return _alpha_next(raw)

        # Mixed token → respect mode selection
        if mode_idx == 0:
            return _alpha_next(raw)
        if mode_idx == 2:
            return _numeric_next(raw)
        return _alphanum_next(raw)

    def _compute_prev(self, curr: str) -> str:
        raw = (curr or "").strip()
        has_alpha = any(ch.isalpha() for ch in raw)
        has_digit = any(ch.isdigit() for ch in raw)

        # Use UI choice if present; otherwise a sentinel to allow inference
        mode_idx = None
        try:
            if getattr(self, "cb_rev_mode", None) is not None:
                mode_idx = int(self.cb_rev_mode.currentIndex())
            else:
                mode_idx = int(getattr(self, "_rev_mode_idx", 0))
        except Exception:
            mode_idx = None

        # Inference: if purely numeric/alpha, prefer that family
        if has_digit and not has_alpha:
            return _numeric_prev(raw)
        if has_alpha and not has_digit:
            return _alpha_prev(raw)

        # Mixed (alphanumeric) or empty: honour selected mode, default to alphanum
        if mode_idx == 0:  # Alpha
            return _alpha_prev(raw)
        if mode_idx == 2:  # Numeric
            return _numeric_prev(raw)
        return _alphanum_prev(raw)

    def _rev_set_selected(self):
        if not (self.db_path and self.project_id is not None):
            QMessageBox.information(self, "Project", "Open a project database first."); return
        sel_rows = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel_rows:
            QMessageBox.information(self, "Select rows", "Single-click to highlight one or more rows, then try again."); return
        rows = getattr(self.model, '_rows', [])
        doc_ids = []
        for vix in sel_rows:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                doc_ids.append(rows[srow].doc_id)
        val, ok = QInputDialog.getText(self, "Set Revision", "Enter revision (e.g. A, 1A, 3):")
        if not ok: return
        val = (val or "").strip().upper()
        if not val:
            QMessageBox.information(self, "Set Revision", "No value entered."); return
        touched = 0
        for did in doc_ids:
            touched += add_revision_by_docid(self.db_path, self.project_id, did, val)
        prev = set(doc_ids)
        self._reload_rows()
        sel_model = self.table.selectionModel(); sel_model.clearSelection()
        rows = getattr(self.model, '_rows', [])
        for r, row in enumerate(rows):
            if row.doc_id in prev:
                vix = self.proxy.mapFromSource(self.model.index(r, COL_DOC_ID))
                sel_model.select(vix, sel_model.Select | sel_model.Rows)
        QMessageBox.information(self, "Revisions", f"Set {touched} revision(s).")

    def _rev_increment_selected(self):
        if not (self.db_path and self.project_id is not None):
            QMessageBox.information(self, "Project", "Open a project database first.")
            return

        sel_rows = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel_rows:
            QMessageBox.information(self, "Select rows", "Single-click to highlight one or more rows, then try again.")
            return

        rows = getattr(self.model, '_rows', [])
        doc_ids = []
        for vix in sel_rows:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                doc_ids.append(rows[srow].doc_id)

        # ensure latest_rev_token reflects any inline edits before we compute next
        self._reload_rows()
        rows = getattr(self.model, '_rows', [])

        # build curr token map (fallback to raw display if token missing)
        curr_map = {}
        for r in rows:
            did = getattr(r, 'doc_id', '')
            tok = getattr(r, 'latest_rev_token', '') or _parse_latest_token(str(getattr(r, 'latest_rev_raw', '') or ''))
            curr_map[did] = tok

        # debug: see the seeds we will increment from
        try:
            print("[rev-increment] seed:", {d: curr_map.get(d, '') for d in doc_ids})
        except Exception:
            pass

        updates = {did: self._compute_next(curr_map.get(did, "")) for did in doc_ids}

        touched = 0
        for did, rev in updates.items():
            touched += add_revision_by_docid(self.db_path, self.project_id, did, rev)

        prev = set(doc_ids)
        self._reload_rows()
        sel_model = self.table.selectionModel()
        sel_model.clearSelection()
        rows = getattr(self.model, '_rows', [])
        for r, row in enumerate(rows):
            if row.doc_id in prev:
                vix = self.proxy.mapFromSource(self.model.index(r, COL_DOC_ID))
                sel_model.select(vix, sel_model.Select | sel_model.Rows)

        QMessageBox.information(self, "Revisions", f"Applied {touched} revision increment(s).")

    def _rev_decrement_selected(self):
        if not (self.db_path and self.project_id is not None):
            QMessageBox.information(self, "Project", "Open a project database first.")
            return

        sel_rows = self.table.selectionModel().selectedRows(COL_DOC_ID)
        if not sel_rows:
            QMessageBox.information(self, "Select rows", "Single-click to highlight one or more rows, then try again.")
            return

        rows = getattr(self.model, '_rows', [])
        doc_ids = []
        for vix in sel_rows:
            srow = self.proxy.mapToSource(vix).row()
            if 0 <= srow < len(rows):
                doc_ids.append(rows[srow].doc_id)

        # ensure tokens reflect any inline edits
        self._reload_rows()
        rows = getattr(self.model, '_rows', [])

        curr_map = {}
        for r in rows:
            did = getattr(r, 'doc_id', '')
            tok = getattr(r, 'latest_rev_token', '') or _parse_latest_token(str(getattr(r, 'latest_rev_raw', '') or ''))
            curr_map[did] = tok

        try:
            print("[rev-decrement] seed:", {d: curr_map.get(d, '') for d in doc_ids})
        except Exception:
            pass

        updates = {did: self._compute_prev(curr_map.get(did, "")) for did in doc_ids}

        touched = 0
        for did, rev in updates.items():
            touched += add_revision_by_docid(self.db_path, self.project_id, did, rev)

        prev = set(doc_ids)
        self._reload_rows()
        sel_model = self.table.selectionModel();
        sel_model.clearSelection()
        rows = getattr(self.model, '_rows', [])
        for r, row in enumerate(rows):
            if row.doc_id in prev:
                vix = self.proxy.mapFromSource(self.model.index(r, COL_DOC_ID))
                sel_model.select(vix, sel_model.Select | sel_model.Rows)

        QMessageBox.information(self, "Revisions", f"Applied {touched} revision decrement(s).")

    # ----------------- Areas / Batch import ----------------------------------
    def _reload_areas(self):
        if not (self.db_path and self.project_id):
            self._areas_cache = []
            return
        self._areas_cache = list_areas(self.db_path, self.project_id)

    def _manage_areas(self):
        if not (self.db_path and self.project_id):
            QMessageBox.information(self, "Project", "Open a project database first."); return
        self._reload_areas()
        dlg = ManageAreasDialog(self._areas_cache, parent=self)
        if dlg.exec_() != dlg.Accepted:
            return
        rows = dlg.get_rows()
        if rows is None:
            return
        existing_codes = {c for c, _ in self._areas_cache}
        new_codes = {c for c, _ in rows}
        for code, desc in rows:
            upsert_area(self.db_path, self.project_id, code, desc)
        for code in existing_codes - new_codes:
            delete_area(self.db_path, self.project_id, code)
        self._reload_areas()
        QMessageBox.information(self, "Areas", "Areas updated.")

    def _add_document(self):
        if not (self.db_path and self.project_id):
            QMessageBox.information(self, "Project", "Open a project database first.")
            return

        # Keep these so dialog picks up fresh options
        self._reload_row_options()
        self._reload_areas()

        existing = [getattr(r, 'doc_id', '') for r in getattr(self.model, '_rows', [])]
        dlg = AddDocumentDialog(
            existing_doc_ids=list(set(existing)),
            row_options=self.row_options,
            project_code=self.project_code or "",
            areas=self._areas_cache,
            parent=self
        )

        if dlg.exec_() != dlg.Accepted:
            return

        # --- Single add ---
        if getattr(dlg, "payload", None):
            from ..services.db import upsert_document
            upsert_document(self.db_path, self.project_id, dlg.payload)

            # Optional: apply template only for single add
            try:
                if dlg.payload.get("use_template"):
                    print("[RegisterTab] attempting template apply with payload:", dlg.payload, flush=True)
                    from .services.template_apply import apply_template_for_new_doc
                    created = apply_template_for_new_doc(Path(self.db_path), dlg.payload)
                    print("[RegisterTab] template apply result:", created, flush=True)
            except Exception as e:
                print("[RegisterTab] template apply ERROR:", e, flush=True)

            self._reload_rows()
            self._set_ticked_ids({dlg.payload["doc_id"]})
            self._recount()
            self.proxy.invalidateFilter()
            try:
                from .widgets.toast import toast
                toast(self, f"Added 1 document.")
            except Exception:
                QMessageBox.information(self, "Add Document", "Added 1 document.")
            return

        # --- Batch add ---
        payloads = getattr(dlg, "payloads", None) or []
        if not payloads:
            return

        from ..services.db import upsert_document
        added_ids = []
        for doc in payloads:
            try:
                upsert_document(self.db_path, self.project_id, doc)
                added_ids.append(doc["doc_id"])
            except Exception as e:
                print(f"[RegisterTab] batch upsert failed for {doc.get('doc_id')}: {e}", flush=True)

        self._reload_rows()

        # Re-select the newly added rows
        try:
            sel_model = self.table.selectionModel()
            sel_model.clearSelection()
            rows = getattr(self.model, '_rows', [])
            added = set(added_ids)
            for r, row in enumerate(rows):
                if getattr(row, 'doc_id', '') in added:
                    vix = self.proxy.mapFromSource(self.model.index(r, COL_DOC_ID))
                    sel_model.select(vix, sel_model.Select | sel_model.Rows)
        except Exception as e:
            print("[RegisterTab] selection after batch failed:", e, flush=True)

        self._recount()
        self.proxy.invalidateFilter()

        try:
            from .widgets.toast import toast
            toast(self, f"Added {len(added_ids)} document(s).")
        except Exception:
            QMessageBox.information(self, "Add Documents", f"Added {len(added_ids)} document(s).")

    def _import_batch_updates(self):
        if not (self.db_path and self.project_id):
            QMessageBox.information(self, "Project", "Open a project database first."); return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import CSV/XLSX", "", "Spreadsheets (*.csv *.xlsx)"
        )
        if not path: return
        updates = self._read_updates_file(path)
        if not updates:
            QMessageBox.information(self, "Import", "No valid rows found."); return
        try:
            summary = bulk_update_docs(self.db_path, self.project_id, updates)
        except Exception as e:
            QMessageBox.information(self, "Import Error", f"Update failed:\n{e}")
            return
        self._reload_rows(); self.proxy.invalidateFilter()
        QMessageBox.information(self, "Import Complete",
                                f"Matched: {summary['matched']}\n"
                                f"Revisions updated: {summary['updated_rev']}\n"
                                f"Descriptions updated: {summary['updated_desc']}")

    def _read_updates_file(self, path: str):
        path = path.strip()
        updates = {}
        try:
            if path.lower().endswith(".csv"):
                import csv
                with open(path, newline="", encoding="utf-8-sig") as f:
                    rows = list(csv.reader(f))
                updates = self._rows_to_updates(rows)
            elif path.lower().endswith(".xlsx"):
                try:
                    import openpyxl
                except ImportError:
                    QMessageBox.information(self, "Import", "openpyxl not installed. Install it to import .xlsx files.")
                    return {}
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                rows = [[c.value if c.value is not None else "" for c in row] for row in ws.iter_rows()]
                updates = self._rows_to_updates(rows)
            else:
                QMessageBox.information(self, "Import", "Only .csv or .xlsx are supported.")
                return {}
        except Exception as e:
            QMessageBox.information(self, "Import Error", f"Failed to read file:\n{e}")
            return {}
        return updates

    def _rows_to_updates(self, rows):
        if not rows: return {}
        header_like = [str(x).strip().lower() for x in rows[0]]
        has_headers = "doc_id" in header_like or "revision" in header_like or "description" in header_like
        data = rows[1:] if has_headers else rows
        idx_doc = (header_like.index("doc_id") if has_headers and "doc_id" in header_like else 0)
        idx_rev = (header_like.index("revision") if has_headers and "revision" in header_like else 1)
        idx_desc = (header_like.index("description") if has_headers and "description" in header_like else 2)
        updates = {}
        for r in data:
            if not any(r):
                continue
            doc_id = (str(r[idx_doc]) if idx_doc < len(r) else "").strip().upper()
            if not doc_id:
                continue
            revision = (str(r[idx_rev]).strip() if idx_rev < len(r) and r[idx_rev] not in (None, "") else None)
            description = (str(r[idx_desc]).strip() if idx_desc < len(r) and r[idx_desc] not in (None, "") else None)
            updates[doc_id] = {}
            if revision is not None:
                updates[doc_id]["revision"] = revision
            if description is not None:
                updates[doc_id]["description"] = description
        return updates

    # ----------------- Persist when model lacks callbacks ---------------------
    def _on_cell_edited(self, topLeft: QModelIndex, bottomRight: QModelIndex, roles):
        try:
            if topLeft != bottomRight:
                return

            # Qt often sends an empty roles list; treat that as "something changed"
            if roles and (Qt.EditRole not in roles and Qt.DisplayRole not in roles):
                return

            src_idx = self.proxy.mapToSource(topLeft)
            r = src_idx.row();
            c = src_idx.column()
            rows = getattr(self.model, '_rows', [])
            if not (0 <= r < len(rows)):
                return
            row = rows[r]
            did = getattr(row, 'doc_id', '')
            if not did:
                return

            # --- debug header ----------------------------------------------------
            try:
                print(f"[RegisterTab] dataChanged r={r} c={c} roles={list(roles) if roles else []} did={did}")
            except Exception:
                pass

            if c == COL_DESCRIPTION:
                update_document_fields(self.db_path, self.project_id, did,
                                       {"description": getattr(row, 'description', '')})
            elif c == COL_TYPE:
                update_document_fields(self.db_path, self.project_id, did, {"doc_type": getattr(row, 'doc_type', '')})
            elif c == COL_FILETYPE:
                update_document_fields(self.db_path, self.project_id, did, {"file_type": getattr(row, 'file_type', '')})
            elif c == COL_STATUS:
                update_document_fields(self.db_path, self.project_id, did, {"status": getattr(row, 'status', '')})
            elif c == COL_LATEST_REV:
                raw = str(topLeft.data() or "")
                token = _parse_latest_token(raw)
                try:
                    print(f"[RegisterTab] LatestRev edit: raw='{raw}' -> token='{token}' for {did}")
                except Exception:
                    pass
                if token:
                    inserted = add_revision_by_docid(self.db_path, self.project_id, did, token)
                    try:
                        print(f"[RegisterTab] add_revision_by_docid -> {inserted} (1=changed, 0=no-op)")
                    except Exception:
                        pass
        except Exception:
            # keep UI responsive on any failure
            pass

    def _reload_row_options(self):
        """Pull latest doc/file/status lists from the DB and refresh bulk-apply widgets."""
        # DEFAULT_ROW_OPTIONS is already imported alongside RowAttributesEditor in your file
        if not (self.db_path and self.project_id):
            self.row_options = DEFAULT_ROW_OPTIONS.copy()
            self._refresh_option_widgets()
            return
        try:
            opts = get_row_options(self.db_path, self.project_id) or {}
        except Exception:
            opts = {}
        self.row_options = {**DEFAULT_ROW_OPTIONS, **opts}
        # keep the toolbar filter widgets in sync too
        self._refresh_option_widgets()

    def _on_view_selection_changed(self, *args):
        self.highlightedDocIdsChanged.emit(self._highlighted_doc_ids())


    # ---- Toast --------------------------------------------------------

    def _toast(self, msg: str):
        try:
            toast(self.window() or self, msg, 1200)
        except Exception:
            pass


def _alpha_next(tok: str) -> str:
    s = ''.join([c for c in (tok or '').upper() if c.isalpha()])
    if not s:
        return 'A'
    n = 0
    for ch in s:
        n = n * 26 + (_ALPHA.index(ch) + 1)
    n += 1
    out = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out.append(_ALPHA[rem])
    return ''.join(reversed(out))


def _numeric_next(tok: str) -> str:
    digits = ''.join([c for c in str(tok or '') if c.isdigit()])
    if not digits:
        return '1'
    try:
        return str(int(digits) + 1)
    except Exception:
        return '1'

def _alphanum_next(tok: str) -> str:
    raw = (tok or '').upper()
    num = ''.join([c for c in raw if c.isdigit()])
    letters = ''.join([c for c in raw if c.isalpha()])
    if not num and not letters:
        return '1A'
    if not num:
        num = '1'
    if not letters:
        letters = 'A'
    nxt = _alpha_next(letters)
    if len(letters) < len(nxt) and letters == 'Z':
        return f"{int(num)+1}A"
    return f"{num}{nxt}"

def _alpha_prev(tok: str) -> str:
    """A -> A (clamp), B -> A, AA -> Z, AB -> AA, etc."""
    s = ''.join([c for c in (tok or '').upper() if c.isalpha()])
    if not s:
        return 'A'
    # decode base-26 (A=1 .. Z=26)
    n = 0
    for ch in s:
        n = n * 26 + (_ALPHA.index(ch) + 1)
    if n <= 1:
        return 'A'
    n -= 1
    out = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out.append(_ALPHA[rem])
    return ''.join(reversed(out))


def _numeric_prev(tok: str) -> str:
    """1 -> 0, 0 -> 0 (clamp)."""
    digits = ''.join([c for c in str(tok or '') if c.isdigit()])
    if not digits:
        return '0'
    try:
        v = int(digits)
        return str(max(0, v - 1))
    except Exception:
        return '0'


def _alphanum_prev(tok: str) -> str:
    """
    Mirrors _alphanum_next:
    3B -> 3A
    3A -> 2Z (if num>1), else clamp to 1A
    """
    raw = (tok or '').upper()
    num = ''.join([c for c in raw if c.isdigit()])
    letters = ''.join([c for c in raw if c.isalpha()])
    if not num and not letters:
        return '1A'  # clamp baseline

    if num and not letters:
        return _numeric_prev(num)
    if letters and not num:
        return _alpha_prev(letters)

    # both present
    try:
        n = int(num)
    except Exception:
        n = 1

    if letters == 'A':
        if n > 1:
            return f"{n-1}Z"
        return '1A'  # clamp baseline
    return f"{n}{_alpha_prev(letters)}"
