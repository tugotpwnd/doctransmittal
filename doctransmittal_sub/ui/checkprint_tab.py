from __future__ import annotations

import os
import shutil
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QMessageBox, QFileDialog, QGroupBox
)
from PyQt5.QtCore import Qt

try:
    from ..services.db import list_checkprint_batches, get_checkprint_items
    from ..services.checkprint_service import resubmit_checkprint_items
except:
    from services.db import list_checkprint_batches, get_checkprint_items
    from services.checkprint_service import resubmit_checkprint_items

from ..core.paths import resolve_company_library_path, company_library_root


class CheckPrintTab(QWidget):
    """
    Early version — handles SUBMITTER workflow only.
    Reviewer UI comes later.

    Workflow:
    1. User selects a batch → CP-TRN-###
    2. User chooses 'Submitter'
    3. UI shows list of items with status (pending/accepted/rejected)
    4. Submitter can resubmit items:
        • rejected → pending, CP incremented
        • accepted → pending, CP incremented
        • pending → pending, CP NOT incremented
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path: Path | None = None
        self.project_id: int | None = None
        self.current_batch_id: int | None = None
        self.setObjectName("CheckPrintTab")

        root = QVBoxLayout(self)

        # --- Batch selector ---
        box_batches = QGroupBox("Available CheckPrint Batches")
        vb = QVBoxLayout(box_batches)
        self.list_batches = QListWidget()
        self.list_batches.itemSelectionChanged.connect(self._on_batch_selected)
        vb.addWidget(self.list_batches)
        root.addWidget(box_batches)

        # --- Mode selector (Submitter vs Reviewer) ---
        mode_box = QGroupBox("Select Role")
        mb = QHBoxLayout(mode_box)
        self.btn_as_submitter = QPushButton("Submitter")
        self.btn_as_submitter.clicked.connect(self._enter_submitter_mode)
        self.btn_as_submitter.setEnabled(False)
        mb.addWidget(self.btn_as_submitter)

        self.btn_as_reviewer = QPushButton("Reviewer (coming soon)")
        self.btn_as_reviewer.setEnabled(False)
        mb.addWidget(self.btn_as_reviewer)

        root.addWidget(mode_box)

        # --- Submitter panel ---
        self.box_submitter = QGroupBox("Submitter View")
        self.box_submitter.setVisible(False)
        sv = QVBoxLayout(self.box_submitter)

        self.list_items = QListWidget()
        sv.addWidget(self.list_items)

        self.btn_resubmit = QPushButton("Resubmit Selected File…")
        self.btn_resubmit.clicked.connect(self._resubmit_selected)
        sv.addWidget(self.btn_resubmit)

        self.btn_cancel = QPushButton("Cancel This CheckPrint")
        self.btn_cancel.clicked.connect(self._cancel_checkprint)
        sv.addWidget(self.btn_cancel)

        root.addWidget(self.box_submitter)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def set_db_path(self, db_path: Path):
        self.db_path = Path(db_path)
        self._reload_batches()

    # -------------------------------------------------------------------------
    # Batch list
    # -------------------------------------------------------------------------
    def _reload_batches(self):
        self.list_batches.clear()
        if not self.db_path:
            return
        rows = list_checkprint_batches(self.db_path)
        for r in rows:
            txt = f"{r['code']} — {r['created_on']} — {r['status']}"
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, r["id"])
            self.list_batches.addItem(it)

    def _on_batch_selected(self):
        item = self.list_batches.currentItem()
        if not item:
            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            return
        self.current_batch_id = item.data(Qt.UserRole)
        self.btn_as_submitter.setEnabled(True)
        # Reviewer mode comes later

    # -------------------------------------------------------------------------
    # Submitter mode
    # -------------------------------------------------------------------------
    def _enter_submitter_mode(self):
        if not self.current_batch_id:
            return

        # Fetch batch metadata
        from ..services.db import get_checkprint_batch
        batch = get_checkprint_batch(self.db_path, self.current_batch_id)

        # Prevent editing cancelled batches
        if batch and batch["status"] == "cancelled":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "This CheckPrint has already been cancelled and cannot be edited."
            )
            self.box_submitter.setVisible(False)
            return

        # Show submitter UI
        self.box_submitter.setVisible(True)
        self._load_items_for_submitter()

        # Only allow Cancel if batch is active
        self.btn_cancel.setEnabled(batch["status"] != "cancelled")

    def _load_items_for_submitter(self):
        self.list_items.clear()
        if not self.db_path or not self.current_batch_id:
            return
        items = get_checkprint_items(self.db_path, self.current_batch_id)
        for it in items:
            disp = f"{it['doc_id']}    [Rev {it['revision']}]    Status: {it['status']}    CP:{it['cp_version']}"
            row = QListWidgetItem(disp)
            row.setData(Qt.UserRole, it)
            # Colour status:
            st = it["status"]
            if st == "rejected":
                row.setForeground(Qt.red)
            elif st == "accepted":
                from PyQt5.QtGui import QColor
                row.setForeground(QColor(38,185,110))
            else:
                from PyQt5.QtGui import QColor
                row.setForeground(QColor(210,130,10))
            self.list_items.addItem(row)

    # -------------------------------------------------------------------------
    # Resubmission logic
    # -------------------------------------------------------------------------
    def _resubmit_selected(self):
        item = self.list_items.currentItem()
        if not item:
            QMessageBox.information(self, "CheckPrint", "Select a document to resubmit.")
            return

        it = item.data(Qt.UserRole)
        doc_id = it["doc_id"]
        status = it["status"]

        # User picks a new file
        fp, _ = QFileDialog.getOpenFileName(
            self,
            f"Select updated file for {doc_id}",
            "",
            "All Files (*.*)"
        )

        if not fp:
            return

        try:
            if status == "pending":
                # overwrite same CP version
                overwrite_checkprint_items(
                    self.db_path,
                    batch_id=self.current_batch_id,
                    item_id_to_new_path={it["id"]: Path(fp)},
                    submitter="submitter",   # swap in your real username if you have it
                )
            else:
                # accepted / rejected → increment CP version
                resubmit_checkprint_items(
                    self.db_path,
                    batch_id=self.current_batch_id,
                    item_id_to_new_path={it["id"]: Path(fp)},
                    submitter="submitter",
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Resubmit failed:\n{e}")
            return

        QMessageBox.information(self, "CheckPrint", f"{doc_id} resubmitted.")
        self._load_items_for_submitter()

    def _cancel_checkprint(self):
        if not self.current_batch_id:
            return

        r = QMessageBox.question(
            self,
            "Cancel CheckPrint?",
            (
                "<html>"
                "Are you sure you want to cancel this CheckPrint?<br><br>"
                "• All items will be marked as cancelled<br>"
                "• Source files will revert to original names<br>"
                "• The CheckPrint folder will be archived as CANCELLED-&lt;name&gt;<br>"
                "• You may start a new CheckPrint immediately afterwards<br><br>"
                "<span style='color:red; font-weight:bold;'>This process cannot be undone.</span>"
                "</html>"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if r != QMessageBox.Yes:
            return

        try:
            from ..services.checkprint_service import cancel_checkprint
            cancel_checkprint(self.db_path, self.current_batch_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to cancel CheckPrint:\n{e}")
            return

        QMessageBox.information(
            self,
            "CheckPrint Cancelled",
            "The CheckPrint has been cancelled and archived."
        )

        self.box_submitter.setVisible(False)
        self._reload_batches()
