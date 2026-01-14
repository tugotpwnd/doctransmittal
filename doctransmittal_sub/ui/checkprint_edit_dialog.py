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
    QWidget,
)

from ..services.db import (
    get_checkprint_items,
    list_documents_with_latest,
    get_project,
    mark_checkprint_items_removed,
)
from ..services.checkprint_service import add_documents_to_checkprint
from ..services.snapshot_helpers import build_snapshot_items
from ..services.autofind import suggest_mapping, find_docid_rev_matches


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

        proj = get_project(self.db_path) or {}
        self.project_id: Optional[int] = proj.get("id")

        self.setWindowTitle("Edit CheckPrint Documents")
        self.resize(1100, 650)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"CheckPrint Batch ID: {self.batch_id}"))

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ---------- LEFT: REGISTER ----------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Register (available)"))

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

        self.tbl_cp = QTableWidget(0, 5)
        self.tbl_cp.setHorizontalHeaderLabels(
            ["Item ID", "Doc ID", "Revision", "CP Ver", "Status"]
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
            self.tbl_cp.setItem(r, 0, QTableWidgetItem(str(it["id"])))
            self.tbl_cp.setItem(r, 1, QTableWidgetItem(it["doc_id"]))
            self.tbl_cp.setItem(r, 2, QTableWidgetItem(it.get("revision", "")))
            self.tbl_cp.setItem(r, 3, QTableWidgetItem(str(it.get("cp_version", ""))))
            self.tbl_cp.setItem(r, 4, QTableWidgetItem(it.get("status", "")))

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
    def _add_selected(self):
        rows = self.tbl_register.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Select", "Select register rows to add.")
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

        # --- Autofind ---
        doc_ids = [it["doc_id"] for it in items]
        mapping: Dict[str, str] = {}

        guessed = suggest_mapping(doc_ids, []) or {}
        for did, matches in guessed.items():
            if matches:
                mapping[did] = str(matches[0][0])

        # --- Prompt for missing ---
        for it in items:
            did = it["doc_id"]
            if did in mapping:
                continue
            path, _ = QFileDialog.getOpenFileName(
                self,
                f"Select file for {did}",
                "",
                "All Files (*.*)",
            )
            if not path:
                QMessageBox.warning(self, "Cancelled", f"No file selected for {did}")
                return
            mapping[did] = path

        # ----------------------------------------------------------
        # Warn (but allow) if selected files are outside the common
        # CheckPrint source folder (new-document add only)
        # ----------------------------------------------------------
        cp_items = get_checkprint_items(self.db_path, self.batch_id)
        if cp_items:
            # Project root is two levels above DB (consistent with services)
            proj_root = self.db_path.parent.parent

            # Use the first CP item's source path as the reference folder
            first_src = str(cp_items[0].get("source_path") or "").strip()
            expected_dir = (proj_root / first_src).parent if first_src else None

            if expected_dir and expected_dir.exists():
                mismatched: List[tuple[str, str]] = []

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
                        "This is allowed, but not advised.\n\n"
                        f"Expected folder:\n{expected_dir}\n\n"
                        f"Mismatched files:\n{txt}\n\n"
                        "Continue anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if ans != QMessageBox.Yes:
                        return

        # ----------------------------------------------------------
        # Proceed with snapshot + DB mutation
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
            QMessageBox.information(self, "Select", "Select CheckPrint items to remove.")
            return

        ids = [int(self.tbl_cp.item(r.row(), 0).text()) for r in rows]

        # Placeholder: DB-only removal for now
        mark_checkprint_items_removed(
            self.db_path,
            batch_id=self.batch_id,
            item_ids=ids,
        )

        self._reload_tables()
