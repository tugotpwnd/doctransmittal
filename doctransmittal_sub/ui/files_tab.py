from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional

from PyQt5.QtCore import Qt, QModelIndex, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QPalette
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QListWidget, QListWidgetItem, QTreeView, QFileSystemModel,
    QPushButton, QLabel, QMessageBox, QFileDialog,
    QStyledItemDelegate, QStyleOptionViewItem, QLineEdit
)

# --- Autofind helpers ---
try:
    from ..services.autofind import suggest_mapping, find_docid_rev_matches  # type: ignore
except Exception:
    try:
        from ..autofind import suggest_mapping, find_docid_rev_matches  # type: ignore
    except Exception:
        from autofind import suggest_mapping, find_docid_rev_matches  # type: ignore

# --- Transmittal service ---
try:
    from ..services.transmittal_service import create_transmittal, edit_transmittal_replace_items  # ADD
except Exception:
    try:
        from ..transmittal_service import create_transmittal, edit_transmittal_replace_items  # ADD
    except Exception:
        from transmittal_service import create_transmittal, edit_transmittal_replace_items  # ADD




class _MapHighlightDelegate(QStyledItemDelegate):
    """Tints any mapped file (exact path) in the file tree as green text."""
    def __init__(self, files_tab: 'FilesTab', parent=None):
        super().__init__(parent)
        self._tab = files_tab
        self._green = QColor(46, 160, 67)

    def paint(self, painter, option, index):
        try:
            model = index.model()
            fp = model.filePath(index)
            if fp and not model.isDir(index) and fp in self._tab._used_paths_set():
                opt = QStyleOptionViewItem(option)
                opt.palette.setColor(QPalette.Text, self._green)
                return super().paint(painter, opt, index)
        except Exception:
            pass
        return super().paint(painter, option, index)


class FilesTab(QWidget):
    backRequested = pyqtSignal()
    proceedCompleted = pyqtSignal(str)  # emits transmittal directory path on success
    remapCompleted = pyqtSignal(str, str)        # (transmittal_number, dir_path) after edit/remap

    def __init__(self, parent=None):
        super().__init__(parent)

        # Core state
        self.db_path: Optional[Path] = None
        self.root_dir: Optional[Path] = None
        self.items: List[dict] = []
        self.doc_ids: List[str] = []
        self.mapping: Dict[str, str] = {}  # {doc_id -> absolute path}
        self.user = self.title = self.client = ""
        # Edit/remap state (non-destructive additions)
        self._edit_mode: bool = False
        self._edit_transmittal_number: Optional[str] = None


        # ===== UI =====
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal, self)

        # LEFT: File tree
        left_box = QGroupBox("File tree", self)
        left_v = QVBoxLayout(left_box)

        self.model = QFileSystemModel(self)
        self.model.setRootPath("")
        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(False)
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.doubleClicked.connect(self._on_tree_double_clicked)
        self.tree.setItemDelegate(_MapHighlightDelegate(self, self.tree))

        self.btn_choose_root = QPushButton("Choose Root Folder…", self)
        self.btn_choose_root.clicked.connect(self._choose_root)

        self.btn_map = QPushButton("Map Selected", self)
        self.btn_map.setToolTip("Map the selected DocID (middle) to the selected file (left).")
        self.btn_map.clicked.connect(self._map_selected)

        left_v.addWidget(self.btn_choose_root)
        left_v.addWidget(self.tree, 1)
        left_v.addWidget(self.btn_map)
        splitter.addWidget(left_box)

        # MIDDLE: Doc IDs
        mid_box = QGroupBox("Files for transmittal", self)
        mid_v = QVBoxLayout(mid_box)
        self.list_docs = QListWidget(self)
        mid_v.addWidget(self.list_docs, 1)
        splitter.addWidget(mid_box)

        # RIGHT: Mapped files
        right_box = QGroupBox("Mapped files", self)
        right_v = QVBoxLayout(right_box)

        # Rematch actions
        actions_row = QHBoxLayout()
        self.btn_auto_exact = QPushButton("Exact Match (DocID_Rev)", self)
        self.btn_auto_exact.setToolTip("Re-run exact matching across all docs.")
        self.btn_auto_exact.clicked.connect(self._auto_find_exact)

        self.btn_auto_fuzzy = QPushButton("Fuzzy Auto-Find", self)
        self.btn_auto_fuzzy.setToolTip("Re-run fuzzy suggestions across all docs.")
        self.btn_auto_fuzzy.clicked.connect(self._auto_find_fuzzy)

        actions_row.addWidget(self.btn_auto_exact)
        actions_row.addWidget(self.btn_auto_fuzzy)
        right_v.addLayout(actions_row)

        self.list_map = QListWidget(self)
        right_v.addWidget(self.list_map, 1)

        rm_row = QHBoxLayout()
        self.btn_unmap = QPushButton("Remove Mapping", self)
        self.btn_unmap.clicked.connect(self._unmap_selected)

        self.btn_clear = QPushButton("Clear All", self)
        self.btn_clear.clicked.connect(self._clear_all)

        rm_row.addWidget(self.btn_unmap)
        rm_row.addWidget(self.btn_clear)
        right_v.addLayout(rm_row)

        self.lbl_status = QLabel("0 / 0 mapped", self)
        right_v.addWidget(self.lbl_status)

        splitter.addWidget(right_box)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        root.addWidget(splitter)

        # Bottom nav
        nav = QHBoxLayout()
        self.btn_back = QPushButton("◀ Back", self)
        self.btn_back.clicked.connect(lambda: self.backRequested.emit())
        nav.addWidget(self.btn_back)

        nav.addStretch(1)
        nav.addWidget(QLabel("Submission date:", self))
        self.le_date = QLineEdit(self)
        self.le_date.setPlaceholderText("DD/MM/YYYY or DD/MM/YYYY HH:MM")
        self.le_date.setFixedWidth(180)
        nav.addWidget(self.le_date)

        self.btn_proceed = QPushButton("Proceed: Build Transmittal ▶", self)
        self.btn_proceed.setToolTip("Copies mapped files and generates a receipt PDF.")
        self.btn_proceed.clicked.connect(self._proceed_build_transmittal)
        nav.addWidget(self.btn_proceed)

        root.addLayout(nav)

    # ===== Public API =====
    def set_flow_context(self, *, db_path: Path, items: List[dict],
                         file_mapping: Dict[str, str], user: str, title: str, client: str,
                         created_on: str = ""):
        self.db_path = Path(db_path) if db_path else None
        self.items = list(items or [])
        self.doc_ids = [(it.get("doc_id") or "").strip() for it in self.items if it.get("doc_id")]
        # Normalize provided mapping
        self.mapping = {k: self._normpath(v) for k, v in (file_mapping or {}).items() if k}
        # Merge any item-carried path if not already set
        for it in self.items:
            d = (it.get("doc_id") or "").strip()
            p = (it.get("file_path") or "").strip()
            if d and p and d not in self.mapping:
                self.mapping[d] = self._normpath(p)
        self.user, self.title, self.client = user, title, client
        self._refresh_doc_list()
        self._refresh_map_list()

        # Prefill date (if ISO in payload, show as DD/MM/YYYY or DD/MM/YYYY HH:MM)
        try:
            disp = (created_on or "").strip()
            if len(disp) >= 10 and disp[4:5] == "-" and disp[7:8] == "-":
                from datetime import datetime as _dt
                fmt_in = "%Y-%m-%d %H:%M" if ":" in disp else "%Y-%m-%d"
                dt = _dt.strptime(disp, fmt_in)
                disp = dt.strftime("%d/%m/%Y %H:%M") if ":" in disp else dt.strftime("%d/%m/%Y")
            if not disp:
                from datetime import date as _d
                disp = _d.today().strftime("%d/%m/%Y")
            if hasattr(self, "le_date"):
                self.le_date.setText(disp)
        except Exception:
            pass

    def set_flow_context_edit(self, payload: dict):
        """
        Start Files tab in EDIT mode from History tab.
        payload keys:
          - db_path, items, file_mapping, user, title, client, created_on
          - transmittal_number (required)
        """
        self._edit_mode = True
        self._edit_transmittal_number = (payload.get("transmittal_number") or "").strip() or None

        self.set_flow_context(
            db_path=payload.get("db_path"),
            items=payload.get("items") or [],
            file_mapping=payload.get("file_mapping") or {},
            user=payload.get("user", ""),
            title=payload.get("title", ""),
            client=payload.get("client", ""),
            created_on=(payload.get("created_on") or ""),
        )

        try:
            self.btn_proceed.setText("Update Transmittal ▶")
        except Exception:
            pass

    def get_mapping(self) -> Dict[str, str]:
        return dict(self.mapping)

    def reset(self):
        """Clear all state and UI for a fresh run."""
        self.db_path = None
        self.root_dir = None
        self.items = []
        self.doc_ids = []
        self.mapping = {}
        self.user = self.title = self.client = ""
        self.list_docs.clear()
        self.list_map.clear()
        self.lbl_status.setText("0 / 0 mapped")
        self._edit_mode = False
        self._edit_transmittal_number = None

        # Reset tree to its root
        try:
            self.tree.setRootIndex(self.model.index(self.model.rootPath()))
        except Exception:
            pass
        self.tree.viewport().update()

    # ===== Internals =====
    def _normpath(self, p: str) -> str:
        try:
            return str(Path(p).resolve())
        except Exception:
            return str(Path(p))

    def _used_paths_set(self) -> set:
        return {self._normpath(v) for v in self.mapping.values() if v}

    def _find_doc_for_path(self, p: str) -> Optional[str]:
        np = self._normpath(p)
        for d, v in self.mapping.items():
            if self._normpath(v) == np:
                return d
        return None

    def _doc_rev_pairs(self) -> List[tuple[str, str]]:
        pairs = []
        for it in self.items:
            did = (it.get("doc_id") or "").strip()
            rev = (it.get("revision") or "").strip()
            if did and rev:
                pairs.append((did, rev))
        return pairs

    def _display_path(self, p: Optional[str]) -> str:
        if not p:
            return "—"
        try:
            P = Path(p)
            if self.root_dir:
                return str(P.resolve().relative_to(self.root_dir.resolve()))
            return P.name
        except Exception:
            try:
                return Path(p).name
            except Exception:
                return str(p)

    def _current_doc_id(self) -> Optional[str]:
        it = self.list_docs.currentItem()
        return it.text().strip() if it else None

    def _current_tree_file(self) -> Optional[Path]:
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        p = Path(self.model.filePath(idx))
        return p if p.is_file() else None

    # ===== Refresh & coloring =====
    def _refresh_doc_list(self):
        self.list_docs.clear()

        # Build a quick lookup: {doc_id -> revision}
        rev_lookup = {}
        for it in (self.items or []):
            did = (it.get("doc_id") if isinstance(it, dict) else getattr(it, "doc_id", "")) or ""
            rev = ""
            if isinstance(it, dict):
                rev = (it.get("revision") or it.get("latest_rev_token") or it.get("latest_rev") or it.get(
                    "rev") or "").strip()
            else:
                rev = (getattr(it, "revision", "") or getattr(it, "latest_rev_token", "") or getattr(it, "latest_rev",
                                                                                                     "") or getattr(it,
                                                                                                                    "rev",
                                                                                                                    "") or "").strip()
            if did:
                rev_lookup[did] = rev

        # Render "DOCID  —  Rev X" (falls back to just DOCID if no rev)
        for d in self.doc_ids:
            rv = rev_lookup.get(d, "")
            label = f"{d}  —  Rev {rv}" if rv else d
            self.list_docs.addItem(QListWidgetItem(label))

        self._apply_colors()

    def _refresh_map_list(self):
        self.list_map.clear()
        for d in self.doc_ids:
            p = self.mapping.get(d)
            self.list_map.addItem(QListWidgetItem(f"{d}  →  {self._display_path(p)}"))
        n = sum(1 for d in self.doc_ids if self.mapping.get(d))
        self.lbl_status.setText(f"{n} / {len(self.doc_ids)} mapped")
        self._apply_colors()

    def _apply_colors(self):
        green = QBrush(QColor(46, 160, 67))
        red   = QBrush(QColor(200, 60, 60))
        for i, d in enumerate(self.doc_ids):
            it = self.list_docs.item(i)
            if it:
                it.setForeground(green if self.mapping.get(d) else red)
        for i, d in enumerate(self.doc_ids):
            it = self.list_map.item(i)
            if it:
                it.setForeground(green if self.mapping.get(d) else red)
        self.tree.viewport().update()

    # NEW: allow callers to prime the file tree's root without popping a dialog
    def set_root_folder(self, folder: str | Path):
        if not folder:
            return
        try:
            self.root_dir = Path(folder)
            self.tree.setRootIndex(self.model.index(str(self.root_dir)))
        except Exception:
            pass
        # refresh right list so relative paths display nicely against the chosen root
        self._refresh_map_list()

    # ===== Actions =====
    def _choose_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a root folder", "")
        if not path:
            return
        self.root_dir = Path(path)
        try:
            self.tree.setRootIndex(self.model.index(str(self.root_dir)))
        except Exception:
            pass
        self._refresh_map_list()

    def _map_selected(self):
        doc = self._current_doc_id()
        if not doc:
            QMessageBox.information(self, "Select Doc", "Highlight a Doc ID (middle) first.")
            return
        file_path = self._current_tree_file()
        if not file_path:
            QMessageBox.information(self, "Select File", "Select a file in the File tree (left).")
            return

        np = self._normpath(str(file_path))
        other = self._find_doc_for_path(np)
        if other and other != doc:
            rel = self._display_path(np)
            resp = QMessageBox.question(
                self, "Already mapped",
                f"This file is already mapped to {other}.\n\nReassign it to {doc} instead?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if resp != QMessageBox.Yes:
                return
            del self.mapping[other]
        self.mapping[doc] = np
        self._refresh_map_list()

    def _on_tree_double_clicked(self, idx: QModelIndex):
        self._map_selected()

    def _unmap_selected(self):
        doc = None
        row = self.list_map.currentRow()
        if 0 <= row < len(self.doc_ids):
            doc = self.doc_ids[row]
        if not doc:
            doc = self._current_doc_id()
        if not doc:
            QMessageBox.information(self, "Select Doc", "Select a Doc ID to unmap.")
            return
        if doc in self.mapping:
            del self.mapping[doc]
            self._refresh_map_list()

    def _clear_all(self):
        self.mapping.clear()
        self._refresh_map_list()

    def _auto_find_exact(self):
        if not self.root_dir:
            QMessageBox.information(self, "Pick a root", "Choose a root folder first.")
            return
        pairs = self._doc_rev_pairs()
        try:
            found = find_docid_rev_matches(pairs, [self.root_dir], extensions=None) or {}
        except Exception:
            found = {}
        assigned = 0
        skipped = 0
        used = self._used_paths_set()
        for d in self.doc_ids:
            p = found.get(d)
            if not p:
                continue
            np = self._normpath(str(p))
            current_owner = self._find_doc_for_path(np)
            if current_owner and current_owner != d:
                skipped += 1
                continue
            prev = self.mapping.get(d)
            if prev:
                used.discard(self._normpath(prev))
            if np not in used or current_owner == d:
                self.mapping[d] = np
                used.add(np)
                assigned += 1
            else:
                skipped += 1
        self._refresh_map_list()
        if skipped:
            QMessageBox.information(self, "Exact Match", f"Assigned: {assigned}\nSkipped (conflicts): {skipped}")

    def _auto_find_fuzzy(self):
        if not self.root_dir:
            QMessageBox.information(self, "Pick a root", "Choose a root folder first.")
            return
        try:
            guessed = suggest_mapping(self.doc_ids, [self.root_dir]) or {}
        except Exception:
            guessed = {}
        assigned = 0
        skipped = 0
        used = self._used_paths_set()
        for d in self.doc_ids:
            lst = guessed.get(d) or []
            if not lst:
                continue
            candidate_path = lst[0][0]
            np = self._normpath(str(candidate_path))
            current_owner = self._find_doc_for_path(np)
            if current_owner and current_owner != d:
                skipped += 1
                continue
            prev = self.mapping.get(d)
            if prev:
                used.discard(self._normpath(prev))
            if np not in used or current_owner == d:
                self.mapping[d] = np
                used.add(np)
                assigned += 1
            else:
                skipped += 1
        self._refresh_map_list()
        if skipped:
            QMessageBox.information(self, "Fuzzy Auto-Find", f"Assigned: {assigned}\nSkipped (conflicts): {skipped}")

    # ===== Proceed: Build Transmittal =====
    def _build_snapshot_items(self) -> List[dict]:
        """Return snapshot rows with file_path from current mapping (unmapped allowed)."""
        snap: List[dict] = []
        for it in self.items:
            d = (it.get("doc_id") or "").strip()
            if not d:
                continue
            row = dict(it)
            row["file_path"] = self.mapping.get(d, "")
            snap.append(row)
        return snap

    def _proceed_build_transmittal(self):
        if not self.db_path:
            QMessageBox.information(self, "Open Project", "Open a project database first.")
            return

        snap = self._build_snapshot_items()

        # --- EDIT/REMAP flow (new; non-destructive to original behavior) ---
        if self._edit_mode and self._edit_transmittal_number:
            try:
                trans_dir = edit_transmittal_replace_items(
                    db_path=self.db_path,
                    transmittal_number=self._edit_transmittal_number,
                    items=snap,
                )
            except Exception as e:
                QMessageBox.critical(self, "Remap", f"Failed to update transmittal:\n{e}")
                return

            QMessageBox.information(
                self, "Remap complete",
                f"Updated {self._edit_transmittal_number} and rebuilt.\n\n{trans_dir}"
            )
            # Notify MainWindow so it can bounce back to History
            self.remapCompleted.emit(self._edit_transmittal_number, str(trans_dir))
            return

        # --- ORIGINAL (NEW transmittal) flow stays the same below ---
        try:
            trans_dir = create_transmittal(
                db_path=self.db_path,
                out_root=None,  # default to …/Transmittals
                user_name=self.user or "",
                title=self.title or "",
                client=self.client or "",
                items=snap,
                created_on_str=(self.le_date.text().strip() if hasattr(self, "le_date") else None),
            )
        except Exception as e:
            QMessageBox.critical(self, "Transmittal", f"Failed to create transmittal:\n{e}")
            return

        QMessageBox.information(
            self, "Transmittal created",
            f"Your transmittal has been created:\n\n{trans_dir}\n\n"
            "Files were copied to the 'Files' subfolder and a PDF receipt was generated in 'Receipt/'."
        )

        # Existing signal—kept as-is for the new-transmittal flow
        self.proceedCompleted.emit(str(trans_dir))
