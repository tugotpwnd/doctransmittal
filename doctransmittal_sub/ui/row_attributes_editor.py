# ui/row_attributes_editor.py
from __future__ import annotations
from typing import Dict, List
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QLineEdit, QWidget, QMessageBox
)

DEFAULT_ROW_OPTIONS = {
    # Replaced to use your standard three-letter identifiers
    "doc_types": ["CAL", "DOC", "DWG", "ITC", "ITP", "MAN", "MDL", "PGM",
                  "REG", "REP", "RFI", "SCH", "TRN", "VAR"],
    "file_types": ["PDF", "DWG", "XLSX", "DOCX", "PTW", "PowerCAD"],
    "statuses":  ["Not Started", "In Progress", "On Hold", "Incomplete", "For Review", "Complete", "Submitted"],
}

DOC_TYPE_NAMES = {
    "CAL": "Calculations",
    "DOC": "General Document",
    "DWG": "Drawing",
    "ITC": "Inspection & Test Checklist",
    "ITP": "Inspection & Test Plan",
    "MAN": "Manual",
    "MDL": "Model",
    "PGM": "Program",
    "REG": "Register",
    "REP": "Report",
    "RFI": "Request for Information",
    "SCH": "Schedule",
    "TRN": "Document Transmittal",
    "VAR": "Variation",
}

class _ListEditor(QWidget):
    def __init__(self, title: str, items: List[str], parent=None):
        super().__init__(parent)
        self.list = QListWidget(self)
        for it in items: self.list.addItem(it)
        self.ed = QLineEdit(self); self.ed.setPlaceholderText(f"Add new {title}…")
        btn_add = QPushButton("Add"); btn_del = QPushButton("Remove")
        btn_up = QPushButton("↑"); btn_down = QPushButton("↓")
        btn_add.clicked.connect(self._add); btn_del.clicked.connect(self._del)
        btn_up.clicked.connect(self._up); btn_down.clicked.connect(self._down)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(title))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout(); row.addWidget(self.ed, 1); row.addWidget(btn_add); lay.addLayout(row)
        row2 = QHBoxLayout(); row2.addWidget(btn_del); row2.addStretch(1); row2.addWidget(btn_up); row2.addWidget(btn_down)
        lay.addLayout(row2)

        theme = (parent.settings.get("ui.theme", "dark") or "dark").lower() if hasattr(parent, "settings") else "dark"
        if theme == "light":
            text_col = "#0b1325"
            border_col = "#d7deea"
            bg_col = "#ffffff"
        else:
            text_col = "#E7ECF4"
            border_col = "#233044"
            bg_col = "#0f1724"

        self.list.setStyleSheet(f"""
        QListWidget {{
            background: {bg_col};
            color: {text_col};
            border: 1px solid {border_col};
            border-radius: 8px;
        }}
        QListWidget::item:selected {{
            background: rgba(79,125,255,0.35);
            color: {'#000' if theme == 'light' else '#fff'};
        }}
        """)

    def _add(self):
        t = self.ed.text().strip()
        if not t: return
        for i in range(self.list.count()):
            if self.list.item(i).text().strip().lower() == t.lower():
                QMessageBox.information(self, "Exists", f"'{t}' already exists."); return
        self.list.addItem(t); self.ed.clear()

    def _del(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _up(self):
        i = self.list.currentRow()
        if i <= 0: return
        it = self.list.takeItem(i)
        self.list.insertItem(i-1, it)
        self.list.setCurrentRow(i-1)

    def _down(self):
        i = self.list.currentRow()
        if i < 0 or i >= self.list.count()-1: return
        it = self.list.takeItem(i)
        self.list.insertItem(i+1, it)
        self.list.setCurrentRow(i+1)

    def values(self) -> List[str]:
        return [self.list.item(i).text().strip() for i in range(self.list.count()) if self.list.item(i).text().strip()]

class RowAttributesEditor(QDialog):
    def __init__(self, project_code: str, initial: Dict[str, List[str]] | None, save_cb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Project Row Attributes")
        opts = {**DEFAULT_ROW_OPTIONS, **(initial or {})}
        self.doc_types = _ListEditor("Document Types", opts.get("doc_types", []), self)
        self.file_types = _ListEditor("File Types", opts.get("file_types", []), self)
        self.statuses = _ListEditor("Statuses", opts.get("statuses", []), self)
        lay = QVBoxLayout(self)
        row = QHBoxLayout(); row.addWidget(self.doc_types, 1); row.addWidget(self.file_types, 1); row.addWidget(self.statuses, 1)
        lay.addLayout(row)
        btns = QHBoxLayout()
        btn_save = QPushButton("Save"); btn_cancel = QPushButton("Cancel")
        btn_save.clicked.connect(self._save); btn_cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(btn_cancel); btns.addWidget(btn_save)
        lay.addLayout(btns)
        self._save_cb = save_cb
        self._apply_theme()  # theme-aware colors for the three list widgets

    # --- NEW ---
    def _apply_theme(self):
        """
        Style the three list panes (doc_types, file_types, statuses) based on the parent's
        settings['ui.theme'] ('light' or 'dark'). Falls back to dark.
        """
        theme = "dark"
        try:
            p = self.parent()
            if hasattr(p, "settings"):
                theme = (p.settings.get("ui.theme", "dark") or "dark").lower()
        except Exception:
            pass

        if theme == "light":
            text_col = "#0b1325"
            bg_col = "#ffffff"
            border_col = "#d7deea"
            sel_bg = "rgba(45,91,255,0.14)"
            sel_fg = "#000000"
        else:
            text_col = "#E7ECF4"
            bg_col = "#0f1724"
            border_col = "#233044"
            sel_bg = "rgba(79,125,255,0.35)"
            sel_fg = "#ffffff"

        ss = f"""
        QListWidget {{
            background: {bg_col};
            color: {text_col};
            border: 1px solid {border_col};
            border-radius: 8px;
        }}
        QListWidget::item {{ padding: 3px 6px; }}
        QListWidget::item:selected {{
            background: {sel_bg};
            color: {sel_fg};
        }}
        """

        for w in (self.doc_types.list, self.file_types.list, self.statuses.list):
            try:
                w.setStyleSheet(ss)
            except Exception:
                pass

    def _save(self):
        # normalize any free-text (e.g., "Drawing") back to a 3-letter code
        inverse = {v.upper(): k for k, v in DOC_TYPE_NAMES.items()}

        def _to_codes(vals):
            out = []
            for s in vals:
                raw = (s or "").strip()
                # allow "DWG — Drawing": take the code on the left of the em dash
                code = raw.split("—", 1)[0].strip().upper()
                if code in DOC_TYPE_NAMES:  # already a code
                    out.append(code)
                    continue
                # try mapping name -> code
                mapped = inverse.get(raw.upper())
                out.append(mapped if mapped else code)
            # de-dupe while keeping order
            seen = set();
            out2 = []
            for c in out:
                if c and c not in seen:
                    seen.add(c);
                    out2.append(c)
            return out2

        payload = {
            "doc_types": _to_codes(self.doc_types.values()),
            "file_types": self.file_types.values(),
            "statuses": self.statuses.values(),
        }
        self._save_cb(payload)
        self.accept()
