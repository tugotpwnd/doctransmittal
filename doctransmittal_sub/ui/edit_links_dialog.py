# ui/edit_links_dialog.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import os, re, difflib

from PyQt5.QtCore import Qt, QSortFilterProxyModel, QAbstractTableModel, QModelIndex
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QSplitter, QWidget, QLineEdit, QPushButton,
    QFileDialog, QLabel, QTableView, QMessageBox, QStyle, QAbstractItemView, QToolBar
)

from ..services import db as regdb
from ..services.sharepoint_links import sp_url_from_local_path

# -------- helpers --------
def _stem(p: Path) -> str:
    return p.stem.upper()

def _norm(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[\s_\-\.]+", "", s)          # remove separators
    s = re.sub(r"(REV|R)[0-9A-Z]*$", "", s)   # drop common trailing rev tags
    return s

def best_filename_match(doc_id: str, candidates: List[Path]) -> Tuple[Optional[Path], float]:
    """Return (best_path, score 0..1) comparing normalized doc_id vs filename stem."""
    if not candidates: return (None, 0.0)
    key = _norm(doc_id)
    best_p, best_s = None, 0.0
    for p in candidates:
        s = difflib.SequenceMatcher(a=key, b=_norm(_stem(p))).ratio()
        # small bias if numeric suffix matches (DWG-001 vs ...001.*)
        m_doc = re.search(r"(\d{2,})$", doc_id)
        m_fil = re.search(r"(\d{2,})$", _stem(p))
        if m_doc and m_fil and m_doc.group(1) == m_fil.group(1):
            s += 0.05
        if s > best_s:
            best_s, best_p = s, p
    return (best_p, min(best_s, 1.0))

# -------- models --------
class DocsModel(QAbstractTableModel):
    COLS = ["Doc ID", "Type", "File", "Description", "Status", "Matched File", "Score"]
    def __init__(self, rows: List[Dict], parent=None):
        super().__init__(parent)
        self.rows = rows
        # UI state: current chosen file per row
        for r in self.rows:
            r["_chosen"] = None
            r["_score"] = 0.0

    def rowCount(self, parent=QModelIndex()): return len(self.rows)
    def columnCount(self, parent=QModelIndex()): return len(self.COLS)

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if role != Qt.DisplayRole: return None
        return self.COLS[section] if orient == Qt.Horizontal else section + 1

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid(): return None
        r = self.rows[idx.row()]
        c = idx.column()
        if role == Qt.DisplayRole:
            if c == 0: return r["doc_id"]
            if c == 1: return r["file_type"] or r["doc_type"] or ""
            if c == 2: return r.get("sp_url") or r.get("local_hint") or ""
            if c == 3: return r["description"] or ""
            if c == 4: return r["status"] or ""
            if c == 5: return str(r["_chosen"]) if r["_chosen"] else ""
            if c == 6: return f"{r['_score']:.2f}" if r["_score"] else ""
        if role == Qt.ToolTipRole and c == 5 and r["_chosen"]:
            return str(r["_chosen"])
        return None

    def set_choice(self, row: int, path: Optional[Path], score: float = 0.0):
        if row < 0 or row >= len(self.rows): return
        self.rows[row]["_chosen"] = Path(path) if path else None
        self.rows[row]["_score"] = score or 0.0
        tl = self.index(row, 5); br = self.index(row, 6)
        self.dataChanged.emit(tl, br, [Qt.DisplayRole, Qt.ToolTipRole])

    def clear_choice(self, row: int):
        self.set_choice(row, None, 0.0)

    def item(self, row: int) -> Dict:
        return self.rows[row]

class FilesModel(QAbstractTableModel):
    COLS = ["Name", "Ext", "Folder", "Size (kB)"]
    def __init__(self, files: List[Path] | None = None, parent=None):
        super().__init__(parent)
        self.files = files or []

    def rowCount(self, parent=QModelIndex()): return len(self.files)
    def columnCount(self, parent=QModelIndex()): return len(self.COLS)

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if role != Qt.DisplayRole: return None
        return self.COLS[section] if orient == Qt.Horizontal else section + 1

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid(): return None
        p = self.files[idx.row()]
        c = idx.column()
        if role == Qt.DisplayRole:
            if c == 0: return p.name
            if c == 1: return p.suffix.lower()
            if c == 2: return str(p.parent)
            if c == 3:
                try: return f"{round(p.stat().st_size/1024):,}"
                except Exception: return ""
        if role == Qt.ToolTipRole:
            return str(p)
        return None

    def add_files(self, ps: List[Path]):
        self.beginResetModel()
        seen = {str(p): p for p in self.files}
        for p in ps:
            if str(p) not in seen:
                seen[str(p)] = p
        self.files = list(seen.values())
        self.files.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
        self.endResetModel()

    def remove_rows(self, rows: List[int]):
        if not rows: return
        for i in sorted(rows, reverse=True):
            if 0 <= i < len(self.files):
                del self.files[i]
        self.layoutChanged.emit()

# -------- dialog --------
class EditLinksDialog(QDialog):
    def __init__(self, db_path: Path, project_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Links")
        self.resize(1300, 720)
        self.db_path = Path(db_path)
        self.project_id = int(project_id)

        # left: documents
        docs = regdb.list_documents_basic(self.db_path, self.project_id)
        self.docs_model = DocsModel(docs, self)
        self.docs_proxy = QSortFilterProxyModel(self); self.docs_proxy.setSourceModel(self.docs_model); self.docs_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.tv_docs = QTableView(self); self.tv_docs.setModel(self.docs_proxy)
        self.tv_docs.setSelectionBehavior(QTableView.SelectRows)
        self.tv_docs.setSelectionMode(QTableView.ExtendedSelection)
        self.tv_docs.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tv_docs.setSortingEnabled(True)
        self.tv_docs.sortByColumn(0, Qt.AscendingOrder)

        # right: files
        self.files_model = FilesModel([], self)
        self.files_proxy = QSortFilterProxyModel(self); self.files_proxy.setSourceModel(self.files_model); self.files_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.tv_files = QTableView(self); self.tv_files.setModel(self.files_proxy)
        self.tv_files.setSelectionBehavior(QTableView.SelectRows)
        self.tv_files.setSelectionMode(QTableView.ExtendedSelection)
        self.tv_files.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tv_files.setSortingEnabled(True)

        # filters
        self.le_filter_docs = QLineEdit(self); self.le_filter_docs.setPlaceholderText("Filter documents…")
        self.le_filter_docs.textChanged.connect(self.docs_proxy.setFilterFixedString)
        self.le_filter_files = QLineEdit(self); self.le_filter_files.setPlaceholderText("Filter files…")
        self.le_filter_files.textChanged.connect(self.files_proxy.setFilterFixedString)

        # file loaders (folder / files)
        self.btn_choose_folder = QPushButton("Add Folder…", self); self.btn_choose_folder.clicked.connect(self._choose_folder)
        self.btn_add_files    = QPushButton("Add Files…", self);  self.btn_add_files.clicked.connect(self._add_files)
        self.btn_remove_files = QPushButton("Remove Selected", self); self.btn_remove_files.clicked.connect(self._remove_files)

        # assign/unassign & auto
        self.btn_assign = QPushButton("Assign →", self); self.btn_assign.clicked.connect(self._assign_selected)
        self.btn_unassign = QPushButton("← Unassign", self); self.btn_unassign.clicked.connect(self._unassign_selected)
        self.btn_auto = QPushButton("Auto-match All", self); self.btn_auto.clicked.connect(self._auto_match_all)

        # save/close
        self.btn_save = QPushButton("Save Links", self); self.btn_save.clicked.connect(self._save_links)
        self.btn_close = QPushButton("Close", self); self.btn_close.clicked.connect(self.accept)

        # layout
        left_box = QVBoxLayout(); left_box.addWidget(QLabel("Documents")); left_box.addWidget(self.le_filter_docs); left_box.addWidget(self.tv_docs)
        left = QWidget(self); left.setLayout(left_box)

        mid_box = QVBoxLayout()
        mid_box.addStretch(1)
        mid_box.addWidget(self.btn_assign)
        mid_box.addWidget(self.btn_unassign)
        mid_box.addSpacing(12)
        mid_box.addWidget(self.btn_auto)
        mid_box.addStretch(1)
        mid = QWidget(self); mid.setLayout(mid_box)

        right_top = QHBoxLayout()
        right_top.addWidget(QLabel("Files"))
        right_top.addStretch(1)
        right_top.addWidget(self.btn_choose_folder)
        right_top.addWidget(self.btn_add_files)
        right_top.addWidget(self.btn_remove_files)
        right_box = QVBoxLayout(); right_box.addLayout(right_top); right_box.addWidget(self.le_filter_files); right_box.addWidget(self.tv_files)
        right = QWidget(self); right.setLayout(right_box)

        split = QSplitter(self); split.addWidget(left); split.addWidget(mid); split.addWidget(right); split.setStretchFactor(0, 3); split.setStretchFactor(2, 3)

        root = QVBoxLayout(self)
        root.addWidget(split)
        bottom = QHBoxLayout(); bottom.addStretch(1); bottom.addWidget(self.btn_save); bottom.addWidget(self.btn_close)
        root.addLayout(bottom)
        self.setLayout(root)

        # double-click convenience
        self.tv_files.doubleClicked.connect(self._assign_selected)
        self.tv_docs.doubleClicked.connect(self._unassign_selected)

    # ---- file loading ----
    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to scan")
        if not folder: return
        exts = {".pdf", ".dwg", ".dgn", ".docx", ".doc", ".xlsx", ".xlsm", ".xls", ".pptx", ".ppt", ".vsdx", ".mpp"}
        files = []
        for root, _, names in os.walk(folder):
            for n in names:
                p = Path(root) / n
                if p.suffix.lower() in exts:
                    files.append(p)
        self.files_model.add_files(files)

    def _add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Add files", "", "All Files (*.*)")
        if not ps: return
        self.files_model.add_files([Path(p) for p in ps])

    def _remove_files(self):
        rows = [i.row() for i in self.tv_files.selectionModel().selectedRows()]
        # convert through proxy
        src_rows = [self.files_proxy.mapToSource(self.files_proxy.index(r,0)).row() for r in rows]
        self.files_model.remove_rows(src_rows)

    # ---- assign/unassign ----
    def _assign_selected(self):
        # map selected doc rows
        drows = [i.row() for i in self.tv_docs.selectionModel().selectedRows()]
        if not drows:
            return
        dsrc = [self.docs_proxy.mapToSource(self.docs_proxy.index(r,0)).row() for r in drows]
        frows = [i.row() for i in self.tv_files.selectionModel().selectedRows()]
        if not frows:
            return
        fsrc = [self.files_proxy.mapToSource(self.files_proxy.index(r,0)).row() for r in frows]
        # assign one-to-one in order
        for i, dr in enumerate(dsrc):
            fr = fsrc[min(i, len(fsrc)-1)]
            p = self.files_model.files[fr]
            score = difflib.SequenceMatcher(a=_norm(self.docs_model.item(dr)["doc_id"]), b=_norm(_stem(p))).ratio()
            self.docs_model.set_choice(dr, p, score)

    def _unassign_selected(self):
        drows = [i.row() for i in self.tv_docs.selectionModel().selectedRows()]
        if not drows: return
        dsrc = [self.docs_proxy.mapToSource(self.docs_proxy.index(r,0)).row() for r in drows]
        for r in dsrc:
            self.docs_model.clear_choice(r)

    def _auto_match_all(self):
        if not self.files_model.files:
            QMessageBox.information(self, "Auto-match", "Add files or a folder on the right first.")
            return
        files = list(self.files_model.files)
        for i in range(self.docs_model.rowCount()):
            doc = self.docs_model.item(i)
            p, score = best_filename_match(doc["doc_id"], files)
            # only accept plausible matches; tune threshold as needed
            if p and score >= 0.65:
                self.docs_model.set_choice(i, p, score)
            else:
                self.docs_model.clear_choice(i)
        QMessageBox.information(self, "Auto-match", "Auto-matching complete. Review ‘Matched File’ and Score columns.")

    # ---- save ----
    def _save_links(self):
        changed = 0
        missing = []
        for i in range(self.docs_model.rowCount()):
            doc = self.docs_model.item(i)
            chosen: Optional[Path] = doc.get("_chosen")
            if not chosen:
                continue
            sp = sp_url_from_local_path(chosen)
            if not sp:
                missing.append((doc["doc_id"], str(chosen)))
                # still store local_hint so you can resolve later
                regdb.set_document_sp_link(self.db_path, self.project_id, doc["doc_id"], "", str(chosen))
            else:
                regdb.set_document_sp_link(self.db_path, self.project_id, doc["doc_id"], sp, str(chosen))
            changed += 1

        if missing:
            msg = "Saved links, but some files are not in a synced SharePoint library:\n\n"
            msg += "\n".join(f"• {d}  ←  {p}" for d, p in missing[:10])
            if len(missing) > 10: msg += f"\n…and {len(missing)-10} more."
            msg += "\n\nThose rows were saved with a local hint and can be back-filled later once the library is synced."
            QMessageBox.warning(self, "Saved with warnings", msg)
        else:
            QMessageBox.information(self, "Saved", f"Saved links for {changed} document(s).")
        self.accept()
