from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QDialogButtonBox, QPushButton, QFileDialog, QHeaderView, QMessageBox,
    QFormLayout, QLineEdit, QComboBox
)
from PyQt5.QtCore import Qt

from ..services.templates_store import CATEGORIES, CATEGORY_LABELS, CATEGORY_FROM_LABEL, KINDS, KIND_FROM_LABEL, \
    load_templates, resolve_abs_path, templates_json_path, DEFAULT_ORG, DEFAULT_LIBRARY, save_templates, KIND_LABELS

from ..core.paths import company_library_root

# Column indices
COL_DOC_ID   = 0
COL_DESC     = 1
COL_CAT      = 2
COL_KIND     = 3
COL_REV      = 4
COL_RELPATH  = 5
COL_ABS      = 6


def _norm_doc_id(s: str) -> str:
    return (s or "").strip().lower()

def _norm_relpath(s: str) -> str:
    return (s or "").strip().replace("\\", "/").lower()

def _find_dupe(items: List[Dict], ignore_index: Optional[int] = None) -> Optional[Tuple[str, int, int]]:
    seen_id: Dict[str, int] = {}
    seen_path: Dict[str, int] = {}
    for i, it in enumerate(items):
        if ignore_index is not None and i == ignore_index:
            continue
        did = _norm_doc_id(it.get("doc_id", ""))
        rlp = _norm_relpath(it.get("relpath", ""))
        if did:
            if did in seen_id:
                return ("doc_id", seen_id[did], i)
            seen_id[did] = i
        if rlp:
            if rlp in seen_path:
                return ("relpath", seen_path[rlp], i)
            seen_path[rlp] = i
    return None

class AddTemplateMiniDialog(QDialog):
    def __init__(self, parent=None, init: Optional[Dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Template")
        self.resize(600, 230)
        self.doc_id = ""
        self.description = ""
        self.revision = ""
        self.category_key = ""  # internal key
        self.kind_key = ""  # internal key
        self.relpath = ""

        self.le_doc_id = QLineEdit(self)
        self.le_desc   = QLineEdit(self)
        self.le_rev    = QLineEdit(self)

        self.cb_cat    = QComboBox(self)
        for k, label in CATEGORIES:
            self.cb_cat.addItem(label, userData=k)
        self.cb_cat.setCurrentIndex(0)

        self.le_rel    = QLineEdit(self); self.le_rel.setReadOnly(True)
        self.btn_browse = QPushButton("Browse…", self)
        self.btn_browse.clicked.connect(self._browse_for_file)

        self.cb_kind = QComboBox(self)
        for k, label in KINDS:
            self.cb_kind.addItem(label, userData=k)
        self.cb_kind.setCurrentIndex(0)

        form = QFormLayout()
        form.addRow("Doc ID", self.le_doc_id)
        form.addRow("Description", self.le_desc)
        form.addRow("Category", self.cb_cat)
        form.addRow("Kind", self.cb_kind)
        form.addRow("Revision", self.le_rev)


        path_row = QHBoxLayout()
        path_row.addWidget(self.le_rel, 1)
        path_row.addWidget(self.btn_browse)
        form.addRow("Relative Path", path_row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

        if init:
            self.le_doc_id.setText(init.get("doc_id", ""))
            self.le_desc.setText(init.get("description", ""))
            self.le_rev.setText(init.get("revision", ""))

            init_cat_key = init.get("category") or CATEGORY_FROM_LABEL.get(init.get("category_label",""), "")
            if init_cat_key:
                idx = max(0, self.cb_cat.findData(init_cat_key))
                self.cb_cat.setCurrentIndex(idx)

            init_kind_key = init.get("kind") or KIND_FROM_LABEL.get(init.get("kind_label", ""), "")
            if init_kind_key:
                idx = max(0, self.cb_kind.findData(init_kind_key))
                self.cb_kind.setCurrentIndex(idx)

            self.le_rel.setText(init.get("relpath", ""))

    def _browse_for_file(self):
        lib_root = company_library_root()
        start_dir = lib_root / "0. MIMS" / "4. Document Templates" / "6 Engineering"
        start_dir = start_dir if start_dir.exists() else lib_root
        path, _ = QFileDialog.getOpenFileName(
            self, "Select template file", str(start_dir),
            "Office files (*.docx *.docm *.xlsx *.xlsm);;All files (*.*)"
        )
        if not path:
            return
        p = Path(path)
        try:
            rel = p.relative_to(lib_root).as_posix()
        except ValueError:
            QMessageBox.warning(self, "Not under library",
                                f"Selected file is not under the synced library:\n{lib_root}")
            return
        self.le_rel.setText(rel)

    def _ok(self):
        self.doc_id = (self.le_doc_id.text() or "").strip()
        self.description = (self.le_desc.text() or "").strip()
        self.revision = (self.le_rev.text() or "").strip()
        self.relpath = (self.le_rel.text() or "").strip()
        self.category_key = self.cb_cat.currentData() or "document"
        self.kind_key = self.cb_kind.currentData() or "excel"
        if not (self.doc_id and self.description and self.relpath):
            QMessageBox.information(self, "Template", "Doc ID, Description and Relative Path are required.")
            return
        self.accept()

class TemplatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Templates")
        self.resize(980, 520)

        self.current_json: Path = templates_json_path()
        self.org = DEFAULT_ORG
        self.library = DEFAULT_LIBRARY

        self.tbl = QTableWidget(self)
        self.tbl.setColumnCount(7)
        self.tbl.setHorizontalHeaderLabels(
            ["Doc ID", "Description", "Category", "Kind", "Revision", "Relative Path", "Resolved Path"]
        )
        self.tbl.horizontalHeader().setSectionResizeMode(COL_DOC_ID,  QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(COL_DESC,    QHeaderView.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(COL_CAT,     QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(COL_KIND, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(COL_REV,     QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(COL_RELPATH, QHeaderView.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(COL_ABS,     QHeaderView.Stretch)
        self.tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setAlternatingRowColors(False)  # let your global theme paint rows (dark bg, white text)

        btns_row = QHBoxLayout()
        self.btn_open   = QPushButton("Open JSON…")
        self.btn_add    = QPushButton("Add…")
        self.btn_edit   = QPushButton("Edit…")
        self.btn_remove = QPushButton("Remove")
        self.btn_save   = QPushButton("Save")
        btns_row.addWidget(self.btn_open)
        btns_row.addStretch(1)
        btns_row.addWidget(self.btn_add)
        btns_row.addWidget(self.btn_edit)
        btns_row.addWidget(self.btn_remove)
        btns_row.addWidget(self.btn_save)

        bb = QDialogButtonBox(QDialogButtonBox.Close, self)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(btns_row)
        lay.addWidget(self.tbl, 1)
        lay.addWidget(bb)

        self.btn_open.clicked.connect(self._open_json)
        self.btn_add.clicked.connect(self._add_row)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_save.clicked.connect(self._save)

        self._reload(self.current_json)

    def _open_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select templates.json",
                                              str(self.current_json.parent), "JSON (*.json)")
        if path:
            self._reload(Path(path))

    def _reload(self, path: Path):
        self.current_json = Path(path)
        rows = load_templates(self.current_json)
        self.tbl.setRowCount(len(rows))
        for r, t in enumerate(rows):
            self._set_row(r, t)
        if rows:
            self.tbl.resizeRowsToContents()

    def _set_row(self, r: int, t: Dict):
        # Doc ID
        self.tbl.setItem(r, COL_DOC_ID, QTableWidgetItem(t.get("doc_id", "")))
        # Description
        self.tbl.setItem(r, COL_DESC, QTableWidgetItem(t.get("description", "")))
        # Category (show label, store key in UserRole)
        cat_label = t.get("category_label") or CATEGORY_LABELS.get(t.get("category","document"), "Report/Document/Register")
        cat_key   = t.get("category") or ""
        cat_item  = QTableWidgetItem(cat_label)
        cat_item.setData(Qt.UserRole, cat_key)
        self.tbl.setItem(r, COL_CAT, cat_item)
        # Kind (show label, store key in UserRole)
        kind_label = t.get("kind_label") or KIND_LABELS.get(t.get("kind", "excel"), "Excel")
        kind_key = t.get("kind") or ""
        kind_item = QTableWidgetItem(kind_label)
        kind_item.setData(Qt.UserRole, kind_key)
        self.tbl.setItem(r, COL_KIND, kind_item)
        # Revision
        self.tbl.setItem(r, COL_REV, QTableWidgetItem(t.get("revision", "")))
        # Rel + Abs
        rel = t.get("relpath", "")
        abs_ = t.get("abs_path", "") or str(resolve_abs_path({"relpath": rel}))
        rel_item = QTableWidgetItem(rel)
        abs_item = QTableWidgetItem(abs_); abs_item.setToolTip(abs_)
        self.tbl.setItem(r, COL_RELPATH, rel_item)
        self.tbl.setItem(r, COL_ABS, abs_item)

    def _gather_rows(self) -> List[Dict]:
        items = []
        for r in range(self.tbl.rowCount()):
            # Category key stored in UserRole (fallback: map from label)
            cat_item = self.tbl.item(r, COL_CAT)
            cat_key = (cat_item.data(Qt.UserRole) if cat_item else None) or ""
            if not cat_key and cat_item:
                cat_key = CATEGORY_FROM_LABEL.get(cat_item.text().strip(), "document")
            # Kind key stored in UserRole (fallback: map from label)
            kind_item = self.tbl.item(r, COL_KIND)
            kind_key = (kind_item.data(Qt.UserRole) if kind_item else None) or ""
            if not kind_key and kind_item:
                kind_key = KIND_FROM_LABEL.get(kind_item.text().strip(), "excel")

            items.append({
                "doc_id":  self.tbl.item(r, COL_DOC_ID).text().strip()  if self.tbl.item(r, COL_DOC_ID)  else "",
                "description": self.tbl.item(r, COL_DESC).text().strip() if self.tbl.item(r, COL_DESC)    else "",
                "revision": self.tbl.item(r, COL_REV).text().strip()     if self.tbl.item(r, COL_REV)     else "",
                "category": cat_key or "document",
                "kind": kind_key or "excel",
                "relpath":  self.tbl.item(r, COL_RELPATH).text().strip().replace("\\","/") if self.tbl.item(r, COL_RELPATH) else "",
            })
        return items

    def _validate_unique_or_warn(self, items: List[Dict], *, ignore_index: Optional[int] = None) -> bool:
        dupe = _find_dupe(items, ignore_index=ignore_index)
        if not dupe:
            return True
        col, i1, i2 = dupe
        pretty = "Doc ID" if col == "doc_id" else "Relative Path"
        QMessageBox.warning(self, "Duplicate", f"{pretty} must be unique.\nConflict between rows {i1+1} and {i2+1}.")
        return False

    def _save(self):
        items = self._gather_rows()
        if not self._validate_unique_or_warn(items):
            return
        save_templates(items, org=self.org, library=self.library, path=self.current_json)
        QMessageBox.information(self, "Templates", "Saved.")

    def _remove_selected(self):
        sel = self.tbl.selectionModel().selectedRows()
        if not sel:
            return
        for idx in sorted(sel, key=lambda m: m.row(), reverse=True):
            self.tbl.removeRow(idx.row())

    def _add_row(self):
        dlg = AddTemplateMiniDialog(self)
        if dlg.exec_() != dlg.Accepted:
            return
        new_item = {
            "doc_id": dlg.doc_id,
            "description": dlg.description,
            "revision": dlg.revision,
            "category": dlg.category_key,
            "kind": dlg.kind_key,
            "relpath": dlg.relpath,
        }
        cur = self._gather_rows()
        cur.append(new_item)
        if not self._validate_unique_or_warn(cur):
            return
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)
        self._set_row(row, {**new_item, "abs_path": str(resolve_abs_path({"relpath": dlg.relpath}))})
        self.tbl.resizeRowToContents(row)

    def _edit_selected(self):
        sel = self.tbl.selectionModel().selectedRows()
        if not sel:
            QMessageBox.information(self, "Edit Template", "Select a single row to edit.")
            return
        if len(sel) > 1:
            QMessageBox.information(self, "Edit Template", "Please edit one row at a time.")
            return
        r = sel[0].row()
        init = {
            "doc_id":      self.tbl.item(r, COL_DOC_ID).text() if self.tbl.item(r, COL_DOC_ID) else "",
            "description": self.tbl.item(r, COL_DESC).text() if self.tbl.item(r, COL_DESC) else "",
            "revision":    self.tbl.item(r, COL_REV).text() if self.tbl.item(r, COL_REV) else "",
            "category":    (self.tbl.item(r, COL_CAT).data(Qt.UserRole) if self.tbl.item(r, COL_CAT) else "") or "",
            "kind":         (self.tbl.item(r, COL_KIND).data(Qt.UserRole) if self.tbl.item(r, COL_KIND) else "") or "",
            "category_label": self.tbl.item(r, COL_CAT).text() if self.tbl.item(r, COL_CAT) else "",
            "kind_label": self.tbl.item(r, COL_KIND).text() if self.tbl.item(r, COL_KIND) else "",
            "relpath":     self.tbl.item(r, COL_RELPATH).text() if self.tbl.item(r, COL_RELPATH) else "",
        }
        dlg = AddTemplateMiniDialog(self, init=init)
        if dlg.exec_() != dlg.Accepted:
            return
        items = self._gather_rows()
        items[r] = {
            "doc_id": dlg.doc_id,
            "description": dlg.description,
            "revision": dlg.revision,
            "category": dlg.category_key,
            "kind": dlg.kind_key,
            "relpath": dlg.relpath,
        }

        if not self._validate_unique_or_warn(items, ignore_index=r):
            return
        self._set_row(r, {
            "doc_id": dlg.doc_id,
            "description": dlg.description,
            "revision": dlg.revision,
            "category": dlg.category_key,
            "kind": dlg.kind_key,
            "relpath": dlg.relpath,
            "abs_path": str(resolve_abs_path({"relpath": dlg.relpath}))
        })

        self.tbl.resizeRowToContents(r)
