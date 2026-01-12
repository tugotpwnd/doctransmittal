from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from PyQt5.QtCore import Qt, QModelIndex, pyqtSignal, QUrl
from PyQt5.QtGui import QColor, QBrush, QPalette
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QListWidget, QListWidgetItem, QTreeView, QFileSystemModel,
    QPushButton, QLabel, QMessageBox, QFileDialog,
    QStyledItemDelegate, QStyleOptionViewItem, QLineEdit,
    QAbstractItemView, QToolButton, QMenu,
)
from PyQt5.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent

# --- Autofind helpers ---
try:
    from ..services.autofind import suggest_mapping, find_docid_rev_matches  # type: ignore
except Exception:
    try:
        from services.autofind import suggest_mapping, find_docid_rev_matches  # type: ignore
    except Exception:
        # Fallback stubs to avoid crashes in design-time
        def suggest_mapping(doc_ids, roots):
            return {}
        def find_docid_rev_matches(pairs, roots, extensions=None):
            return {}

# --- Transmittal service ---
try:
    from ..services.transmittal_service import create_transmittal, edit_transmittal_replace_items  # type: ignore
except Exception:
    try:
        from services.transmittal_service import create_transmittal, edit_transmittal_replace_items  # type: ignore
    except Exception:
        def create_transmittal(**kwargs):
            raise RuntimeError("transmittal_service not available")
        def edit_transmittal_replace_items(**kwargs):
            raise RuntimeError("transmittal_service not available")


try:
    from .checkprint_service import start_checkprint_batch
except Exception:
    from ..services.checkprint_service import start_checkprint_batch


# --- Toast helper import (robust) ---
try:
    from .widgets.toast import toast  # type: ignore
except Exception:
    try:
        from ui.widgets.toast import toast  # type: ignore
    except Exception:
        try:
            from widgets.toast import toast  # type: ignore
        except Exception:
            def toast(parent, message: str, msec: int = 1200):
                # Minimal fallback: ignore silently
                pass


# ===================== Small helper widgets =====================
class DragDocListWidget(QListWidget):
    """Accepts file drops; emits (row, local_path) when a file is dropped onto a row."""
    mappingRequested = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)  # we only receive drops here
        self.setDefaultDropAction(Qt.CopyAction)
        self._hover_row = -1

    def dragEnterEvent(self, e: QDragEnterEvent):
        md = e.mimeData()
        if md.hasUrls() or md.hasText():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e: QDragMoveEvent):
        md = e.mimeData()
        if md.hasUrls() or md.hasText():
            row = self.indexAt(e.pos()).row()
            if row != self._hover_row:
                self._hover_row = row
                if row >= 0:
                    self.setCurrentRow(row)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._hover_row = -1
        self.clearSelection()
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QDropEvent):
        path = None
        md = e.mimeData()
        if md.hasUrls():
            urls = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if urls:
                path = urls[0]
        elif md.hasText():
            t = md.text().strip()
            if t.startswith("file://"):
                path = QUrl(t).toLocalFile()
            else:
                path = t  # last resort

        if not path:
            e.ignore()
            return

        row = self.indexAt(e.pos()).row()
        if row < 0:
            row = self.currentRow()
        if row < 0:
            e.ignore()
            return

        self.mappingRequested.emit(row, path)
        e.acceptProposedAction()


class _MapHighlightDelegate(QStyledItemDelegate):
    """Tint rules for the left file tree: duplicates amber, mapped green."""
    def __init__(self, files_tab: 'FilesTab', parent=None):
        super().__init__(parent)
        self._tab = files_tab
        self._green = QColor(46, 160, 67)
        self._amber = QColor(210, 130, 10)

    def paint(self, painter, option, index):
        try:
            model = index.model()
            if not model.isDir(index):
                fp = model.filePath(index)
                if fp:
                    # If this exact path is manually mapped, show manual green (overrides duplicate amber)
                    if self._tab._is_manual_mapped_path(fp):
                        opt = QStyleOptionViewItem(option)
                        opt.palette.setColor(QPalette.Text, getattr(self._tab, "_green_manual", QColor(38, 185, 110)))
                        return super().paint(painter, opt, index)

                    # Duplicates (only if not manually chosen)
                    if self._tab._is_duplicate_basename(fp):
                        opt = QStyleOptionViewItem(option)
                        opt.palette.setColor(QPalette.Text, self._amber)
                        return super().paint(painter, opt, index)

                    # Auto/normal mapped
                    if fp in self._tab._used_paths_set():
                        opt = QStyleOptionViewItem(option)
                        opt.palette.setColor(QPalette.Text, getattr(self._tab, "_green_auto", QColor(46, 160, 67)))
                        return super().paint(painter, opt, index)
        except Exception:
            pass
        return super().paint(painter, option, index)


# ===================== Main Tab =====================
class FilesTab(QWidget):
    backRequested = pyqtSignal()
    proceedCompleted = pyqtSignal(str)  # emits transmittal directory path on success
    remapCompleted = pyqtSignal(str, str)  # (transmittal_number, dir_path) after edit/remap
    checkprintStarted = pyqtSignal(str, str)  # (cp_code, cp_dir)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Core state
        self.db_path: Optional[Path] = None
        self.root_dir: Optional[Path] = None
        self.items: List[dict] = []
        self.doc_ids: List[str] = []
        self.mapping: Dict[str, str] = {}  # {doc_id -> absolute path}
        self.user = self.title = self.client = ""

        # Edit/remap state
        self._edit_mode: bool = False
        # NEW: track duplicates specific to current submission set
        self._dup_for_selection: Dict[str, List[str]] = {}
        self._edit_transmittal_number: Optional[str] = None

        # Duplicate tracking state (names and full paths that belong to *selected* DocID+Rev duplicates)
        self._dup_names: set[str] = set()
        self._dup_paths: set[str] = set()
        # --- NEW: track manual mappings so we can tint them differently ---
        self._manual_mapped_docids: set[str] = set()
        # --- NEW: greens ---
        self._green_auto = QColor(46, 160, 67)  # existing
        self._green_manual = QColor(38, 185, 110)  # slightly different shade

        # ===== UI =====
        root = QVBoxLayout(self)

        # Middle area: splitter L/M/R
        splitter = QSplitter(Qt.Horizontal, self)

        # LEFT: File tree
        left_box = QGroupBox("File tree", self)
        left_v = QVBoxLayout(left_box)

        self.model = QFileSystemModel(self)
        self.model.setRootPath("")
        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setDefaultDropAction(Qt.CopyAction)
        self.tree.setItemDelegate(_MapHighlightDelegate(self, self.tree))

        # Root picker + duplicate banner
        self.btn_choose_root = QPushButton("Choose Root Folder…", self)
        self.btn_choose_root.clicked.connect(self._choose_root)
        self.lbl_dups = QLabel("", self)
        self.lbl_dups.setStyleSheet("color: rgb(210,130,10); font-weight: 600;")
        self.lbl_dups.setVisible(False)

        left_v.addWidget(self.btn_choose_root)
        left_v.addWidget(self.lbl_dups)
        left_v.addWidget(self.tree, 1)

        # How-to (replaces old Map Selected button)
        self.lbl_howto = QLabel(
            "🛈 Drag a file from the tree onto a document row to map it.\n"
            "Duplicates are flagged amber and excluded from auto-match; manual mapping is allowed.",
            self,
        )
        self.lbl_howto.setWordWrap(True)
        self.lbl_howto.setStyleSheet("color:#556; padding-top:4px;")
        left_v.addWidget(self.lbl_howto)

        splitter.addWidget(left_box)

        # MIDDLE: Doc IDs (drop target)
        mid_box = QGroupBox("Files for transmittal", self)
        mid_v = QVBoxLayout(mid_box)
        self.list_docs = DragDocListWidget(self)
        self.list_docs.mappingRequested.connect(self._on_drop_map_to_doc)
        mid_v.addWidget(self.list_docs, 1)
        splitter.addWidget(mid_box)

        # RIGHT: Mapped files
        right_box = QGroupBox("Mapped files", self)
        right_v = QVBoxLayout(right_box)

        # Rematch actions
        actions_row = QHBoxLayout()
        self.btn_auto_exact = QPushButton("Exact Match (DocID_Rev)", self)
        self.btn_auto_exact.setToolTip("Re-run exact matching across all docs (duplicates skipped).")
        self.btn_auto_exact.clicked.connect(self._auto_find_exact)
        actions_row.addWidget(self.btn_auto_exact)

        self.btn_auto_fuzzy = QPushButton("Fuzzy Suggest", self)
        self.btn_auto_fuzzy.setToolTip("Suggest best matches for each doc (duplicates skipped).")
        self.btn_auto_fuzzy.clicked.connect(self._auto_find_fuzzy)
        actions_row.addWidget(self.btn_auto_fuzzy)

        self.btn_clear = QPushButton("Clear All", self)
        self.btn_clear.clicked.connect(self._clear_all)
        actions_row.addWidget(self.btn_clear)

        right_v.addLayout(actions_row)

        self.list_map = QListWidget(self)
        right_v.addWidget(self.list_map, 1)

        splitter.addWidget(right_box)

        root.addWidget(splitter, 1)

        # === Bottom navigation bar (moved from top) ===
        bottom_nav = QHBoxLayout()
        bottom_nav.setSpacing(12)
        bottom_nav.setContentsMargins(4, 4, 4, 4)

        # Back button
        self.btn_back = QPushButton("◀ Back", self)
        self.btn_back.setFixedHeight(32)
        bottom_nav.addWidget(self.btn_back)
        self.btn_back.clicked.connect(lambda: self.backRequested.emit())

        bottom_nav.addStretch(1)

        # Date label + field
        bottom_nav.addWidget(QLabel("Submission date:", self))

        self.le_date = QLineEdit(self)
        self.le_date.setPlaceholderText("DD/MM/YYYY or DD/MM/YYYY HH:MM")
        self.le_date.setFixedWidth(200)
        self.le_date.setMinimumWidth(180)
        self.le_date.setFixedHeight(32)
        bottom_nav.addWidget(self.le_date)

        # Buttons
        self.btn_build_trans = QPushButton("Build Transmittal", self)
        self.btn_submit_cp = QPushButton("Submit for CheckPrint", self)

        self.btn_build_trans.setFixedHeight(32)
        self.btn_submit_cp.setFixedHeight(32)

        # Confirmation wrapper
        def _confirm_build_transmittal():
            r = QMessageBox.question(
                self,
                "Build Transmittal?",
                "This will move all mapped files and create a transmittal.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r == QMessageBox.Yes:
                self._proceed_build_transmittal()

        self.btn_build_trans.clicked.connect(_confirm_build_transmittal)
        self.btn_submit_cp.clicked.connect(self._proceed_submit_checkprint)

        bottom_nav.addWidget(self.btn_build_trans)
        bottom_nav.addWidget(self.btn_submit_cp)

        # Add bottom nav to root
        root.addLayout(bottom_nav)

        # Reset tree to its root
        try:
            self.tree.setRootIndex(self.model.index(self.model.rootPath()))
        except Exception:
            pass
        self.tree.viewport().update()

    # ===== Public API =====
    def set_flow_context(self, *, db_path: Path, items: List[dict],
                         file_mapping: Dict[str, str], user: str, title: str, client: str,
                         created_on: str = ""):
        self.db_path = Path(db_path) if db_path else None
        self.items = items or []
        self.doc_ids = [
            (it.get("doc_id") if isinstance(it, dict) else getattr(it, "doc_id", "")) or ""
            for it in (self.items or [])
        ]
        self.mapping = {}
        for d in self.doc_ids:
            p = (file_mapping or {}).get(d)
            if p:
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
        if not self._edit_transmittal_number:
            raise ValueError("EDIT flow requires 'transmittal_number'")
        self.set_flow_context(
            db_path=payload.get("db_path"),
            items=payload.get("items") or [],
            file_mapping=payload.get("file_mapping") or {},
            user=payload.get("user") or "",
            title=payload.get("title") or "",
            client=payload.get("client") or "",
            created_on=payload.get("created_on") or "",
        )

    def get_mapping(self) -> Dict[str, str]:
        return dict(self.mapping)

    def reset(self):
        self.db_path = None
        self.items = []
        self.doc_ids = []
        self.mapping.clear()
        self._manual_mapped_docids.clear()
        self.root_dir = None
        self._dup_for_selection.clear()
        self._dup_names.clear(); self._dup_paths.clear()
        self._edit_mode = False
        self._edit_transmittal_number = None
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

    def _norm_rev(self, raw: str) -> str:
        """NEW: normalize 'Rev A' / ' a ' -> 'A'."""
        r = (raw or "").strip().upper()
        if r.startswith("REV"):
            r = r[3:].strip()
        return r

    def _used_paths_set(self) -> set:
        return {self._normpath(v) for v in self.mapping.values() if v}

    # --- NEW: helpers for manual tinting ---
    def _manual_paths_set(self) -> set:
        out = set()
        try:
            for d in self._manual_mapped_docids:
                p = self.mapping.get(d)
                if p:
                    out.add(self._normpath(p))
        except Exception:
            pass
        return out

    def _is_manual_mapped_path(self, p: str) -> bool:
        try:
            return self._normpath(p) in self._manual_paths_set()
        except Exception:
            return False

    def _find_doc_for_path(self, p: str) -> Optional[str]:
        np = self._normpath(p)
        for d, v in self.mapping.items():
            if self._normpath(v) == np:
                return d
        return None

    def _doc_rev_pairs(self) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        for it in self.items:
            did = (it.get("doc_id") or "").strip()
            rv = (it.get("revision") or it.get("latest_rev_token") or it.get("latest_rev") or it.get("rev") or "").strip()
            rv = self._norm_rev(rv)
            if did and rv:
                pairs.append((did, rv))
        return pairs

    def _display_path(self, p: Optional[str]) -> str:
        if not p:
            return "—"
        try:
            P = Path(p)
            if self.root_dir:
                return str(P.resolve().relative_to(self.root_dir.resolve()))
            return str(P.name)
        except Exception:
            return str(p)

    def _current_doc_id(self) -> Optional[str]:
        row = self.list_docs.currentRow()
        if 0 <= row < len(self.doc_ids):
            return self.doc_ids[row]
        return None

    # ===== Refresh & coloring =====
    def _refresh_doc_list(self):
        self.list_docs.clear()
        # Build {doc_id -> revision} for nicer labels
        rev_lookup: Dict[str, str] = {}
        for it in (self.items or []):
            did = (it.get("doc_id") if isinstance(it, dict) else getattr(it, "doc_id", "")) or ""
            rev = ""
            if isinstance(it, dict):
                rev = (it.get("revision") or it.get("latest_rev_token") or it.get("latest_rev") or it.get("rev") or "").strip()
            else:
                rev = (getattr(it, "revision", "") or getattr(it, "latest_rev_token", "") or getattr(it, "latest_rev", "") or getattr(it, "rev", "") or "").strip()
            if did:
                rev_lookup[did] = rev
        for d in self.doc_ids:
            rv = rev_lookup.get(d, "")
            label = f"{d}  —  Rev {self._norm_rev(rv)}" if rv else d
            self.list_docs.addItem(QListWidgetItem(label))
        self._apply_colors()

    def _refresh_map_list(self):
        self.list_map.clear()
        for d in self.doc_ids:
            p = self.mapping.get(d)
            it = QListWidgetItem(self._display_path(p))
            if p:
                if self._is_manual_mapped_path(p):
                    it.setForeground(QBrush(getattr(self, "_green_manual", QColor(38, 185, 110))))
                elif self._is_duplicate_basename(p):
                    it.setForeground(QBrush(QColor(210, 130, 10)))  # amber
                else:
                    it.setForeground(QBrush(getattr(self, "_green_auto", QColor(46, 160, 67))))
            else:
                it.setForeground(QBrush(QColor(200, 60, 60)))  # red
            self.list_map.addItem(it)
        # Keep doc list in sync
        self._apply_colors()

    def _apply_colors(self):
        green_auto = QBrush(getattr(self, "_green_auto", QColor(46, 160, 67)))
        green_manual = QBrush(getattr(self, "_green_manual", QColor(38, 185, 110)))
        red = QBrush(QColor(200, 60, 60))
        amber = QBrush(QColor(210, 130, 10))

        for i, d in enumerate(self.doc_ids):
            mapped_path = self.mapping.get(d)

            # Middle list (Files for transmittal)
            it_mid = self.list_docs.item(i)
            if it_mid:
                if mapped_path:
                    if self._is_manual_mapped_path(mapped_path):
                        it_mid.setForeground(green_manual)  # manual overrides duplicate
                    elif self._is_duplicate_basename(mapped_path):
                        it_mid.setForeground(amber)
                    else:
                        it_mid.setForeground(green_auto)
                else:
                    it_mid.setForeground(red)

            # Right list (Mapped files)
            it_right = self.list_map.item(i)
            if it_right:
                if mapped_path:
                    if self._is_manual_mapped_path(mapped_path):
                        it_right.setForeground(green_manual)  # manual overrides duplicate
                    elif self._is_duplicate_basename(mapped_path):
                        it_right.setForeground(amber)
                    else:
                        it_right.setForeground(green_auto)
                else:
                    it_right.setForeground(red)

        self.tree.viewport().update()

    # ===== Duplicate detection =====
    # --- OLD (commented): global scan across *all* filenames under root ---
    # def _scan_duplicates(self):
    #     """Build sets of duplicate basenames and their full paths under current root_dir."""
    #     self._dup_names = set()
    #     self._dup_paths = set()
    #     if not self.root_dir:
    #         self._update_dup_banner(0)
    #         self.tree.viewport().update()
    #         return
    #     counts: Dict[str, List[str]] = {}
    #     try:
    #         for p in self.root_dir.rglob("*"):
    #             try:
    #                 if p.is_file():
    #                     name = p.name.lower()
    #                     counts.setdefault(name, []).append(self._normpath(str(p)))
    #             except Exception:
    #                 continue
    #     except Exception:
    #         counts = {}
    #     self._dup_names = {n for n, lst in counts.items() if len(lst) > 1}
    #     self._dup_paths = {pp for n, lst in counts.items() if len(lst) > 1 for pp in lst}
    #     total = len(self._dup_names)
    #     self._update_dup_banner(total)
    #     if total:
    #         examples = sorted(list(self._dup_names))[:10]
    #         msg = [f"Detected {total} duplicate filename(s) under:\n{self.root_dir}\n",
    #                "These files are excluded from auto-matching.\nYou can still map them manually.\n"]
    #         if examples:
    #             msg.append("\nExamples:\n" + "\n".join(f"• {e}" for e in examples))
    #             rem = total - len(examples)
    #             if rem > 0:
    #                 msg.append(f"\n… and {rem} more.")
    #         QMessageBox.warning(self, "Duplicate files detected", "".join(msg))
    #     self._refresh_map_list()
    #     self.tree.viewport().update()

    # --- NEW: only flag duplicates for the *selected* DocID+Rev pairs ---
    def _scan_duplicates(self):
        """
        Build duplicate sets **only** for items in the current submission.
        We look for files named like:
            <DocID>_<REV>.*, <DocID>-<REV>.*, <DocID> <REV>.*
        (case-insensitive)
        """
        import re
        self._dup_for_selection.clear()
        self._dup_names = set()
        self._dup_paths = set()

        if not self.root_dir:
            self._update_dup_banner(0)
            self.tree.viewport().update()
            return

        pairs = self._doc_rev_pairs()
        if not pairs:
            self._update_dup_banner(0)
            self.tree.viewport().update()
            return

        # Compile per-DocID target regexes
        targets: Dict[str, List[re.Pattern]] = {}
        for did, rv in pairs:
            if not did or not rv:
                continue
            bases = {f"{did}_{rv}", f"{did}-{rv}", f"{did} {rv}"}
            targets[did] = [re.compile(rf"^{re.escape(b)}\.[A-Za-z0-9]+$", re.IGNORECASE) for b in bases]

        # Walk once, bucket matches against targets
        hits: Dict[str, List[str]] = {did: [] for did, _ in pairs}
        try:
            for p in self.root_dir.rglob("*"):
                if not p.is_file():
                    continue
                name = p.name
                np = self._normpath(str(p))
                for did, regs in targets.items():
                    if any(rx.match(name) for rx in regs):
                        hits.setdefault(did, []).append(np)
        except Exception:
            pass

        # Keep only true duplicates (more than one match for that DocID+Rev)
        self._dup_for_selection = {did: paths for did, paths in hits.items() if len(paths) > 1}
        for paths in self._dup_for_selection.values():
            for np in paths:
                self._dup_paths.add(np)
                try:
                    self._dup_names.add(Path(np).name.lower())
                except Exception:
                    pass

        # Banner shows number of DocIDs with duplicates
        self._update_dup_banner(len(self._dup_for_selection))
        self._refresh_map_list()
        self.tree.viewport().update()

    def _is_duplicate_basename(self, p: str) -> bool:
        """True only if this file is part of a duplicate set for one of the selected DocIDs."""
        try:
            name = Path(p).name.lower()
            return name in getattr(self, "_dup_names", set())
        except Exception:
            return False

    def _update_dup_banner(self, total: int):
        try:
            if hasattr(self, "lbl_dups"):
                if total > 0:
                    self.lbl_dups.setText(f"⚠ Duplicates (selected docs): {total}")
                    self.lbl_dups.setVisible(True)
                else:
                    self.lbl_dups.setVisible(False)
        except Exception:
            pass

    # NEW: allow callers to prime the file tree's root without popping a dialog
    def set_root_folder(self, folder: str | Path):
        if not folder:
            return
        try:
            self.root_dir = Path(folder)
            self.tree.setRootIndex(self.model.index(str(self.root_dir)))
        except Exception:
            pass
        self._scan_duplicates()     # scan + banner + repaint
        self._refresh_map_list()    # relative path display against new root

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
        self._scan_duplicates()
        self._refresh_map_list()

    def _on_drop_map_to_doc(self, row: int, file_path: str):
        try:
            p = Path(file_path)
            if not (p.exists() and p.is_file()):
                QMessageBox.warning(self, "Invalid file", f"'{file_path}' is not a file.")
                return
            np = self._normpath(str(p))
            doc_id = self.doc_ids[row]
            # Duplicate basename warning (manual mapping allowed)
            if self._is_duplicate_basename(np):
                r = QMessageBox.warning(
                    self, "Duplicate filename",
                    "This filename appears multiple times under the root for the current submission.\n\n"
                    "• It will be flagged amber.\n"
                    "• It is excluded from auto-matching rules.\n\n"
                    "Proceed with manual mapping?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if r != QMessageBox.Yes:
                    return
            # Conflict check
            current_owner = self._find_doc_for_path(np)
            if current_owner and current_owner != doc_id:
                r = QMessageBox.question(
                    self, "Reassign mapping?",
                    f"'{p.name}' is already mapped to {current_owner}.\n"
                    f"Reassign to {doc_id}?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if r != QMessageBox.Yes:
                    return
                self.mapping.pop(current_owner, None)
                self._refresh_map_list()
                try: toast(self, f"Reassigned to {doc_id}")
                except Exception: pass
            # Assign
            self.mapping[doc_id] = np
            # mark this doc as manual; if we stole it from someone else, clear theirs
            self._manual_mapped_docids.discard(self._find_doc_for_path(np) or "")  # just in case
            self._manual_mapped_docids.add(doc_id)
            self._refresh_map_list()
            try: self._apply_colors()
            except Exception: pass
            # SUCCESS TOAST
            try:
                if self._is_duplicate_basename(np):
                    toast(self, f"Assigned (duplicate): {p.name}")
                else:
                    toast(self, f"Assigned to {doc_id}")
            except Exception:
                pass
        except Exception as ex:
            QMessageBox.critical(self, "Drop failed", f"{type(ex).__name__}: {ex}")

    def _clear_all(self):
        self.mapping.clear()
        self._manual_mapped_docids.clear()
        self._refresh_map_list()

    # NEW: helper to find <DocID>_latestRev.* under root (case-insensitive)
    def _find_latestrev_file(self, doc_id: str) -> Optional[Path]:
        if not self.root_dir or not doc_id:
            return None
        base_lower = f"{doc_id}_latestrev"
        try:
            for p in self.root_dir.rglob("*"):
                if p.is_file() and p.suffix and p.stem.lower() == base_lower.lower():
                    return p
        except Exception:
            pass
        return None

    def _auto_find_exact(self):
        if not self.root_dir:
            QMessageBox.information(self, "Pick a root", "Choose a root folder first.")
            return

        pairs = self._doc_rev_pairs()
        try:
            found = find_docid_rev_matches(pairs, [self.root_dir], extensions=None) or {}
        except Exception:
            found = {}

        # --- NEW: fallback to <DocID>_latestRev.* if no explicit DocID_Rev match was found ---
        for d in self.doc_ids:
            if not found.get(d):
                alt = self._find_latestrev_file(d)
                if alt:
                    found[d] = alt

        assigned = 0
        skipped_conflict = 0
        skipped_dups = 0
        used = self._used_paths_set()
        for d in self.doc_ids:
            p = found.get(d)
            if not p:
                continue
            if self._is_duplicate_basename(str(p)):
                skipped_dups += 1
                continue
            np = self._normpath(str(p))
            current_owner = self._find_doc_for_path(np)
            if current_owner and current_owner != d:
                skipped_conflict += 1
                continue
            prev = self.mapping.get(d)
            if prev:
                used.discard(self._normpath(prev))
            if np not in used or current_owner == d:
                self.mapping[d] = np
                self._manual_mapped_docids.discard(d)  # auto → not manual
                used.add(np)
                assigned += 1
            else:
                skipped_conflict += 1
        self._refresh_map_list()
        try:
            toast(self, f"Exact: {assigned} assigned, {skipped_conflict} conflicts, {skipped_dups} duplicates")
        except Exception:
            pass
        if skipped_conflict or skipped_dups:
            QMessageBox.information(
                self, "Exact Match",
                f"Assigned: {assigned}\n"
                f"Skipped (conflicts): {skipped_conflict}\n"
                f"Skipped (duplicates): {skipped_dups}"
            )

    def _auto_find_fuzzy(self):
        if not self.root_dir:
            QMessageBox.information(self, "Pick a root", "Choose a root folder first.")
            return
        try:
            guessed = suggest_mapping(self.doc_ids, [self.root_dir]) or {}
        except Exception:
            guessed = {}
        assigned = 0
        skipped_conflict = 0
        skipped_dups = 0
        used = self._used_paths_set()
        for d in self.doc_ids:
            lst = guessed.get(d) or []
            if not lst:
                continue
            candidate_path = lst[0][0]
            if self._is_duplicate_basename(str(candidate_path)):
                skipped_dups += 1
                continue
            np = self._normpath(str(candidate_path))
            current_owner = self._find_doc_for_path(np)
            if current_owner and current_owner != d:
                skipped_conflict += 1
                continue
            prev = self.mapping.get(d)
            if prev:
                used.discard(self._normpath(prev))
            if np not in used or current_owner == d:
                self.mapping[d] = np
                used.add(np)
                assigned += 1
            else:
                skipped_conflict += 1
        self._refresh_map_list()
        try:
            toast(self, f"Fuzzy: {assigned} assigned, {skipped_conflict} conflicts, {skipped_dups} duplicates")
        except Exception:
            pass
        if skipped_conflict or skipped_dups:
            QMessageBox.information(
                self, "Fuzzy Auto-Find",
                f"Assigned: {assigned}\n"
                f"Skipped (conflicts): {skipped_conflict}\n"
                f"Skipped (duplicates): {skipped_dups}"
            )

    # ===== Snapshot & Proceed =====
    def _build_snapshot_items(self) -> List[dict]:
        snap: List[dict] = []
        for it in (self.items or []):
            did = (it.get("doc_id") if isinstance(it, dict) else getattr(it, "doc_id", "")) or ""
            if not did:
                continue
            p = self.mapping.get(did)
            snap.append({
                "doc_id": did,
                "description": (it.get("description") if isinstance(it, dict) else getattr(it, "description", "")) or "",
                "type": (it.get("type") if isinstance(it, dict) else getattr(it, "type", "")) or "",
                "file_type": (it.get("file_type") if isinstance(it, dict) else getattr(it, "file_type", "")) or "",
                "revision": (it.get("revision") if isinstance(it, dict) else getattr(it, "revision", "")) or "",
                "file_path": p or "",   # <— key expected by the service
            })
        return snap

    def _proceed_build_transmittal(self):
        if not self.db_path:
            QMessageBox.warning(self, "Missing DB", "No database path available.")
            return
        # build snapshot with warnings on unmapped
        snap = self._build_snapshot_items()
        unmapped = [s for s in snap if not s.get("file_path")] \
                   + [s for s in snap
                      if s.get("file_path")
                      and self._is_duplicate_basename(s.get("file_path"))
                      and not self._is_manual_mapped_path(s.get("file_path"))]
        if unmapped:
            names = "\n".join(f"• {s['doc_id']}" for s in unmapped[:8])
            more = max(0, len(unmapped) - 8)
            suffix = f"\n… and {more} more" if more else ""
            r = QMessageBox.question(
                self, "Proceed with issues?",
                "Some items are unmapped or reference duplicate filenames (flagged amber).\n\n"
                f"{names}{suffix}\n\nProceed anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if r != QMessageBox.Yes:
                return
        # normalize mapping for service call
        for s in snap:
            p = s.get("file_path")
            if p:
                s["file_path"] = self._normpath(p)
        # EDIT flow
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
            self.remapCompleted.emit(self._edit_transmittal_number, str(trans_dir))
            return
        # NEW transmittal flow
        try:
            trans_dir = create_transmittal(
                db_path=self.db_path,
                out_root=None,
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
        self.proceedCompleted.emit(str(trans_dir))

    def _proceed_submit_checkprint(self):
        """
        New flow: instead of immediately building a transmittal, send the files to CheckPrint.
        Uses checkprint_service.start_checkprint_batch, which:
          - renames source files to *_CP_N
          - copies them into the CheckPrint folder
          - records the batch + items in the DB.
        """
        from doctransmittal_sub.services.checkprint_service import (
            start_checkprint_batch,
        )

        if not self.db_path:
            QMessageBox.warning(self, "Missing DB", "No database path available.")
            return

        from doctransmittal_sub.services.db import get_active_checkprint_batch

        active = get_active_checkprint_batch(self.db_path)
        if active:
            QMessageBox.warning(
                self,
                "Active CheckPrint exists",
                f"You already have an active CheckPrint:\n\n"
                f"    {active['code']} ({active['status']})\n\n"
                "You must complete or cancel that CheckPrint before starting a new one."
            )
            return

        # Build snapshot of selected register items (same as transmittal path)
        snap = self._build_snapshot_items()

        # Same unmapped/duplicate pre-check logic
        unmapped = [s for s in snap if not s.get("file_path")] \
                   + [s for s in snap
                      if s.get("file_path")
                      and self._is_duplicate_basename(s.get("file_path"))
                      and not self._is_manual_mapped_path(s.get("file_path"))]

        if unmapped:
            names = "\n".join(f"• {s['doc_id']}" for s in unmapped[:8])
            more = max(0, len(unmapped) - 8)
            suffix = f"\n… and {more} more" if more else ""
            r = QMessageBox.question(
                self,
                "Unmapped / duplicate files",
                "Some documents are not mapped to a file or appear to be duplicates:\n\n"
                f"{names}{suffix}\n\nProceed with CheckPrint anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return

        # Normalise file paths
        for s in snap:
            p = s.get("file_path")
            if p:
                s["file_path"] = self._normpath(p)

        # Prevent use while editing existing transmittals
        if self._edit_mode:
            QMessageBox.warning(
                self,
                "CheckPrint not available",
                "CheckPrint is only available when creating a new transmittal, "
                "not when editing an existing one.",
            )
            return

        # Filter out items with no mapped file_path to avoid service trying to rename ''
        mapped_items = [s for s in snap if s.get("file_path")]
        if not mapped_items:
            QMessageBox.warning(
                self,
                "No mapped files",
                "No documents with mapped files are available to send to CheckPrint.",
            )
            return

        # Ask user to confirm
        r = QMessageBox.question(
            self,
            "Proceed to CheckPrint?",
            "This will rename the mapped files for CheckPrint, copy them into a "
            "CheckPrint folder, and start the review workflow.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return

        # Kick off the CheckPrint batch via the service
        try:
            result = start_checkprint_batch(
                self.db_path,
                items=mapped_items,
                user_name=getattr(self, "user", "") or "",
                title=getattr(self, "title", "") or "",
                client=getattr(self, "client", "") or "",
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to start CheckPrint batch:\n{e}",
            )
            return

        cp_code = (result or {}).get("code", "")
        cp_dir = (result or {}).get("dir", "")

        QMessageBox.information(
            self,
            "CheckPrint Started",
            "CheckPrint batch created:\n\n"
            f"Batch Code: {cp_code or '(unknown)'}\n"
            f"Folder:\n{cp_dir}"
        )

        # Refresh CheckPrint tab so the new batch appears immediately
        try:
            mw = self.window()  # MainWindow
            if hasattr(mw, "checkprint_tab"):
                mw.checkprint_tab._reload_batches()
        except Exception as e:
            print("Failed to refresh CheckPrint tab:", e)

        self.checkprintStarted.emit(cp_code, cp_dir)

