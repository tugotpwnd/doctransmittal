from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QMessageBox,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QMenu,
    QInputDialog,
)

from PyQt5.QtGui import QColor

from ..core.settings import SettingsManager

try:
    from ..services.db import (
        list_checkprint_batches,
        get_checkprint_items,
        get_checkprint_batch,
        update_checkprint_item_status,
    )
    from ..services.checkprint_service import (
        resubmit_checkprint_items,
        overwrite_checkprint_items,
        cancel_checkprint,
        finalize_checkprint_to_transmittal,
    )
    from ..core.paths import resolve_company_library_path
except ImportError:
    from services.db import (
        list_checkprint_batches,
        get_checkprint_items,
        get_checkprint_batch,
        update_checkprint_item_status,
    )
    from services.checkprint_service import (
        resubmit_checkprint_items,
        overwrite_checkprint_items,
        cancel_checkprint,
        finalize_checkprint_to_transmittal,
    )
    from core.paths import resolve_company_library_path


class CheckPrintTab(QWidget):
    """
    CheckPrint tab.

    Layout:
      • Batch selector (combo)
      • Role selector: Submitter / Reviewer
      • Submitter view: horizontal panes (Pending, Rejected, Accepted)
      • Reviewer view: horizontal panes (Pending, Rejected, Accepted)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path: Path | None = None
        self.current_batch_id: int | None = None
        self._batch_rows = []

        self.setObjectName("CheckPrintTab")
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)

        # --- Batch selector (combo) ---
        box_batches = QGroupBox("CheckPrint Batch")
        box_batches.setMaximumHeight(100)
        hb = QHBoxLayout(box_batches)
        hb.addWidget(QLabel("Select batch:"))
        self.combo_batches = QComboBox()
        self.combo_batches.currentIndexChanged.connect(self._on_batch_selected)
        hb.addWidget(self.combo_batches, 1)
        root.addWidget(box_batches)

        # --- Mode selector (Submitter vs Reviewer) ---
        mode_box = QGroupBox("Select Role")
        mb = QHBoxLayout(mode_box)
        mode_box.setMaximumHeight(100)

        self.btn_as_submitter = QPushButton("Submitter")
        self.btn_as_submitter.setFixedWidth(150)
        self.btn_as_submitter.setEnabled(self.current_batch_id is not None)
        self.btn_as_submitter.clicked.connect(self._enter_submitter_mode)
        mb.addWidget(self.btn_as_submitter)

        self.btn_as_reviewer = QPushButton("Reviewer")
        self.btn_as_reviewer.setFixedWidth(150)
        self.btn_as_reviewer.setEnabled(self.current_batch_id is not None)
        self.btn_as_reviewer.clicked.connect(self._enter_reviewer_mode)
        mb.addWidget(self.btn_as_reviewer)

        mb.addStretch(1)
        root.addWidget(mode_box)

        # --- Submitter panel ---
        self.box_submitter = QGroupBox("Submitter View")
        self.box_submitter.setVisible(False)
        sv = QVBoxLayout(self.box_submitter)

        sub_h = QHBoxLayout()

        # Pending (submitter)
        grp_pend_sub = QGroupBox("Pending")
        g_pend_layout = QVBoxLayout(grp_pend_sub)
        self.list_pending_sub = QListWidget()
        # WINDOW SIZE ISSUES? ITS PROBABLY THIS LINE
        self.list_pending_sub.setMinimumHeight(500)
        # WINDOW SIZE ISSUES? ITS PROBABLY THIS LINE
        self._wire_list_common(self.list_pending_sub, editable=False, for_reviewer=False)
        g_pend_layout.addWidget(self.list_pending_sub)
        self.btn_resubmit_pending_sub = QPushButton("Resubmit Selected")
        self.btn_resubmit_pending_sub.setFixedWidth(300)
        self.btn_resubmit_pending_sub.clicked.connect(self._resubmit_from_pending)
        g_pend_layout.addWidget(self.btn_resubmit_pending_sub)
        sub_h.addWidget(grp_pend_sub)

        # Rejected (submitter)
        grp_rej_sub = QGroupBox("Rejected")
        g_rej_layout = QVBoxLayout(grp_rej_sub)
        self.list_rejected_sub = QListWidget()
        self._wire_list_common(self.list_rejected_sub, editable=False, for_reviewer=False)
        g_rej_layout.addWidget(self.list_rejected_sub)

        btn_row_rej_sub = QHBoxLayout()
        self.btn_resubmit_rejected_sub = QPushButton("Resubmit Selected")
        self.btn_resubmit_rejected_sub.setFixedWidth(300)
        self.btn_resubmit_rejected_sub.clicked.connect(self._resubmit_from_rejected)
        btn_row_rej_sub.addWidget(self.btn_resubmit_rejected_sub)

        self.btn_view_comment_sub = QPushButton("View Comment")
        self.btn_view_comment_sub.setFixedWidth(300)
        self.btn_view_comment_sub.clicked.connect(self._view_comment_submitter)
        btn_row_rej_sub.addStretch(1)
        btn_row_rej_sub.addWidget(self.btn_view_comment_sub)

        g_rej_layout.addLayout(btn_row_rej_sub)
        sub_h.addWidget(grp_rej_sub)

        # Accepted (submitter)
        grp_acc_sub = QGroupBox("Accepted")
        g_acc_layout = QVBoxLayout(grp_acc_sub)
        self.list_accepted_sub = QListWidget()
        self._wire_list_common(self.list_accepted_sub, editable=False, for_reviewer=False)
        g_acc_layout.addWidget(self.list_accepted_sub)
        sub_h.addWidget(grp_acc_sub)

        sv.addLayout(sub_h)

        # Cancel button (submitter)
        bottom_sub = QHBoxLayout()
        bottom_sub.addStretch(1)
        self.btn_cancel_submitter = QPushButton("Cancel This CheckPrint")
        self.btn_cancel_submitter.setFixedWidth(300)
        self.btn_cancel_submitter.clicked.connect(self._cancel_checkprint)
        bottom_sub.addWidget(self.btn_cancel_submitter)
        sv.addLayout(bottom_sub)

        root.addWidget(self.box_submitter)

        # --- Reviewer panel ---
        self.box_reviewer = QGroupBox("Reviewer View")
        self.box_reviewer.setVisible(False)
        rv = QVBoxLayout(self.box_reviewer)

        rev_h = QHBoxLayout()

        # Pending (reviewer)
        grp_pend_rev = QGroupBox("Pending")
        g_pend_rev_layout = QVBoxLayout(grp_pend_rev)
        self.list_pending_rev = QListWidget()
        self.list_pending_rev.setMinimumHeight(500)
        self._wire_list_common(self.list_pending_rev, editable=True, for_reviewer=True)
        g_pend_rev_layout.addWidget(self.list_pending_rev)

        btn_row_pend_rev = QHBoxLayout()
        self.btn_accept = QPushButton("Accept")
        self.btn_accept.setFixedWidth(150)
        self.btn_accept.clicked.connect(self._reviewer_accept)
        btn_row_pend_rev.addWidget(self.btn_accept)

        self.btn_reject = QPushButton("Reject…")
        self.btn_reject.setFixedWidth(150)
        self.btn_reject.clicked.connect(self._reviewer_reject)
        btn_row_pend_rev.addWidget(self.btn_reject)

        btn_row_pend_rev.addStretch(1)
        g_pend_rev_layout.addLayout(btn_row_pend_rev)

        rev_h.addWidget(grp_pend_rev)

        # Rejected (reviewer)
        grp_rej_rev = QGroupBox("Rejected")
        g_rej_rev_layout = QVBoxLayout(grp_rej_rev)
        self.list_rejected_rev = QListWidget()
        self._wire_list_common(self.list_rejected_rev, editable=True, for_reviewer=True)
        g_rej_rev_layout.addWidget(self.list_rejected_rev)

        btn_row_rej_rev = QHBoxLayout()
        self.btn_open_comment_rev = QPushButton("Open Comment…")
        self.btn_open_comment_rev.setFixedWidth(300)
        self.btn_open_comment_rev.clicked.connect(self._open_comment_reviewer_button)
        btn_row_rej_rev.addWidget(self.btn_open_comment_rev)
        btn_row_rej_rev.addStretch(1)
        g_rej_rev_layout.addLayout(btn_row_rej_rev)

        rev_h.addWidget(grp_rej_rev)

        # Accepted (reviewer)
        grp_acc_rev = QGroupBox("Accepted")
        g_acc_rev_layout = QVBoxLayout(grp_acc_rev)
        self.list_accepted_rev = QListWidget()
        self._wire_list_common(self.list_accepted_rev, editable=True, for_reviewer=True)
        g_acc_rev_layout.addWidget(self.list_accepted_rev)

        self.btn_finalize = QPushButton("Finalize → Transmittal")
        self.btn_finalize.setFixedWidth(300)
        self.btn_finalize.clicked.connect(self._finalize_checkprint)
        g_acc_rev_layout.addWidget(self.btn_finalize)

        rev_h.addWidget(grp_acc_rev)

        rv.addLayout(rev_h)

        # Cancel (reviewer)
        bottom_rev = QHBoxLayout()
        bottom_rev.addStretch(1)
        self.btn_cancel_reviewer = QPushButton("Cancel This CheckPrint")
        self.btn_cancel_reviewer.setFixedWidth(300)
        self.btn_cancel_reviewer.clicked.connect(self._cancel_checkprint)
        bottom_rev.addWidget(self.btn_cancel_reviewer)
        rv.addLayout(bottom_rev)

        root.addWidget(self.box_reviewer)
        root.addStretch(1)

    # ------------------------------------------------------------------ wiring helpers
    def _wire_list_common(self, lw: QListWidget, *, editable: bool, for_reviewer: bool):
        lw.itemDoubleClicked.connect(self._open_cp_item)
        lw.setContextMenuPolicy(Qt.CustomContextMenu)
        lw.customContextMenuRequested.connect(
            lambda pos, w=lw, e=editable, r=for_reviewer: self._show_comment_menu(w, pos, e, r)
        )

    # ------------------------------------------------------------------ Public API
    def set_db_path(self, db_path: Path):
        self.db_path = Path(db_path)
        self._reload_batches()

    # ------------------------------------------------------------------ Batch handling
    def _reload_batches(self):
        self.combo_batches.blockSignals(True)
        self.combo_batches.clear()
        self._batch_rows = []

        if not self.db_path:
            self.combo_batches.blockSignals(False)
            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            return

        rows = list_checkprint_batches(self.db_path)
        self._batch_rows = rows

        for r in rows:
            label = f"{r['code']} — {r['status']} — {r['created_on']}"
            self.combo_batches.addItem(label, r["id"])

        self.combo_batches.blockSignals(False)

        if rows:
            # Assume first row is latest
            self.combo_batches.setCurrentIndex(0)
            self._on_batch_selected(0)

        else:
            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)

    def _on_batch_selected(self, idx: int):
        if idx < 0 or not self._batch_rows:
            self.current_batch_id = None
            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            return

        batch_id = self.combo_batches.itemData(idx)
        self.current_batch_id = int(batch_id) if batch_id is not None else None

        self._update_role_buttons(None)

        # Enable role buttons now that batch is valid
        self.btn_as_submitter.setEnabled(True)
        self.btn_as_reviewer.setEnabled(True)

        # Hide both role views until a role is chosen
        self.box_submitter.setVisible(False)
        self.box_reviewer.setVisible(False)

    # ------------------------------------------------------------------ UX
    def _update_role_buttons(self, role: str):
        """
        role = 'submitter' or 'reviewer'
        Makes the selected role visually obvious.
        """

        if role == "submitter":
            self.btn_as_submitter.setStyleSheet(
                "background-color: #d0d0d0; font-weight: bold;"
            )
            self.btn_as_reviewer.setStyleSheet("")
        elif role == "reviewer":
            self.btn_as_reviewer.setStyleSheet(
                "background-color: #d0d0d0; font-weight: bold;"
            )
            self.btn_as_submitter.setStyleSheet("")
        else:
            # reset both
            self.btn_as_submitter.setStyleSheet("")
            self.btn_as_reviewer.setStyleSheet("")

    # ------------------------------------------------------------------ Submitter mode
    def _enter_submitter_mode(self):
        if not self.current_batch_id or not self.db_path:
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if batch and batch["status"] == "cancelled":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "This CheckPrint has already been cancelled and cannot be edited.",
            )
            return

        self.box_reviewer.setVisible(False)
        self.box_submitter.setVisible(True)
        self._update_role_buttons("submitter")
        self._load_items_for_submitter()

        self.btn_cancel_submitter.setEnabled(batch["status"] != "cancelled")

    def _load_items_for_submitter(self):
        self.list_pending_sub.clear()
        self.list_rejected_sub.clear()
        self.list_accepted_sub.clear()

        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)
        self._populate_three_lists(items,
                                   self.list_pending_sub,
                                   self.list_rejected_sub,
                                   self.list_accepted_sub)

    # ------------------------------------------------------------------ Reviewer mode
    def _enter_reviewer_mode(self):
        if not self.current_batch_id or not self.db_path:
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if batch and batch["status"] == "cancelled":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "This CheckPrint has already been cancelled and cannot be edited.",
            )
            return

        self.box_submitter.setVisible(False)
        self.box_reviewer.setVisible(True)
        self._update_role_buttons("reviewer")
        self._load_items_for_reviewer()

        self.btn_cancel_reviewer.setEnabled(batch["status"] != "cancelled")

    def _load_items_for_reviewer(self):
        self.list_pending_rev.clear()
        self.list_rejected_rev.clear()
        self.list_accepted_rev.clear()

        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)
        self._populate_three_lists(items,
                                   self.list_pending_rev,
                                   self.list_rejected_rev,
                                   self.list_accepted_rev)

    # ------------------------------------------------------------------ Common list population
    def _populate_three_lists(self, items, list_pending: QListWidget,
                              list_rejected: QListWidget,
                              list_accepted: QListWidget):
        for it in items:
            st = (it.get("status") or "").lower()

            disp = f"{it['doc_id']}  [Rev {it['revision']}]  Status: {it['status']}  CP:{it['cp_version']}"
            row = QListWidgetItem(disp)
            row.setData(Qt.UserRole, it)

            if st == "rejected":
                row.setForeground(Qt.red)
                list_rejected.addItem(row)
            elif st == "accepted":
                row.setForeground(QColor(38, 185, 110))
                list_accepted.addItem(row)
            else:
                # pending / anything else
                row.setForeground(QColor(210, 130, 10))
                list_pending.addItem(row)

    # ------------------------------------------------------------------ File opening
    def _open_cp_item(self, item: QListWidgetItem):
        if not item:
            return
        it = item.data(Qt.UserRole) or {}
        rel_cp = it.get("cp_path")
        if not rel_cp:
            QMessageBox.warning(self, "Open File", "No CP file path recorded.")
            return
        abs_cp = Path(resolve_company_library_path(rel_cp))
        if not abs_cp.exists():
            QMessageBox.warning(self, "Open File", f"File not found:\n{abs_cp}")
            return

        try:
            if sys.platform.startswith("darwin"):
                os.system(f"open '{abs_cp}'")
            elif os.name == "nt":
                os.startfile(str(abs_cp))
            else:
                os.system(f"xdg-open '{abs_cp}'")
        except Exception as e:
            QMessageBox.critical(self, "Open File", f"Failed to open file:\n{e}")

    # ------------------------------------------------------------------ Comments: context menu + dialogs
    def _show_comment_menu(self, lw: QListWidget, pos: QPoint,
                           editable: bool, for_reviewer: bool):
        item = lw.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        if editable:
            act = menu.addAction("View/Edit Comment…")
        else:
            act = menu.addAction("View Comment…")

        chosen = menu.exec_(lw.mapToGlobal(pos))
        if chosen == act:
            self._open_comment_dialog(item, editable=editable, for_reviewer=for_reviewer)

    def _open_comment_dialog(self, item: QListWidgetItem,
                             *, editable: bool, for_reviewer: bool):
        it = item.data(Qt.UserRole) or {}
        current = it.get("last_reviewer_note") or ""

        if not editable:
            if not current.strip():
                QMessageBox.information(self, "Comment", "No comment recorded.")
                return
            QMessageBox.information(self, "Comment", current)
            return

        # Editable (reviewer)
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Reviewer Comment",
            "Enter reviewer comment:",
            current,
        )
        if not ok:
            return

        new_text = text.strip()
        # Persist via DB
        update_checkprint_item_status(
            self.db_path,
            item_id=it["id"],
            note=new_text,
        )

        # Refresh lists from DB for whichever view we're in
        if for_reviewer:
            self._load_items_for_reviewer()
        else:
            self._load_items_for_submitter()

    # Submitter button to view comment on rejected
    def _view_comment_submitter(self):
        item = self.list_rejected_sub.currentItem()
        if not item:
            QMessageBox.information(self, "Comment", "Select a rejected document.")
            return
        self._open_comment_dialog(item, editable=False, for_reviewer=False)

    # ------------------------------------------------------------------ Submitter resubmission
    def _resubmit_from_pending(self):
        item = self.list_pending_sub.currentItem()
        if not item:
            QMessageBox.information(self, "CheckPrint", "Select a pending document to resubmit.")
            return
        it = item.data(Qt.UserRole)
        self._resubmit_item(it)

    def _resubmit_from_rejected(self):
        item = self.list_rejected_sub.currentItem()
        if not item:
            QMessageBox.information(self, "CheckPrint", "Select a rejected document to resubmit.")
            return
        it = item.data(Qt.UserRole)
        self._resubmit_item(it)

    def _resubmit_item(self, it: dict):
        doc_id = it["doc_id"]
        status = it["status"]

        fp, _ = QFileDialog.getOpenFileName(
            self,
            f"Select updated file for {doc_id}",
            "",
            "All Files (*.*)",
        )
        if not fp:
            return

        try:
            if status == "pending":
                overwrite_checkprint_items(
                    self.db_path,
                    batch_id=self.current_batch_id,
                    item_id_to_new_path={it["id"]: Path(fp)},
                    submitter="submitter",  # TODO: wire real username
                )
            else:
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
        # Reload both views in case user flips roles
        self._load_items_for_submitter()
        self._load_items_for_reviewer()

    # ------------------------------------------------------------------ Reviewer actions
    def _reviewer_accept(self):
        item = self.list_pending_rev.currentItem()
        if not item:
            QMessageBox.information(self, "Reviewer", "Select a pending document to accept.")
            return
        it = item.data(Qt.UserRole)

        update_checkprint_item_status(
            self.db_path,
            item_id=it["id"],
            status="accepted",
            reviewer="reviewer",  # TODO: real username
        )
        self._load_items_for_reviewer()

    def _reviewer_reject(self):
        item = self.list_pending_rev.currentItem()
        if not item:
            QMessageBox.information(self, "Reviewer", "Select a pending document to reject.")
            return
        it = item.data(Qt.UserRole)

        comment, ok = QInputDialog.getMultiLineText(
            self,
            "Reject Document",
            "Enter rejection reason:",
            it.get("last_reviewer_note") or "",
        )
        if not ok:
            return

        comment = comment.strip()
        if not comment:
            QMessageBox.information(self, "Reviewer", "Please enter a rejection reason.")
            return

        update_checkprint_item_status(
            self.db_path,
            item_id=it["id"],
            status="rejected",
            reviewer="reviewer",
            note=comment,
        )
        self._load_items_for_reviewer()

    def _open_comment_reviewer_button(self):
        # Try current selection from any reviewer list
        lw = None
        for candidate in (self.list_pending_rev, self.list_rejected_rev, self.list_accepted_rev):
            if candidate.currentItem():
                lw = candidate
                break
        if not lw:
            QMessageBox.information(self, "Comment", "Select a document first.")
            return

        self._open_comment_dialog(lw.currentItem(), editable=True, for_reviewer=True)

    def _finalize_checkprint(self):
        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)
        if any((it.get("status") or "").lower() != "accepted" for it in items):
            QMessageBox.warning(
                self,
                "Cannot Finalize",
                "All documents must be accepted before finalizing.",
            )
            return

        r = QMessageBox.question(
            self,
            "Finalize CheckPrint",
            "All documents are accepted.\n\nCreate the transmittal now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return

        try:
            trans_dir = finalize_checkprint_to_transmittal(
                self.db_path, batch_id=self.current_batch_id, reviewer="reviewer"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to finalize:\n{e}")
            return

        QMessageBox.information(
            self,
            "CheckPrint Complete",
            f"CheckPrint finalized.\nTransmittal created at:\n{trans_dir}",
        )

        self.box_reviewer.setVisible(False)
        self.box_submitter.setVisible(False)
        self._reload_batches()

    # ------------------------------------------------------------------ Cancel CheckPrint
    def _cancel_checkprint(self):
        if not self.current_batch_id or not self.db_path:
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
                "• You may start a new CheckPrint afterwards<br><br>"
                "<span style='color:red; font-weight:bold;'>This process cannot be undone.</span>"
                "</html>"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return

        try:
            actor = SettingsManager().get("user.name", "")
            cancel_checkprint(
                self.db_path,
                batch_id=self.current_batch_id,
                actor=actor
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to cancel CheckPrint:\n{e}")
            return

        QMessageBox.information(
            self,
            "CheckPrint Cancelled",
            "The CheckPrint has been cancelled and archived.",
        )

        self.box_submitter.setVisible(False)
        self.box_reviewer.setVisible(False)
        self._reload_batches()
