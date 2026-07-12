from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget, QInputDialog,
)

from ..core.settings import SettingsManager
from ..services.db import (
    get_checkprint_items,
    list_documents_with_latest,
    get_project,
    mark_checkprint_items_removed,
)
from ..services.checkprint_service import add_documents_to_checkprint, remove_documents_from_checkprint
from ..services.snapshot_helpers import build_snapshot_items
from ..services.autofind import find_docid_rev_candidates
from .file_match_dialogs import (
    resolve_ambiguous_file_matches,
    select_native_exact_matches,
)


class CheckPrintEditDialog(QDialog):
    """
    Minimal, controlled editor for CheckPrint contents.
    No FilesTab reuse. Uses autofind + file picker only.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        db_path: Path,
        batch_id: int,
        user_name: str,
    ):
        super().__init__(parent)

        self.db_path = Path(db_path)
        self.batch_id = int(batch_id)
        self.user_name = user_name
        self.source_dir: Optional[Path] = None

        proj = get_project(self.db_path) or {}
        self.project_id: Optional[int] = proj.get("id")

        self.setWindowTitle("Edit CheckPrint Documents")
        self.resize(1400, 800)
        self.setMinimumSize(1400, 800)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"CheckPrint Batch ID: {self.batch_id}"))

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ---------- LEFT: REGISTER ----------
        left = QWidget()
        lv = QVBoxLayout(left)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Register (available)"))

        self.lbl_source = QLabel("Source folder: <not set>")
        self.lbl_source.setStyleSheet("color: #666;")
        hdr.addWidget(self.lbl_source, 1)

        btn_pick = QPushButton("Change…")
        btn_pick.clicked.connect(self._pick_source_folder)
        hdr.addWidget(btn_pick)

        lv.addLayout(hdr)

        self.tbl_register = QTableWidget(0, 4)
        self.tbl_register.setHorizontalHeaderLabels(
            ["Doc ID", "Revision", "Type", "Description"]
        )
        self.tbl_register.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_register.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tbl_register.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lv.addWidget(self.tbl_register, 1)

        self.btn_add = QPushButton("Add →")
        self.btn_add.clicked.connect(self._add_selected)
        lv.addWidget(self.btn_add)

        splitter.addWidget(left)

        # ---------- RIGHT: CHECKPRINT ----------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("CheckPrint Items"))

        self.tbl_cp = QTableWidget(0, 4)
        self.tbl_cp.setHorizontalHeaderLabels(
            ["Doc ID", "Revision", "CP Ver", "Status"]
        )

        self.tbl_cp.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_cp.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tbl_cp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rv.addWidget(self.tbl_cp, 1)

        self.btn_remove = QPushButton("← Remove")
        self.btn_remove.clicked.connect(self._remove_selected)
        rv.addWidget(self.btn_remove)

        splitter.addWidget(right)

        # ---------- BOTTOM ----------
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self._reload_tables()


    # ==========================================================
    # Data loading
    # ==========================================================
    def _reload_tables(self):
        cp_items = get_checkprint_items(self.db_path, self.batch_id)
        cp_doc_ids = {it["doc_id"] for it in cp_items}

        # Right table
        self.tbl_cp.setRowCount(0)
        for it in cp_items:
            r = self.tbl_cp.rowCount()
            self.tbl_cp.insertRow(r)
            self.tbl_cp.setItem(r, 0, QTableWidgetItem(it["doc_id"]))
            self.tbl_cp.setItem(r, 1, QTableWidgetItem(it.get("revision", "")))
            self.tbl_cp.setItem(r, 2, QTableWidgetItem(str(it.get("cp_version", ""))))
            self.tbl_cp.setItem(r, 3, QTableWidgetItem(it.get("status", "")))

        # Left table
        self.tbl_register.setRowCount(0)
        if not self.project_id:
            return

        reg = list_documents_with_latest(
            self.db_path,
            self.project_id,
            state="active",
        ) or []

        for d in reg:
            if d.get("doc_id") in cp_doc_ids:
                continue
            r = self.tbl_register.rowCount()
            self.tbl_register.insertRow(r)
            self.tbl_register.setItem(r, 0, QTableWidgetItem(d.get("doc_id", "")))
            self.tbl_register.setItem(r, 1, QTableWidgetItem(d.get("latest_rev", "")))
            self.tbl_register.setItem(r, 2, QTableWidgetItem(d.get("doc_type", "")))
            self.tbl_register.setItem(r, 3, QTableWidgetItem(d.get("description", "")))

        self.tbl_register.resizeColumnsToContents()
        self.tbl_cp.resizeColumnsToContents()

    # ==========================================================
    # Actions
    # ==========================================================

    def _pick_source_folder(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select source folder for autofind",
            str(self.source_dir) if self.source_dir else "",
        )
        if not path:
            return

        self.source_dir = Path(path).resolve()
        self.lbl_source.setText(f"Source folder: {self.source_dir}")

    def _add_selected(self):
        rows = self.tbl_register.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Select", "Select register rows to add.")
            return

        if not self.source_dir or not self.source_dir.exists():
            QMessageBox.warning(
                self,
                "Source folder required",
                "Please select a source folder before adding documents.\n\n"
                "This folder will be scanned to locate files automatically.",
            )
            return

        items: List[dict] = []
        for idx in rows:
            r = idx.row()
            items.append({
                "doc_id": self.tbl_register.item(r, 0).text(),
                "revision": self.tbl_register.item(r, 1).text(),
                "type": self.tbl_register.item(r, 2).text(),
                "description": self.tbl_register.item(r, 3).text(),
            })

        doc_ids = [it["doc_id"] for it in items]

        # ----------------------------------------------------------
        # Autofind (recursive, duplicate-aware)
        # ----------------------------------------------------------
        # Use exact DocID_Revision matching. This avoids silently selecting an
        # arbitrary native/backup file when, for example, DOC_A.pdf, DOC_A.dwg
        # and DOC_A.bak exist in the same source folder.
        pairs = [(it["doc_id"], it.get("revision", "")) for it in items]
        revisions = {it["doc_id"]: it.get("revision", "") for it in items}

        try:
            candidates = find_docid_rev_candidates(pairs, [self.source_dir], extensions=None) or {}
        except Exception:
            candidates = {}

        mapping: Dict[str, str] = {}
        ambiguous: Dict[str, List[Path]] = {}
        native_items: List[dict] = []
        missing: List[str] = []

        for it in items:
            did = it["doc_id"]
            rev = it.get("revision", "")
            paths = sorted({Path(p).resolve() for p in (candidates.get(did) or [])}, key=lambda p: (p.suffix.lower(), p.name.lower(), str(p.parent).lower()))

            if not paths:
                missing.append(did)
                continue

            if len(paths) > 1:
                ambiguous[did] = paths
                continue

            pth = paths[0]
            if pth.suffix.lower() == ".pdf":
                mapping[did] = str(pth)
            else:
                native_items.append({"doc_id": did, "revision": rev, "path": pth})

        # ----------------------------------------------------------
        # Confirm exact single-match native files
        # ----------------------------------------------------------
        selected_native = select_native_exact_matches(
            self,
            native_items,
            source_root=self.source_dir,
            title="Native files found for CheckPrint",
        )
        if selected_native is None:
            return

        for native in native_items:
            did = native.get("doc_id")
            if did in selected_native:
                mapping[did] = str(Path(native.get("path")).resolve())
            else:
                missing.append(did)

        # ----------------------------------------------------------
        # Resolve multiple exact matches
        # ----------------------------------------------------------
        if ambiguous:
            resolved = resolve_ambiguous_file_matches(
                self,
                ambiguous,
                revisions=revisions,
                source_root=self.source_dir,
                title="Resolve CheckPrint file matches",
            )
            if resolved is None:
                return
            for did, selected in resolved.items():
                if selected:
                    mapping[did] = str(Path(selected).resolve())
                else:
                    missing.append(did)

        # De-duplicate missing list after native/ambiguous choices.
        missing = [did for did in dict.fromkeys(missing) if did not in mapping]

        # ----------------------------------------------------------
        # Prompt for missing/manual files
        # ----------------------------------------------------------
        for did in missing:
            path, _ = QFileDialog.getOpenFileName(
                self,
                f"Select file for {did}",
                str(self.source_dir),
                "All Files (*.*);;PDF Files (*.pdf);;DWG Files (*.dwg);;Excel Files (*.xlsx *.xls)",
            )
            if not path:
                QMessageBox.warning(self, "Cancelled", f"No file selected for {did}")
                return
            mapping[did] = str(Path(path).resolve())

        # ----------------------------------------------------------
        # Warn (but allow) if files are outside existing CP source dir
        # ----------------------------------------------------------
        cp_items = get_checkprint_items(self.db_path, self.batch_id)
        if cp_items:
            proj_root = self.db_path.parent.parent
            first_src = str(cp_items[0].get("source_path") or "").strip()
            expected_dir = (proj_root / first_src).parent if first_src else None

            if expected_dir and expected_dir.exists():
                mismatched = []
                for did, p in mapping.items():
                    pp = Path(p).resolve()
                    if pp.parent.resolve() != expected_dir.resolve():
                        mismatched.append((did, str(pp)))

                if mismatched:
                    txt = "\n".join([f"- {did}: {path}" for did, path in mismatched])
                    ans = QMessageBox.question(
                        self,
                        "Non-standard location",
                        "One or more selected files are not in the same folder as the existing "
                        "CheckPrint source files.\n\n"
                        "This is allowed, registration is per-file, but it is not advised.\n\n"
                        f"Expected folder:\n{expected_dir}\n\n"
                        f"Mismatched files:\n{txt}\n\n"
                        "Continue anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if ans != QMessageBox.Yes:
                        return

        # ----------------------------------------------------------
        # Commit
        # ----------------------------------------------------------
        snapshot = build_snapshot_items(items=items, mapping=mapping)

        res = add_documents_to_checkprint(
            self.db_path,
            self.batch_id,
            items=snapshot,
            actor=self.user_name,
        )

        if not res.get("ok"):
            QMessageBox.critical(self, "Add failed", str(res))
            return

        self._reload_tables()

    def _remove_selected(self):
        rows = self.tbl_cp.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(
                self,
                "Select",
                "Select CheckPrint items to remove.",
            )
            return

        # Extract selected checkprint_items.id values
        try:
            cp_items = get_checkprint_items(self.db_path, self.batch_id)
            row_to_id = {idx: it["id"] for idx, it in enumerate(cp_items)}

            item_ids = [
                row_to_id.get(r.row())
                for r in rows
                if row_to_id.get(r.row()) is not None
            ]

        except Exception:
            QMessageBox.critical(
                self,
                "Error",
                "Failed to determine selected CheckPrint items.",
            )
            return

        # Prompt user for source handling decision
        msg = QMessageBox(self)
        msg.setWindowTitle("Remove from CheckPrint")
        msg.setIcon(QMessageBox.Question)

        msg.setText("Remove selected documents from this CheckPrint?")

        msg.setInformativeText(
            "Choose how the source files should be handled:\n\n"
            "• Keep Latest CheckPrint\n"
            "  The most recent CP version will become the new source file.\n\n"
            "• Revert to Original\n"
            "  The original source file (prior to CheckPrint) will be restored.\n\n"
            "This action affects all selected documents."
        )

        keep_btn = msg.addButton(
            "Keep Latest CP",
            QMessageBox.AcceptRole,
        )
        revert_btn = msg.addButton(
            "Revert to Original",
            QMessageBox.DestructiveRole,
        )
        cancel_btn = msg.addButton(QMessageBox.Cancel)

        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == cancel_btn or clicked is None:
            return

        if clicked == keep_btn:
            mode = "keep_latest"
        elif clicked == revert_btn:
            mode = "revert_original"
        else:
            return

        actor = SettingsManager().get("user.name", "") or ""

        # Call service-layer removal (authoritative)
        try:
            res = remove_documents_from_checkprint(
                self.db_path,
                batch_id=self.batch_id,
                item_ids=item_ids,
                actor=actor,
                mode=mode,
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Remove failed",
                f"An unexpected error occurred:\n{e}",
            )
            return

        if not res.get("ok"):
            QMessageBox.critical(
                self,
                "Remove failed",
                str(res),
            )
            return

        self._reload_tables()
