from __future__ import annotations
from typing import List, Dict, Any, Optional

from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, pyqtSignal

# Visible headers
RFI_COLS: List[str] = [
    "DOCUMENT NO.",      # 0
    "Discipline",        # 1
    "Issued To",         # 2
    "Company",           # 3 issued_to_company
    "Issued From",       # 4
    "Issued Date",       # 5
    "Respond By",        # 6
    "Subject",           # 7
    "Response From",     # 8
    "Company",           # 9 response_company
    "Response Date",     # 10
    "Response Status",   # 11
    "Comments",          # 12
    "Contents",          # 13 → opens rich editor (not stored directly)
]

# Storage keys (superset, includes hidden fields)
FIELD_KEYS: List[str] = [
    "number","discipline","issued_to","issued_to_company","issued_from",
    "issued_date","respond_by","subject",
    "response_from","response_company","response_date","response_status","comments",
    "_contents_",  # sentinel for the special column
]

# Dropdown options
DISCIPLINE_OPTS = [
    "Contracts","Commencement","Design","Controls","Commisioning",
    "Management","Delivery","Safety","Environment","Quality","Equipment",
]
STATUS_OPTS = ["Closed", "Outstanding"]

class RfiTableModel(QAbstractTableModel):
    edited = pyqtSignal()

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = rows or []
        self._save_cb = None  # fn(number, fields)

    def set_save_callback(self, fn) -> None:
        self._save_cb = fn

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows or []
        self.endResetModel()

    def raw_row(self, r: int) -> Dict[str, Any]:
        return self._rows[r] if 0 <= r < len(self._rows) else {}

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(RFI_COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return RFI_COLS[section] if orientation == Qt.Horizontal else str(section + 1)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        row = self._rows[r]
        key = FIELD_KEYS[c]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if key == "_contents_":
                return "Open"
            return row.get(key, "")
        if role == Qt.TextAlignmentRole and key == "_contents_":
            return Qt.AlignCenter
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemIsEnabled
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if FIELD_KEYS[index.column()] == "_contents_":
            return base  # clickable, not editable
        return base if index.column() == 0 else (base | Qt.ItemIsEditable)

    def setData(self, index: QModelIndex, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        key = FIELD_KEYS[index.column()]
        if key == "_contents_":
            return False
        r = index.row()
        row = self._rows[r]
        number = row.get("number","")
        new_val = "" if value is None else str(value).strip()
        if row.get(key, "") == new_val:
            return True
        row[key] = new_val
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        if self._save_cb and number:
            try:
                self._save_cb(number, {key: new_val})
            except Exception:
                pass
        self.edited.emit()
        return True
