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

from .checkprint_edit_dialog import CheckPrintEditDialog
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
        resubmit_all_incoming,
        cancel_checkprint,
        finalize_checkprint_to_transmittal,
        _mark_checkprint_cancelled,
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
        resubmit_all_incoming,
        cancel_checkprint,
        finalize_checkprint_to_transmittal,
        _mark_checkprint_cancelled,
    )
    from core.paths import resolve_company_library_path


from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout, QPushButton

class CommentEditDialog(QDialog):
    def __init__(self, parent, title, label, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("CheckPrintTab")  # <-- inherits your theme correctly

        layout = QVBoxLayout(self)

        lbl = QLabel(label, self)
        layout.addWidget(lbl)

        self.editor = QTextEdit(self)
        self.editor.setPlainText(text)
        layout.addWidget(self.editor)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("OK", self)
        btn_cancel = QPushButton("Cancel", self)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

    def text(self):
        return self.editor.toPlainText()

class CommentViewDialog(QDialog):
    def __init__(self, parent, reviewer, date, comment):
        super().__init__(parent)
        self.setWindowTitle("Rejection Details")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>Reviewer:</b> {reviewer}<br>"
            f"<b>Date:</b> {date}<br><br>"
            f"<b>Comment:</b>"
        )
        lbl.setTextFormat(Qt.RichText)
        layout.addWidget(lbl)

        txt = QTextEdit(self)
        txt.setPlainText(comment)
        txt.setReadOnly(True)
        layout.addWidget(txt)

        btn = QPushButton("Close", self)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class CheckPrintTab(QWidget):
    """
    CheckPrint tab.

    Layout:
      • Batch selector (combo)
      • Role selector: Submitter / Reviewer
      • Submitter view: horizontal panes (Pending, Rejected, Accepted)
      • Reviewer view: horizontal panes (Pending, Rejected, Accepted)
    """

    def __init__(self, parent=None, *, user_name: str = ""):
        super().__init__(parent)
        self.user_name = user_name
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

        self.btn_resubmit_pending_sub = QPushButton("Resubmit All Incoming")
        self.btn_resubmit_pending_sub.setFixedWidth(300)
        self.btn_resubmit_pending_sub.clicked.connect(self._resubmit_all_incoming)
        g_pend_layout.addWidget(self.btn_resubmit_pending_sub)
        sub_h.addWidget(grp_pend_sub)

        # Rejected (submitter)
        grp_rej_sub = QGroupBox("Rejected")
        g_rej_layout = QVBoxLayout(grp_rej_sub)
        self.list_rejected_sub = QListWidget()
        self._wire_list_common(self.list_rejected_sub, editable=False, for_reviewer=False)
        g_rej_layout.addWidget(self.list_rejected_sub)

        btn_row_rej_sub = QHBoxLayout()
        self.btn_resubmit_rejected_sub = QPushButton("Resubmit All Incoming")
        self.btn_resubmit_rejected_sub.setFixedWidth(300)
        self.btn_resubmit_rejected_sub.clicked.connect(self._resubmit_all_incoming)
        btn_row_rej_sub.addWidget(self.btn_resubmit_rejected_sub)

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

        self.btn_edit_docs_submitter = QPushButton("Edit Documents…")
        self.btn_edit_docs_submitter.setFixedWidth(200)
        self.btn_edit_docs_submitter.clicked.connect(self._edit_checkprint_documents)
        bottom_sub.addWidget(self.btn_edit_docs_submitter)

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

        editable = batch["status"] in {"in_progress", "submitted", "awaiting_review"}
        self.btn_edit_docs_submitter.setEnabled(editable)
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

    def _edit_checkprint_documents(self):
        if not self.db_path or not self.current_batch_id:
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if not batch or batch.get("status") not in {"in_progress", "submitted", "awaiting_review"}:
            QMessageBox.information(
                self,
                "Not editable",
                "This CheckPrint batch is not editable in its current state.",
            )
            return

        dlg = CheckPrintEditDialog(
            self,
            db_path=Path(self.db_path),
            batch_id=int(self.current_batch_id),
            user_name=self.user_name,
        )

        dlg.exec_()
        # Refresh lists after any edits
        self._load_items_for_submitter()


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

        # Correct resolution: project-root-relative path
        proj_root = self.db_path.parent.parent
        abs_cp = proj_root / rel_cp

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

    # ------------------------------------------------------------------ Previous checkprint navigation

    def _open_specific_cp_version(self, base, folder, version, ext):
        path = folder / f"{base}_CP_{version}{ext}"
        if not path.exists():
            QMessageBox.warning(self, "Missing File", f"Version CP_{version} not found:\n{path}")
            return

        try:
            if sys.platform.startswith("darwin"):
                os.system(f"open '{path}'")
            elif os.name == "nt":
                os.startfile(str(path))
            else:
                os.system(f"xdg-open '{path}'")
        except Exception as e:
            QMessageBox.critical(self, "Open File", f"Failed to open file:\n{e}")

    # ------------------------------------------------------------------ Comments: context menu + dialogs
    def _show_comment_menu(self, lw: QListWidget, pos: QPoint,
                           editable: bool, for_reviewer: bool):
        item = lw.itemAt(pos)
        if not item:
            return

        it = item.data(Qt.UserRole) or {}
        rel_cp = it.get("cp_path")
        cp_version = int(it.get("cp_version") or 1)

        menu = QMenu(self)

        # Existing menu entry
        if editable:
            act_comment = menu.addAction("View/Edit Comment…")
        else:
            act_comment = menu.addAction("View Comment…")

        # --- NEW: Build Previous Versions submenu ---
        submenu = menu.addMenu("Show Previous Versions")

        try:
            proj_root = self.db_path.parent.parent
            abs_cp = proj_root / rel_cp
            folder = abs_cp.parent
            filename = abs_cp.name

            # extract <base> and <ext>, remove "_CP_N"
            stem = filename.rsplit("_CP_", 1)[0]
            ext = "." + filename.split(".")[-1]

            # Add entries CP_1 … CP_(N-1)
            if cp_version > 1:
                for v in range(1, cp_version):
                    act = submenu.addAction(f"Open CP_{v}")
                    act.triggered.connect(
                        lambda _, base=stem, ver=v, ext=ext, fld=folder:
                        self._open_specific_cp_version(base, fld, ver, ext)
                    )
            else:
                submenu.setEnabled(False)

        except Exception:
            submenu.setEnabled(False)

        # Execute menu
        chosen = menu.exec_(lw.mapToGlobal(pos))

        if chosen == act_comment:
            self._open_comment_dialog(item, editable=editable, for_reviewer=for_reviewer)

    def _open_comment_dialog(self, item: QListWidgetItem,
                             *, editable: bool, for_reviewer: bool):
        it = item.data(Qt.UserRole) or {}
        current = it.get("last_reviewer_note") or ""

        if not editable:
            reviewer = it.get("reviewer") or "Unknown"
            date = it.get("last_submitted_on") or "Unknown"
            comment = current or "(No comment provided)"

            dlg = CommentViewDialog(self, reviewer, date, comment)
            dlg.exec_()
            return

        # Editable (reviewer)
        dlg = CommentEditDialog(self, "Reviewer Comment", "Enter reviewer comment:", current)
        if dlg.exec_() != QDialog.Accepted:
            return
        new_text = dlg.text().strip()

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
        # legacy button path; now uses incoming folder flow
        self._resubmit_all_incoming()

    def _resubmit_from_rejected(self):
        # legacy button path; now uses incoming folder flow
        self._resubmit_all_incoming()

    def _resubmit_all_incoming(self):
        if not getattr(self, "db_path", None) or not self.current_batch_id:
            QMessageBox.information(self, "CheckPrint", "No active CheckPrint batch selected.")
            return

        actor = SettingsManager().get("user.name", "") or ""

        try:
            res = resubmit_all_incoming(
                self.db_path,
                batch_id=int(self.current_batch_id),
                actor=actor,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Resubmit failed:\n{e}")
            return

        if not res.get("ok"):
            incoming_dir = res.get("incoming_dir", "CheckPrint/_CheckPrintIncoming")
            details = res.get("details")
            msg = f"Resubmit failed.\n\nIncoming folder:\n{incoming_dir}\n\nFiles must be named DOCID_REV.pdf"
            if details:
                msg += f"\n\nDetails:\n{details}"
            QMessageBox.critical(self, "CheckPrint", msg)
            return

        msg = (
            f"Resubmitted from _CheckPrintIncoming.\n\n"
            f"Updated: {res.get('updated', 0)}\n"
            f"Overwritten (pending): {res.get('overwritten', 0)}\n"
            f"Incremented (accepted/rejected): {res.get('incremented', 0)}\n"
            f"Incoming cleaned: {res.get('deleted_incoming', 0)} file(s)"
        )
        cw = res.get("cleanup_warning")
        if cw:
            msg += f"\n\nCleanup warning:\n{cw}"

        QMessageBox.information(self, "CheckPrint", msg)

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

        actor = SettingsManager().get("user.name", "")

        update_checkprint_item_status(
            self.db_path,
            item_id=it["id"],
            status="accepted",
            reviewer=actor,
        )
        self._load_items_for_reviewer()

    def _reviewer_reject(self):
        item = self.list_pending_rev.currentItem()
        if not item:
            QMessageBox.information(self, "Reviewer", "Select a pending document to reject.")
            return
        it = item.data(Qt.UserRole)

        dlg = CommentEditDialog(
            self,
            "Reject Document",
            "Optional: Enter rejection reason:",
            it.get("last_reviewer_note") or "",
        )
        if dlg.exec_() != QDialog.Accepted:
            return

        comment = dlg.text().strip()  # allow empty

        actor = SettingsManager().get("user.name", "")

        update_checkprint_item_status(
            self.db_path,
            item_id=it["id"],
            status="rejected",
            reviewer=actor,
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

        # First confirmation
        r = QMessageBox.question(
            self,
            "Cancel CheckPrint?",
            (
                "<html>"
                "Are you sure you want to cancel this CheckPrint?<br><br>"
                "• Source files will be restored<br>"
                "• The CheckPrint folder will be archived<br>"
                "• You may start a new CheckPrint afterwards<br><br>"
                "<span style='color:red;font-weight:bold;'>This cannot be undone.</span>"
                "</html>"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if r != QMessageBox.Yes:
            return

        actor = SettingsManager().get("user.name", "")
        result = cancel_checkprint(self.db_path, batch_id=self.current_batch_id, actor=actor)

        # --------------------------
        # Successful full cancellation
        # --------------------------
        if result["ok"] and result["mode"] == "full":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "The CheckPrint has been cancelled and archived."
            )
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self._reload_batches()
            return

        # --------------------------
        # Preflight blocked OR execution failed
        # --------------------------
        error_path = result.get("error_path")
        reason = result.get("error_reason") or "Unknown error."

        if result["mode"] == "blocked":
            msg = (
                "<b>Cancellation cannot proceed due to a file operation error.</b><br><br>"
                f"<b>File:</b> {error_path}<br>"
                f"<b>Reason:</b> {reason}<br><br>"
                "No files have been changed.<br><br>"
                "<b>Force DB cancellation?</b><br><br>"
                "If yes, you must manually:<br>"
                "• Restore original source file names (remove _CP_N)<br>"
                "• Rename/move the CheckPrint folder yourself<br>"
            )
        else:  # partial execution
            msg = (
                "<b>Some file operations failed during cancellation.</b><br><br>"
                f"<b>Error:</b> {reason}<br><br>"
                "Some file changes may have occurred.<br><br>"
                "<b>Force DB cancellation?</b>"
            )

        rr = QMessageBox.question(
            self,
            "Force DB Cancellation?",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if rr != QMessageBox.Yes:
            QMessageBox.information(
                self,
                "Cancellation Aborted",
                "CheckPrint cancellation has been aborted. No database changes were made."
            )
            return

        # --------------------------
        # Force DB-only cancellation
        # --------------------------
        _mark_checkprint_cancelled(self.db_path, self.current_batch_id, actor)

        QMessageBox.warning(
            self,
            "DB Cancelled (Manual Cleanup Required)",
            (
                "The CheckPrint has been cancelled in the database only.\n\n"
                "Manual cleanup is required:\n"
                "• Restore source file names (remove _CP_N)\n"
                "• Move/rename the CheckPrint folder yourself"
            )
        )

        self.box_submitter.setVisible(False)
        self.box_reviewer.setVisible(False)
        self._reload_batches()
