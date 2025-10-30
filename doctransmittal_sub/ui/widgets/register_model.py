# doctransmittal_sub/ui/widgets/register_model.py
from __future__ import annotations
from typing import List, Set, Callable, Optional, Dict
from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex
from ...models.document import DocumentRow

class RegisterTableModel(QAbstractTableModel):
    COL_SELECT      = 0
    COL_DOC_ID      = 1
    COL_TYPE        = 2
    COL_FILETYPE    = 3
    COL_DESCRIPTION = 4
    COL_STATUS      = 5
    COL_LATEST_REV  = 6

    COLS = ["✓", "Doc ID", "Type", "File Type", "Description", "Status", "Latest Rev"]

    def __init__(self, rows: List[DocumentRow] | None = None, parent=None):
        super().__init__(parent)
        self._rows: List[DocumentRow] = rows or []
        self._selected: List[bool] = [False] * len(self._rows)
        # Callbacks wired by the tab to persist edits
        self._save_fields_cb: Optional[Callable[[str, Dict[str, str]], None]] = None
        self._add_revision_cb: Optional[Callable[[str, str], int]] = None

    # ---- wiring for persistence ----
    def set_save_callbacks(self,
                           save_fields: Callable[[str, Dict[str, str]], None],
                           add_revision: Callable[[str, str], int]) -> None:
        self._save_fields_cb = save_fields
        self._add_revision_cb = add_revision

    # ---- model shape ----
    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(self.COLS)

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if role != Qt.DisplayRole: return None
        return self.COLS[section] if orient == Qt.Horizontal else str(section + 1)

    # ---- data/flags ----
    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid(): return None
        r, c = index.row(), index.column()
        row = self._rows[r]

        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == self.COL_DOC_ID:      return row.doc_id
            if c == self.COL_TYPE:        return row.doc_type or ""
            if c == self.COL_FILETYPE:    return row.file_type or ""
            if c == self.COL_DESCRIPTION: return row.description or ""
            if c == self.COL_STATUS:      return row.status or ""
            if c == self.COL_LATEST_REV:  return (row.latest_rev_token or row.latest_rev_raw or "")
            if c == self.COL_SELECT:      return None

        if role == Qt.CheckStateRole and c == self.COL_SELECT:
            return Qt.Checked if self._selected[r] else Qt.Unchecked

        return None

    def flags(self, index: QModelIndex):
        if not index.isValid(): return Qt.ItemIsEnabled
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        c = index.column()
        if c == self.COL_SELECT:
            return base | Qt.ItemIsUserCheckable
        if c == self.COL_DOC_ID:
            return base  # locked
        if c in (self.COL_TYPE, self.COL_FILETYPE, self.COL_DESCRIPTION, self.COL_STATUS, self.COL_LATEST_REV):
            return base | Qt.ItemIsEditable
        return base

    # ---- editing ----
    def setData(self, index: QModelIndex, value, role=Qt.EditRole):
        if not index.isValid(): return False

        r, c = index.row(), index.column()
        row = self._rows[r]

        # selection checkbox
        if c == self.COL_SELECT and role == Qt.CheckStateRole:
            self._selected[r] = (value == Qt.Checked)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True

        if role != Qt.EditRole:
            return False

        did = row.doc_id

        def _emit():
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])

        try:
            if c == self.COL_DESCRIPTION:
                new_desc = ("" if value is None else str(value)).strip()
                if new_desc == (row.description or ""): return True
                row.description = new_desc
                if self._save_fields_cb:
                    self._save_fields_cb(did, {"description": new_desc})
                _emit(); return True

            if c == self.COL_TYPE:
                # allow "DWG — Drawing" items; take code to the left of the em dash
                new_type = ("" if value is None else str(value)).split("—", 1)[0].strip().upper()
                if new_type == (row.doc_type or ""): return True
                row.doc_type = new_type
                if self._save_fields_cb:
                    self._save_fields_cb(did, {"doc_type": new_type})
                _emit(); return True

            if c == self.COL_FILETYPE:
                new_ft = ("" if value is None else str(value)).strip().upper()
                if new_ft == (row.file_type or ""): return True
                row.file_type = new_ft
                if self._save_fields_cb:
                    self._save_fields_cb(did, {"file_type": new_ft})
                _emit(); return True

            if c == self.COL_STATUS:
                new_status = ("" if value is None else str(value)).strip()
                if new_status == (row.status or ""): return True
                row.status = new_status
                if self._save_fields_cb:
                    self._save_fields_cb(did, {"status": new_status})
                _emit(); return True

            if c == self.COL_LATEST_REV:
                new_rev = ("" if value is None else str(value)).strip()
                if not new_rev: return True
                # persist to revisions table (latest is derived by SELECT)
                if self._add_revision_cb:
                    self._add_revision_cb(did, new_rev)
                # reflect immediately in the UI
                row.latest_rev_token = new_rev
                row.latest_rev_raw = new_rev
                _emit(); return True

        except Exception:
            # keep UI alive even if persistence throws
            return False

        return False

    # ---- helpers used by the tab ----
    def set_rows(self, rows: List[DocumentRow]):
        self.beginResetModel()
        self._rows = rows or []
        self._selected = [False] * len(self._rows)
        self.endResetModel()

    def selected_items(self) -> List[DocumentRow]:
        return [r for r, s in zip(self._rows, self._selected) if s]

    def selected_doc_ids(self) -> List[str]:
        return [r.doc_id for r, s in zip(self._rows, self._selected) if s]

    def all_doc_ids(self) -> List[str]:
        return [r.doc_id for r in self._rows]

    def set_selected_doc_ids(self, doc_ids: Set[str]) -> None:
        s = set(doc_ids or set())
        self._selected = [(row.doc_id in s) for row in self._rows]
        if self.rowCount() > 0:
            tl = self.index(0, self.COL_SELECT)
            br = self.index(self.rowCount() - 1, self.COL_SELECT)
            self.dataChanged.emit(tl, br, [Qt.CheckStateRole])

    def clear_all_selection(self) -> None:
        self.set_selected_doc_ids(set())
