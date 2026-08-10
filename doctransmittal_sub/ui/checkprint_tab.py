from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

from PyQt5.QtCore import Qt, QPoint, pyqtSignal
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
    QInputDialog, QCheckBox, QLineEdit,
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
        _mark_checkprint_cancelled,
        complete_and_archive_checkprint,
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
        _mark_checkprint_cancelled,
        complete_and_archive_checkprint,
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
    def __init__(self, parent, reviewer, date, comment, *, title="CheckPrint Comment", actor_label="Reviewer"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>{actor_label}:</b> {reviewer}<br>"
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
      • Role selector: Submitter / Reviewer / Approver
      • Submitter view: final approver-facing result only
      • Reviewer view: interim recommendation only
      • Approver view: final reject / accept-minor / approve decision
    """
    registerNeedsRefresh = pyqtSignal()

    def __init__(self, parent=None, *, user_name: str = ""):
        super().__init__(parent)
        self.user_name = user_name
        self.db_path: Path | None = None
        self.current_batch_id: int | None = None
        self._batch_rows = []
        self._active_role: str | None = None  # "submitter" | "reviewer" | "approver" | None

        # Color caching
        self._colors: dict[str, QColor] = {}
        self._refresh_color_cache()

        self.setObjectName("CheckPrintTab")
        self._build_ui()
    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)

        # ==========================================================
        # Batch selector
        # ==========================================================
        box_batches = QGroupBox("CheckPrint Batch")
        box_batches.setMaximumHeight(100)
        hb = QHBoxLayout(box_batches)
        hb.addWidget(QLabel("Select batch:"))

        self.combo_batches = QComboBox()
        self.combo_batches.currentIndexChanged.connect(self._on_batch_selected)
        hb.addWidget(self.combo_batches, 1)

        self.btn_print_qa = QPushButton("Print QA Summary…")
        self.btn_print_qa.setFixedWidth(180)
        self.btn_print_qa.clicked.connect(self._print_qa_summary)
        self.btn_print_qa.setEnabled(False)
        hb.addWidget(self.btn_print_qa)

        root.addWidget(box_batches)

        # ==========================================================
        # Role selector
        # ==========================================================
        self.box_role = QGroupBox("Select Role")
        self.box_role.setMaximumHeight(100)
        mb = QHBoxLayout(self.box_role)

        self.btn_as_submitter = QPushButton("Submitter")
        self.btn_as_submitter.setFixedWidth(150)
        self.btn_as_submitter.clicked.connect(self._enter_submitter_mode)
        mb.addWidget(self.btn_as_submitter)

        self.btn_as_reviewer = QPushButton("Reviewer")
        self.btn_as_reviewer.setFixedWidth(150)
        self.btn_as_reviewer.clicked.connect(self._enter_reviewer_mode)
        mb.addWidget(self.btn_as_reviewer)

        self.btn_as_approver = QPushButton("Approver")
        self.btn_as_approver.setFixedWidth(150)
        self.btn_as_approver.clicked.connect(self._enter_approver_mode)
        mb.addWidget(self.btn_as_approver)

        mb.addStretch(1)
        root.addWidget(self.box_role)

        # ==========================================================
        # COMPLETED (HISTORY) VIEW
        # ==========================================================
        self.box_history = QGroupBox("CheckPrint History")
        self.box_history.setVisible(False)
        hv = QVBoxLayout(self.box_history)

        lbl = QLabel(
            "This CheckPrint has been completed.\n"
            "Documents below are read-only and retained for audit."
        )
        lbl.setStyleSheet("color: #aaa;")
        hv.addWidget(lbl)

        self.list_history = QListWidget()
        self.list_history.setSelectionMode(QListWidget.ExtendedSelection)
        self._wire_list_common(self.list_history, editable=False, for_reviewer=False)
        hv.addWidget(self.list_history, 1)

        root.addWidget(self.box_history, 1)

        # ==========================================================
        # SUBMITTER VIEW
        # ==========================================================
        self.box_submitter = QGroupBox("Submitter View")
        self.box_submitter.setVisible(False)
        sv = QVBoxLayout(self.box_submitter)
        sv.setStretch(0, 1)

        sub_h = QHBoxLayout()

        # ---------- Pending ----------
        grp_pend_sub = QGroupBox("Pending")
        grp_pend_sub.setSizePolicy(grp_pend_sub.sizePolicy().horizontalPolicy(),
                                   grp_pend_sub.sizePolicy().verticalPolicy())
        g_pend_layout = QVBoxLayout(grp_pend_sub)

        self.list_pending_sub = QListWidget()
        self._wire_list_common(self.list_pending_sub, editable=False, for_reviewer=False)
        g_pend_layout.addWidget(self.list_pending_sub, 1)

        self.btn_autocad_resubmit_sub = QPushButton("Resubmit Open CAD + Selected")
        self.btn_autocad_resubmit_sub.setToolTip(
            "Plots the currently active AutoCAD layout to the selected CheckPrint document filename, "
            "then immediately resubmits that one selected document."
        )
        self.btn_autocad_resubmit_sub.clicked.connect(self._resubmit_open_cad_selected)
        g_pend_layout.addWidget(self.btn_autocad_resubmit_sub)

        self.btn_resubmit_pending_sub = QPushButton("Resubmit All Incoming")
        self.btn_resubmit_pending_sub.clicked.connect(self._resubmit_all_incoming)
        g_pend_layout.addWidget(self.btn_resubmit_pending_sub)

        sub_h.addWidget(grp_pend_sub, 1)

        # ---------- Rejected ----------
        grp_rej_sub = QGroupBox("Rejected")
        g_rej_layout = QVBoxLayout(grp_rej_sub)

        self.list_rejected_sub = QListWidget()
        self._wire_list_common(self.list_rejected_sub, editable=False, for_reviewer=False)
        g_rej_layout.addWidget(self.list_rejected_sub, 1)

        sub_h.addWidget(grp_rej_sub, 1)

        # ---------- Accepted (Minor) ----------
        grp_accm_sub = QGroupBox("Accepted (Minor)")
        g_accm_layout = QVBoxLayout(grp_accm_sub)

        self.list_accepted_minor_sub = QListWidget()
        self._wire_list_common(self.list_accepted_minor_sub, editable=False, for_reviewer=False)
        g_accm_layout.addWidget(self.list_accepted_minor_sub, 1)

        sub_h.addWidget(grp_accm_sub, 1)

        # ---------- Accepted ----------
        grp_acc_sub = QGroupBox("Accepted")
        g_acc_layout = QVBoxLayout(grp_acc_sub)

        self.list_accepted_sub = QListWidget()
        self._wire_list_common(self.list_accepted_sub, editable=False, for_reviewer=False)
        g_acc_layout.addWidget(self.list_accepted_sub, 1)

        sub_h.addWidget(grp_acc_sub, 1)

        sv.addLayout(sub_h)

        # Bottom submitter controls
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
        root.addWidget(self.box_submitter, 1)

        # ==========================================================
        # REVIEWER VIEW
        # ==========================================================
        self.box_reviewer = QGroupBox("Reviewer View")
        self.box_reviewer.setVisible(False)
        rv = QVBoxLayout(self.box_reviewer)
        rv.setStretch(0, 1)

        rev_h = QHBoxLayout()

        # ---------- Pending ----------
        grp_pend_rev = QGroupBox("Pending")
        g_pend_rev_layout = QVBoxLayout(grp_pend_rev)

        self.list_pending_rev = QListWidget()
        self._wire_list_common(self.list_pending_rev, editable=True, for_reviewer=True)
        g_pend_rev_layout.addWidget(self.list_pending_rev, 1)
        self.list_pending_rev.setSelectionMode(QListWidget.ExtendedSelection)


        btn_row = QHBoxLayout()
        self.btn_accept = QPushButton("Accept")
        self.btn_accept.clicked.connect(self._reviewer_accept)

        self.btn_accept_minor = QPushButton("Accept (Minor)")
        self.btn_accept_minor.clicked.connect(self._reviewer_accept_minor)

        self.btn_reject = QPushButton("Reject")
        self.btn_reject.clicked.connect(self._reviewer_reject)

        self.btn_rev_pending = QPushButton("Move to Pending")
        self.btn_rev_pending.clicked.connect(self._reviewer_pending)

        # --- Row 2: reject/default comment controls ---
        comment_row = QHBoxLayout()

        self.chk_always_comment = QCheckBox("Always comment")
        self.chk_always_comment.setChecked(True)

        self.le_reject_comment = QLineEdit()
        self.le_reject_comment.setPlaceholderText("Review PDF")
        self.le_reject_comment.setText("Review PDF")
        self.le_reject_comment.setEnabled(True)

        self.chk_always_comment.toggled.connect(
            self.le_reject_comment.setEnabled
        )

        btn_row.addWidget(self.btn_accept)
        btn_row.addWidget(self.btn_accept_minor)
        btn_row.addWidget(self.btn_reject)
        btn_row.addWidget(self.btn_rev_pending)

        comment_row.addWidget(self.chk_always_comment)
        comment_row.addWidget(self.le_reject_comment, stretch=1)

        btn_row.addStretch(1)
        comment_row.addStretch(1)

        g_pend_rev_layout.addLayout(btn_row)
        g_pend_rev_layout.addLayout(comment_row)
        rev_h.addWidget(grp_pend_rev, 1)


        # ---------- Rejected ----------
        grp_rej_rev = QGroupBox("Rejected")
        g_rej_rev_layout = QVBoxLayout(grp_rej_rev)

        self.list_rejected_rev = QListWidget()
        self._wire_list_common(self.list_rejected_rev, editable=True, for_reviewer=True)
        g_rej_rev_layout.addWidget(self.list_rejected_rev, 1)

        rev_h.addWidget(grp_rej_rev, 1)

        # ---------- Accepted (Minor) ----------
        grp_accm_rev = QGroupBox("Accepted (Minor)")
        g_accm_rev_layout = QVBoxLayout(grp_accm_rev)

        self.list_accepted_minor_rev = QListWidget()
        self._wire_list_common(self.list_accepted_minor_rev, editable=True, for_reviewer=True)
        g_accm_rev_layout.addWidget(self.list_accepted_minor_rev, 1)

        rev_h.addWidget(grp_accm_rev, 1)

        # ---------- Accepted ----------
        grp_acc_rev = QGroupBox("Accepted")
        g_acc_rev_layout = QVBoxLayout(grp_acc_rev)

        self.list_accepted_rev = QListWidget()
        self._wire_list_common(self.list_accepted_rev, editable=True, for_reviewer=True)
        g_acc_rev_layout.addWidget(self.list_accepted_rev, 1)


        rev_h.addWidget(grp_acc_rev, 1)

        rv.addLayout(rev_h)

        # Reviewer cancel
        bottom_rev = QHBoxLayout()
        bottom_rev.addStretch(1)

        self.btn_cancel_reviewer = QPushButton("Cancel This CheckPrint")
        self.btn_cancel_reviewer.setFixedWidth(300)
        self.btn_cancel_reviewer.clicked.connect(self._cancel_checkprint)
        bottom_rev.addWidget(self.btn_cancel_reviewer)

        rv.addLayout(bottom_rev)
        root.addWidget(self.box_reviewer, 1)

        # ==========================================================
        # APPROVER VIEW
        # ==========================================================
        self.box_approver = QGroupBox("Approver View")
        self.box_approver.setVisible(False)
        av = QVBoxLayout(self.box_approver)
        av.setStretch(0, 1)

        app_h = QHBoxLayout()

        grp_pend_app = QGroupBox("Pending")
        g_pend_app_layout = QVBoxLayout(grp_pend_app)
        self.list_pending_app = QListWidget()
        self._wire_list_common(self.list_pending_app, editable=True, for_reviewer=False, for_approver=True)
        self.list_pending_app.setSelectionMode(QListWidget.ExtendedSelection)
        g_pend_app_layout.addWidget(self.list_pending_app, 1)

        app_btn_row = QHBoxLayout()
        self.btn_app_reject = QPushButton("Reject")
        self.btn_app_reject.clicked.connect(self._approver_reject)
        self.btn_app_accept_minor = QPushButton("Approve (Minor)")
        self.btn_app_accept_minor.clicked.connect(self._approver_accept_minor)
        self.btn_app_approve = QPushButton("Approve")
        self.btn_app_approve.clicked.connect(self._approver_approve)
        self.btn_app_pending = QPushButton("Move to Pending")
        self.btn_app_pending.clicked.connect(self._approver_pending)
        app_btn_row.addWidget(self.btn_app_reject)
        app_btn_row.addWidget(self.btn_app_accept_minor)
        app_btn_row.addWidget(self.btn_app_approve)
        app_btn_row.addWidget(self.btn_app_pending)
        app_btn_row.addStretch(1)
        g_pend_app_layout.addLayout(app_btn_row)

        app_comment_row = QHBoxLayout()
        self.chk_app_always_comment = QCheckBox("Always comment")
        self.chk_app_always_comment.setChecked(True)
        self.le_app_comment = QLineEdit()
        self.le_app_comment.setPlaceholderText("Approval PDF")
        self.le_app_comment.setText("Approval PDF")
        self.chk_app_always_comment.toggled.connect(self.le_app_comment.setEnabled)
        app_comment_row.addWidget(self.chk_app_always_comment)
        app_comment_row.addWidget(self.le_app_comment, stretch=1)
        app_comment_row.addStretch(1)
        g_pend_app_layout.addLayout(app_comment_row)
        app_h.addWidget(grp_pend_app, 1)

        grp_rej_app = QGroupBox("Rejected")
        g_rej_app_layout = QVBoxLayout(grp_rej_app)
        self.list_rejected_app = QListWidget()
        self._wire_list_common(self.list_rejected_app, editable=True, for_reviewer=False, for_approver=True)
        self.list_rejected_app.setSelectionMode(QListWidget.ExtendedSelection)
        g_rej_app_layout.addWidget(self.list_rejected_app, 1)
        app_h.addWidget(grp_rej_app, 1)

        grp_accm_app = QGroupBox("Approved (Minor)")
        g_accm_app_layout = QVBoxLayout(grp_accm_app)
        self.list_accepted_minor_app = QListWidget()
        self._wire_list_common(self.list_accepted_minor_app, editable=True, for_reviewer=False, for_approver=True)
        self.list_accepted_minor_app.setSelectionMode(QListWidget.ExtendedSelection)
        g_accm_app_layout.addWidget(self.list_accepted_minor_app, 1)
        app_h.addWidget(grp_accm_app, 1)


        grp_appr_app = QGroupBox("Approved")
        g_appr_app_layout = QVBoxLayout(grp_appr_app)
        self.list_approved_app = QListWidget()
        self._wire_list_common(self.list_approved_app, editable=True, for_reviewer=False, for_approver=True)
        self.list_approved_app.setSelectionMode(QListWidget.ExtendedSelection)
        g_appr_app_layout.addWidget(self.list_approved_app, 1)
        self.btn_complete = QPushButton("Complete & Archive")
        self.btn_complete.clicked.connect(self._complete_checkprint)
        g_appr_app_layout.addWidget(self.btn_complete)
        app_h.addWidget(grp_appr_app, 1)

        av.addLayout(app_h)

        bottom_app = QHBoxLayout()
        bottom_app.addStretch(1)
        self.btn_cancel_approver = QPushButton("Cancel This CheckPrint")
        self.btn_cancel_approver.setFixedWidth(300)
        self.btn_cancel_approver.clicked.connect(self._cancel_checkprint)
        bottom_app.addWidget(self.btn_cancel_approver)
        av.addLayout(bottom_app)
        root.addWidget(self.box_approver, 1)

    # ------------------------------------------------------------------ wiring helpers
    def _wire_list_common(self, lw: QListWidget, *, editable: bool, for_reviewer: bool, for_approver: bool = False):
        lw.itemDoubleClicked.connect(self._open_cp_item)
        lw.setContextMenuPolicy(Qt.CustomContextMenu)
        lw.customContextMenuRequested.connect(
            lambda pos, w=lw, e=editable, r=for_reviewer, a=for_approver: self._show_comment_menu(w, pos, e, r, a)
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
            self.btn_as_approver.setEnabled(False)
            if hasattr(self, "btn_print_qa"):
                self.btn_print_qa.setEnabled(False)
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
            self.btn_as_approver.setEnabled(False)
            if hasattr(self, "btn_print_qa"):
                self.btn_print_qa.setEnabled(False)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self.box_approver.setVisible(False)
            self.box_approver.setVisible(False)
            self.box_approver.setVisible(False)

    def _on_batch_selected(self, idx: int):
        # Always reset the view state first (prevents "sticky"/frozen history state).
        self.box_history.setVisible(False)
        self.box_submitter.setVisible(False)
        self.box_reviewer.setVisible(False)
        self.box_approver.setVisible(False)

        if idx < 0 or not self._batch_rows or not self.db_path:
            self.current_batch_id = None
            self._active_role = None
            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            self.btn_as_approver.setEnabled(False)
            if hasattr(self, "btn_print_qa"):
                self.btn_print_qa.setEnabled(False)
            self._update_role_buttons(None)
            if hasattr(self, "box_role"):
                self.box_role.setVisible(True)
            return

        # Resolve the selected batch id FIRST (this was the freeze bug).
        batch_id = self.combo_batches.itemData(idx)
        self.current_batch_id = int(batch_id) if batch_id is not None else None

        if not self.current_batch_id:
            self._active_role = None
            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            self.btn_as_approver.setEnabled(False)
            if hasattr(self, "btn_print_qa"):
                self.btn_print_qa.setEnabled(False)
            self._update_role_buttons(None)
            if hasattr(self, "box_role"):
                self.box_role.setVisible(True)
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if hasattr(self, "btn_print_qa"):
            self.btn_print_qa.setEnabled(bool(batch))

        # Completed batches: show history immediately, no role selection.
        if batch and (batch.get("status") == "completed"):
            self._active_role = None
            self._update_role_buttons(None)

            self.btn_as_submitter.setEnabled(False)
            self.btn_as_reviewer.setEnabled(False)
            self.btn_as_approver.setEnabled(False)
            if hasattr(self, "box_role"):
                self.box_role.setVisible(False)

            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self.box_approver.setVisible(False)
            self.box_history.setVisible(True)
            self._load_history_view()
            return

        # Non-completed: role selector applies.
        if hasattr(self, "box_role"):
            self.box_role.setVisible(True)

        self.btn_as_submitter.setEnabled(True)
        self.btn_as_reviewer.setEnabled(True)
        self.btn_as_approver.setEnabled(True)

        # If user already picked a role earlier, keep them in that role when switching batches.
        if self._active_role == "submitter":
            self._enter_submitter_mode()
        elif self._active_role == "reviewer":
            self._enter_reviewer_mode()
        elif self._active_role == "approver":
            self._enter_approver_mode()
        else:
            self._update_role_buttons(None)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self.box_approver.setVisible(False)

    # ------------------------------------------------------------------ QA PDF
    def _print_qa_summary(self):
        if not self.db_path or not self.current_batch_id:
            QMessageBox.information(self, "CheckPrint QA", "Select a CheckPrint batch first.")
            return

        try:
            from datetime import date
            import webbrowser
            from ..services.db import get_project
            from ..services.checkprint_qa_pdf import export_checkprint_qa_pdf
        except Exception:
            from datetime import date
            import webbrowser
            from services.db import get_project  # type: ignore
            from services.checkprint_qa_pdf import export_checkprint_qa_pdf  # type: ignore

        db_path = Path(self.db_path)
        batch = get_checkprint_batch(db_path, int(self.current_batch_id))
        if not batch:
            QMessageBox.warning(self, "CheckPrint QA", "Selected CheckPrint batch could not be loaded.")
            return

        proj = get_project(db_path) or {}
        project_code = (proj.get("project_code") or "PROJECT").strip() or "PROJECT"
        batch_code = (batch.get("code") or f"CP-{self.current_batch_id}").strip()

        base = db_path.parent
        if base.name.startswith("."):
            base = base.parent
        out_dir = base / "Reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_batch = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in batch_code)
        out_pdf = out_dir / f"{project_code}-{safe_batch}-CheckPrint-QA-{date.today().isoformat()}.pdf"

        try:
            pdf_path = export_checkprint_qa_pdf(
                out_pdf,
                db_path=db_path,
                batch_id=int(self.current_batch_id),
                generated_by=SettingsManager().get("user.name", "") or "",
            )
        except Exception as e:
            QMessageBox.critical(self, "CheckPrint QA", f"Failed to build QA PDF:\n{e}")
            return

        QMessageBox.information(self, "CheckPrint QA", f"Saved:\n{pdf_path}")
        try:
            webbrowser.open_new(str(pdf_path))
        except Exception:
            pass

    # ------------------------------------------------------------------ UX
    def _update_role_buttons(self, role: str):
        """
        role = 'submitter', 'reviewer' or 'approver'
        Makes the selected role visually obvious.
        """
        self.btn_as_submitter.setStyleSheet("")
        self.btn_as_reviewer.setStyleSheet("")
        self.btn_as_approver.setStyleSheet("")

        style = "background-color: #d0d0d0; font-weight: bold;"
        if role == "submitter":
            self.btn_as_submitter.setStyleSheet(style)
        elif role == "reviewer":
            self.btn_as_reviewer.setStyleSheet(style)
        elif role == "approver":
            self.btn_as_approver.setStyleSheet(style)

    def _refresh_color_cache(self):
        sm = SettingsManager()
        c = sm.get("ui.colors.checkprint", {})
        self._colors = {
            "pending": QColor(c.get("pending", "#D2820A")),
            "rejected": QColor(c.get("rejected", "#C83C3C")),
            "accepted_minor": QColor(c.get("accepted_minor", "#B48C14")),
            "accepted": QColor(c.get("accepted", "#26B96E")),
            "approved": QColor(c.get("approved", "#199650")),
        }

    def refresh_ui_colors(self):
        self._refresh_color_cache()
        if self._active_role == "submitter":
            self._load_items_for_submitter()
        elif self._active_role == "reviewer":
            self._load_items_for_reviewer()
        elif self._active_role == "approver":
            self._load_items_for_approver()
        
        # Also reload history if visible
        if self.box_history.isVisible():
            self._load_history_view()

    # ------------------------------------------------------------------ History mode

    def _load_history_view(self):
        self.list_history.clear()

        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)

        for it in items:
            st = (it.get("status") or "").lower()
            if st not in {"approved", "accepted", "accepted_minor"}:
                continue

            disp = (
                f"{it['doc_id']}  "
                f"[Rev {it['revision']}]  "
                f"Status: {self._final_status_label(it['status'])}  "
                f"CP:{it['cp_version']}  "
                f"[A:{self._initials(it.get('approver'))}]"
            )
            row = QListWidgetItem(disp)
            row.setData(Qt.UserRole, it)

            if st == "accepted_minor":
                row.setForeground(self._colors["accepted_minor"])
            elif st == "approved":
                row.setForeground(self._colors["approved"])
            else:
                row.setForeground(self._colors["accepted"])

            self.list_history.addItem(row)

    # ------------------------------------------------------------------ Submitter mode
    def _enter_submitter_mode(self):
        if not self.current_batch_id or not self.db_path:
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if batch and batch["status"] == "completed":
            self._active_role = None
            if hasattr(self, "box_role"):
                self.box_role.setVisible(False)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self.box_approver.setVisible(False)
            self.box_history.setVisible(True)
            self._update_role_buttons(None)
            self._load_history_view()
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if batch and batch["status"] == "cancelled":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "This CheckPrint has already been cancelled and cannot be edited.",
            )
            return

        self._active_role = "submitter"
        if hasattr(self, "box_role"):
            self.box_role.setVisible(True)

        self.box_history.setVisible(False)
        self.box_reviewer.setVisible(False)
        self.box_approver.setVisible(False)
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
        self.list_accepted_minor_sub.clear()

        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)
        self._populate_submitter_lists(items)

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
        if batch and batch["status"] == "completed":
            self._active_role = None
            if hasattr(self, "box_role"):
                self.box_role.setVisible(False)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self.box_approver.setVisible(False)
            self.box_history.setVisible(True)
            self._update_role_buttons(None)
            self._load_history_view()
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if batch and batch["status"] == "cancelled":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "This CheckPrint has already been cancelled and cannot be edited.",
            )
            return

        self._active_role = "reviewer"
        if hasattr(self, "box_role"):
            self.box_role.setVisible(True)

        self.box_history.setVisible(False)
        self.box_submitter.setVisible(False)
        self.box_approver.setVisible(False)
        self.box_reviewer.setVisible(True)
        self._update_role_buttons("reviewer")
        self._load_items_for_reviewer()

        self.btn_cancel_reviewer.setEnabled(batch["status"] != "cancelled")

    def _load_items_for_reviewer(self):
        self.list_pending_rev.clear()
        self.list_rejected_rev.clear()
        self.list_accepted_minor_rev.clear()
        self.list_accepted_rev.clear()

        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)
        self._populate_reviewer_lists(items)

    # ------------------------------------------------------------------ Approver mode
    def _enter_approver_mode(self):
        if not self.current_batch_id or not self.db_path:
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if batch and batch["status"] == "completed":
            self._active_role = None
            if hasattr(self, "box_role"):
                self.box_role.setVisible(False)
            self.box_submitter.setVisible(False)
            self.box_reviewer.setVisible(False)
            self.box_approver.setVisible(False)
            self.box_history.setVisible(True)
            self._update_role_buttons(None)
            self._load_history_view()
            return

        if batch and batch["status"] == "cancelled":
            QMessageBox.information(
                self,
                "CheckPrint Cancelled",
                "This CheckPrint has already been cancelled and cannot be edited.",
            )
            return

        self._active_role = "approver"
        if hasattr(self, "box_role"):
            self.box_role.setVisible(True)

        self.box_history.setVisible(False)
        self.box_submitter.setVisible(False)
        self.box_reviewer.setVisible(False)
        self.box_approver.setVisible(True)
        self._update_role_buttons("approver")
        self._load_items_for_approver()

        self.btn_cancel_approver.setEnabled(batch["status"] != "cancelled")

    def _load_items_for_approver(self):
        self.list_pending_app.clear()
        self.list_rejected_app.clear()
        self.list_accepted_minor_app.clear()
        self.list_approved_app.clear()

        if not self.db_path or not self.current_batch_id:
            return

        items = get_checkprint_items(self.db_path, self.current_batch_id)
        self._populate_approver_lists(items)

    # ------------------------------------------------------------------ Common list population
    @staticmethod
    def _initials(name: str | None) -> str:
        """Return compact initials for display in CheckPrint list rows."""
        txt = (name or "").strip()
        if not txt:
            return "-"
        # Strip common email domain noise if the setting stores an address.
        if "@" in txt and " " not in txt:
            txt = txt.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
        parts = [p for p in txt.replace(",", " ").split() if p]
        if not parts:
            return "-"
        if len(parts) == 1:
            token = parts[0]
            return token[:2].upper() if len(token) > 1 else token.upper()
        return "".join(p[0].upper() for p in parts[:3])

    @staticmethod
    def _status_label(status: str | None) -> str:
        st = (status or "pending").lower()
        labels = {
            "pending": "Pending",
            "rejected": "Rejected",
            "accepted_minor": "Accepted Minor",
            "accepted": "Accepted",
            "approved": "Approved",
        }
        return labels.get(st, st.replace("_", " ").title())

    @staticmethod
    def _final_status_label(status: str | None) -> str:
        st = (status or "pending").lower()
        labels = {
            "pending": "Pending",
            "rejected": "Rejected",
            "accepted_minor": "Approved Minor",
            "accepted": "Accepted",
            "approved": "Approved",
        }
        return labels.get(st, st.replace("_", " ").title())

    @staticmethod
    def _status_compare_key(status: str | None) -> str:
        """Normalise reviewer/approver statuses for divergence checks."""
        st = (status or "pending").lower()
        if st in {"accepted", "approved"}:
            return "accepted"
        return st

    def _approver_differs_from_reviewer(self, it: dict) -> bool:
        """Return True only when an actioned approver outcome conflicts with reviewer advice."""
        final_status = (it.get("status") or "pending").lower()
        reviewer_status = (it.get("reviewer_status") or "pending").lower()

        # Pending is not an approver decision. Do not flag a difference while the
        # item is merely awaiting approver action.
        if final_status == "pending":
            return False

        # Only final/actioned approver outcomes should be compared.
        if final_status not in {"rejected", "accepted_minor", "accepted", "approved"}:
            return False

        # If the reviewer has moved their own item back to Pending, that is a
        # valid reviewer state. Do not flag the old approver/final result as a
        # reviewer/approver conflict until the reviewer gives a new recommendation.
        if reviewer_status == "pending":
            return False

        return self._status_compare_key(final_status) != self._status_compare_key(reviewer_status)

    def _approver_status_marker(self, it: dict, *, compare_to_reviewer: bool = False) -> str:
        """Return compact final approver action text for list rows."""
        st = (it.get("status") or "pending").lower()

        # Pending means the approver has not actioned the item yet. It should not
        # show as an approver decision or as a reviewer/approver difference.
        if st == "pending":
            return ""

        prefix = "⚑ " if compare_to_reviewer and self._approver_differs_from_reviewer(it) else ""
        return f"{prefix}A:{self._initials(it.get('approver'))} - {self._final_status_label(st)}"

    def _apply_approver_divergence_cue(self, row: QListWidgetItem, it: dict) -> None:
        """Cue reviewer rows where the actioned approver outcome differs."""
        if not self._approver_differs_from_reviewer(it):
            return

        # Do not set a background colour here. The previous full-row highlight
        # made text unreadable under some Windows/Qt themes. Keep only the inline
        # flag marker and make the row bold.
        try:
            font = row.font()
            font.setBold(True)
            row.setFont(font)
        except Exception:
            pass

        row.setToolTip(
            "Approver decision differs from reviewer recommendation.\n"
            f"Reviewer: {self._status_label(it.get('reviewer_status'))}\n"
            f"Approver: {self._final_status_label(it.get('status'))}"
        )

    def _make_item_row(
        self,
        it: dict,
        *,
        status_key: str = "status",
        show_submitter: bool = False,
        show_reviewer: bool = False,
        show_approver: bool = False,
        show_reviewer_status: bool = False,
        show_approver_status: bool = False,
        compare_approver_to_reviewer: bool = False,
    ) -> QListWidgetItem:
        st = (it.get(status_key) or "pending").lower()
        label = self._final_status_label(st) if status_key == "status" else self._status_label(st)
        disp = f"{it['doc_id']}  [Rev {it['revision']}]  Status: {label}  CP:{it['cp_version']}"

        bits: list[str] = []
        if show_submitter:
            bits.append(f"S:{self._initials(it.get('submitter'))}")
        if show_reviewer:
            bits.append(f"R:{self._initials(it.get('reviewer'))}")
        if show_approver:
            bits.append(f"A:{self._initials(it.get('approver'))}")
        if show_reviewer_status:
            rv = (it.get("reviewer_status") or "pending").lower()
            bits.append(f"Review:{self._status_label(rv)}")
        if show_approver_status:
            marker = self._approver_status_marker(it, compare_to_reviewer=compare_approver_to_reviewer)
            if marker:
                bits.append(marker)

        if bits:
            disp += "  [" + "  ".join(bits) + "]"

        row = QListWidgetItem(disp)
        row.setData(Qt.UserRole, it)
        return row

    def _populate_submitter_lists(self, items):
        for it in items:
            st = (it.get("status") or "pending").lower()
            row = self._make_item_row(it, status_key="status", show_approver=(st != "pending"))
            if st == "rejected":
                row.setForeground(self._colors["rejected"])
                self.list_rejected_sub.addItem(row)
            elif st == "accepted_minor":
                row.setForeground(self._colors["accepted_minor"])
                self.list_accepted_minor_sub.addItem(row)
            elif st in {"accepted", "approved"}:
                row.setForeground(self._colors["accepted"])
                self.list_accepted_sub.addItem(row)
            else:
                row.setForeground(self._colors["pending"])
                self.list_pending_sub.addItem(row)

    def _populate_reviewer_lists(self, items):
        """Populate reviewer lists using reviewer_status only.

        The approver/final status is intentionally display-only in the Reviewer
        view. This keeps the roles mutually independent, so a document rejected
        by the approver can still be moved back to the Reviewer's Pending list.
        """
        for it in items:
            st = (it.get("reviewer_status") or "pending").lower()
            if st not in {"pending", "rejected", "accepted_minor", "accepted"}:
                st = "pending"

            row = self._make_item_row(
                it,
                status_key="reviewer_status",
                show_submitter=True,
                show_approver_status=True,
                compare_approver_to_reviewer=True,
            )
            self._apply_approver_divergence_cue(row, it)

            if st == "rejected":
                row.setForeground(self._colors["rejected"])
                self.list_rejected_rev.addItem(row)
            elif st == "accepted_minor":
                row.setForeground(self._colors["accepted_minor"])
                self.list_accepted_minor_rev.addItem(row)
            elif st == "accepted":
                row.setForeground(self._colors["accepted"])
                self.list_accepted_rev.addItem(row)
            else:
                row.setForeground(self._colors["pending"])
                self.list_pending_rev.addItem(row)

    def _populate_approver_lists(self, items):
        for it in items:
            st = (it.get("status") or "pending").lower()
            rv = (it.get("reviewer_status") or "pending").lower()
            row = self._make_item_row(
                it,
                status_key="status",
                show_submitter=True,
                show_reviewer=True,
                show_reviewer_status=True,
            )

            if st == "rejected":
                row.setForeground(self._colors["rejected"])
                self.list_rejected_app.addItem(row)
            elif st == "accepted_minor":
                row.setForeground(self._colors["accepted_minor"])
                self.list_accepted_minor_app.addItem(row)
            elif st in {"accepted", "approved"}:
                # Legacy `accepted` items from earlier testing are shown under Approved
                # because the Approver role no longer has a separate Accept outcome.
                row.setForeground(self._colors["approved"])
                self.list_approved_app.addItem(row)
            else:
                # Pending approver items are colour-coded by reviewer recommendation.
                if rv == "rejected":
                    row.setForeground(self._colors["rejected"])
                elif rv == "accepted_minor":
                    row.setForeground(self._colors["accepted_minor"])
                elif rv == "accepted":
                    row.setForeground(self._colors["accepted"])
                else:
                    # No reviewer action yet: leave neutral / uncoloured.
                    pass
                self.list_pending_app.addItem(row)

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
                           editable: bool, for_reviewer: bool, for_approver: bool = False):
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
            self._open_comment_dialog(item, editable=editable, for_reviewer=for_reviewer, for_approver=for_approver)

    def _open_comment_dialog(self, item: QListWidgetItem,
                             *, editable: bool, for_reviewer: bool, for_approver: bool = False):
        it = item.data(Qt.UserRole) or {}
        role = "approver" if for_approver else "reviewer"
        current = it.get("last_approver_note") if role == "approver" else it.get("last_reviewer_note")
        current = current or ""

        if not editable:
            if it.get("approver"):
                actor = it.get("approver") or "Unknown"
                date = it.get("last_approved_on") or "Unknown"
                comment = (it.get("last_approver_note") or "") or "(No comment provided)"
                dlg = CommentViewDialog(self, actor, date, comment, title="Approver Comment", actor_label="Approver")
            else:
                actor = it.get("reviewer") or "Unknown"
                date = it.get("last_reviewed_on") or it.get("last_submitted_on") or "Unknown"
                comment = (it.get("last_reviewer_note") or "") or "(No comment provided)"
                dlg = CommentViewDialog(self, actor, date, comment, title="Reviewer Comment", actor_label="Reviewer")
            dlg.exec_()
            return

        if role == "approver":
            dlg = CommentEditDialog(self, "Approver Comment", "Enter approver comment:", current)
        else:
            dlg = CommentEditDialog(self, "Reviewer Comment", "Enter reviewer comment:", current)
        if dlg.exec_() != QDialog.Accepted:
            return
        new_text = dlg.text().strip()

        # Persist via DB
        update_checkprint_item_status(
            self.db_path,
            item_id=it["id"],
            note=new_text,
            role=role,
            actor=SettingsManager().get("user.name", "") or "",
        )

        # Refresh lists from DB for whichever view we're in
        if for_approver:
            self._load_items_for_approver()
        elif for_reviewer:
            self._load_items_for_reviewer()
        else:
            self._load_items_for_submitter()

    # Submitter button to view comment on rejected
    def _view_comment_submitter(self):
        item = self.list_rejected_sub.currentItem()
        if not item:
            QMessageBox.information(self, "Comment", "Select a rejected document.")
            return
        self._open_comment_dialog(item, editable=False, for_reviewer=False, for_approver=False)

    # ------------------------------------------------------------------ Submitter resubmission

    def _submitter_selected_items(self):
        return self._selected_from_lists(
            self.list_pending_sub,
            self.list_rejected_sub,
            self.list_accepted_minor_sub,
            self.list_accepted_sub,
        )

    def _resubmit_open_cad_selected(self):
        """Plot the active AutoCAD layout and immediately resubmit one selected item."""
        if not getattr(self, "db_path", None) or not self.current_batch_id:
            QMessageBox.information(self, "CheckPrint", "No active CheckPrint batch selected.")
            return

        batch = get_checkprint_batch(self.db_path, self.current_batch_id)
        if not batch or batch.get("status") not in {"in_progress", "submitted", "awaiting_review"}:
            QMessageBox.information(
                self,
                "CheckPrint",
                "This CheckPrint batch is not editable in its current state.",
            )
            return

        selected = self._submitter_selected_items()
        if len(selected) != 1:
            QMessageBox.information(
                self,
                "AutoCAD Resubmit",
                "Select exactly one CheckPrint document in the Submitter view.\n\n"
                "The currently active AutoCAD drawing/layout will be plotted against that selected document.",
            )
            return

        it = selected[0].data(Qt.UserRole) or {}
        item_id = int(it.get("id") or 0)
        doc_id = str(it.get("doc_id") or "").strip()
        revision = str(it.get("revision") or "").strip()
        if not item_id or not doc_id:
            QMessageBox.warning(self, "AutoCAD Resubmit", "Selected item is missing its CheckPrint document data.")
            return

        try:
            try:
                from ..services.autocad_plot_service import checkprint_incoming_dir, plot_active_layout_to_pdf, active_autocad_summary
            except ImportError:
                from services.autocad_plot_service import checkprint_incoming_dir, plot_active_layout_to_pdf, active_autocad_summary  # type: ignore
        except Exception as e:
            QMessageBox.critical(self, "AutoCAD Resubmit", f"AutoCAD plotting service could not be loaded:\n{e}")
            return

        incoming_dir = checkprint_incoming_dir(Path(self.db_path))
        output_name = f"{doc_id}_{revision}.pdf" if revision else f"{doc_id}.pdf"
        output_pdf = incoming_dir / output_name

        if output_pdf.exists():
            ans = QMessageBox.question(
                self,
                "Overwrite Incoming PDF?",
                f"The incoming PDF already exists:\n\n{output_pdf}\n\nOverwrite it and continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
            try:
                output_pdf.unlink()
            except Exception as e:
                QMessageBox.critical(self, "AutoCAD Resubmit", f"Could not remove existing incoming PDF:\n{e}")
                return

        try:
            summary = active_autocad_summary()
        except Exception as e:
            QMessageBox.critical(self, "AutoCAD Resubmit", str(e))
            return

        actor = SettingsManager().get("user.name", "") or ""
        old_final = str(it.get("status") or "pending").strip().lower() or "pending"
        old_reviewer = str(it.get("reviewer_status") or "pending").strip().lower() or "pending"
        mode = "increment" if old_final != "pending" or old_reviewer != "pending" else "overwrite"

        self._debug_checkprint_log(
            "AUTOCAD_QUICK_RESUBMIT_START "
            f"item_id={item_id} doc_id={doc_id!r} revision={revision!r} mode={mode!r} "
            f"acad_doc={summary.get('document_name')!r} layout={summary.get('layout_name')!r} "
            f"output={str(output_pdf)!r}"
        )

        try:
            from PyQt5.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                plot_result = plot_active_layout_to_pdf(output_pdf, timeout_seconds=120)
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as e:
            QMessageBox.critical(self, "AutoCAD Resubmit", f"AutoCAD plot failed:\n\n{e}")
            return

        try:
            if mode == "overwrite":
                overwrite_checkprint_items(
                    self.db_path,
                    batch_id=int(self.current_batch_id),
                    item_id_to_new_path={item_id: output_pdf},
                    submitter=actor,
                )
            else:
                resubmit_checkprint_items(
                    self.db_path,
                    batch_id=int(self.current_batch_id),
                    item_id_to_new_path={item_id: output_pdf},
                    submitter=actor,
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "AutoCAD Resubmit",
                "The PDF was plotted, but CheckPrint resubmission failed.\n\n"
                f"The plotted PDF has been left here:\n{output_pdf}\n\n"
                f"Error:\n{e}",
            )
            return

        try:
            if output_pdf.exists():
                output_pdf.unlink()
        except Exception:
            pass

        self._debug_checkprint_log(
            "AUTOCAD_QUICK_RESUBMIT_DONE "
            f"item_id={item_id} doc_id={doc_id!r} revision={revision!r} mode={mode!r} "
            f"acad_doc={plot_result.get('document_name')!r} layout={plot_result.get('layout_name')!r}"
        )

        QMessageBox.information(
            self,
            "AutoCAD Resubmit",
            "Active AutoCAD layout plotted and selected CheckPrint document resubmitted.\n\n"
            f"Document: {doc_id}\n"
            f"Revision: {revision or '-'}\n"
            f"AutoCAD drawing: {plot_result.get('document_name') or summary.get('document_name') or '-'}\n"
            f"Layout: {plot_result.get('layout_name') or summary.get('layout_name') or '-'}\n"
            f"Mode: {'New CP iteration' if mode == 'increment' else 'Overwrite pending CP'}",
        )

        self.registerNeedsRefresh.emit()
        self._load_items_for_submitter()
        self._load_items_for_reviewer()
        self._load_items_for_approver()

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

        errors = res.get("errors", {}) or {}

        # Bridge newer `details` structure into legacy error buckets
        details = res.get("details", []) or []
        if details:
            unmatched = errors.setdefault("unmatched", [])
            for d in details:
                if d.get("error") == "no_match":
                    unmatched.append(d.get("file"))

        has_errors = any(
            errors.get(k) for k in ("unmatched", "bad_format", "duplicate")
        )

        if not res.get("ok") or has_errors:
            incoming_dir = res.get("incoming_dir", "CheckPrint/_CheckPrintIncoming")
            register_examples = res.get("register_examples", []) or []

            msg = (
                "Resubmission failed.\n\n"
                f"Incoming folder:\n{incoming_dir}\n\n"
            )

            def _fmt_sample(title: str, items: list, limit: int = 5) -> str:
                if not items:
                    return ""
                shown = items[:limit]
                txt = "\n".join(f"  • {x}" for x in shown)
                more = f"\n  … and {len(items) - limit} more" if len(items) > limit else ""
                return f"{title} ({len(items)}):\n{txt}{more}\n"

            msg += _fmt_sample(
                "Files not matching any register document",
                errors.get("unmatched", []),
            )
            msg += _fmt_sample(
                "Files with invalid naming (expected DOCID_REV.pdf)",
                errors.get("bad_format", []),
            )
            msg += _fmt_sample(
                "Duplicate submissions",
                errors.get("duplicate", []),
            )

            if register_examples:
                msg += (
                        "Register expects filenames similar to:\n"
                        + "\n".join(f"  • {x}" for x in register_examples[:5])
                        + ("\n  …" if len(register_examples) > 5 else "")
                        + "\n"
                )

            msg += (
                "\nFix the issues above and try again.\n"
                "Tip: Incoming is a transient buffer — only correctly named files should be placed here."
            )

            QMessageBox.critical(self, "CheckPrint – Resubmit failed", msg)
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
        self.registerNeedsRefresh.emit()
        self._load_items_for_submitter()
        self._load_items_for_reviewer()
        self._load_items_for_approver()

    # ------------------------------------------------------------------ Role actions
    def _selected_from_lists(self, *lists: QListWidget) -> list[QListWidgetItem]:
        selected = []
        seen = set()
        for lw in lists:
            for item in lw.selectedItems():
                it = item.data(Qt.UserRole) or {}
                iid = it.get("id")
                key = int(iid) if iid is not None else id(item)
                if key not in seen:
                    seen.add(key)
                    selected.append(item)
        return selected

    def _reviewer_selected_items(self):
        return self._selected_from_lists(
            self.list_pending_rev,
            self.list_rejected_rev,
            self.list_accepted_minor_rev,
            self.list_accepted_rev,
        )

    def _approver_selected_items(self):
        return self._selected_from_lists(
            self.list_pending_app,
            self.list_rejected_app,
            self.list_accepted_minor_app,
            self.list_approved_app,
        )

    def _debug_checkprint_log_path(self) -> Path | None:
        try:
            if getattr(self, "db_path", None):
                return Path(self.db_path).parent / "checkprint_debug.log"
        except Exception:
            pass
        return None

    def _debug_checkprint_log(self, message: str) -> None:
        """Print and persist CheckPrint move diagnostics for intermittent selection issues."""
        try:
            line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {message}"
            print(line, flush=True)
            log_path = self._debug_checkprint_log_path()
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception:
            # Diagnostics must never interrupt CheckPrint actions.
            pass

    def _debug_role_selection_snapshot(self, role: str) -> list[dict]:
        if role == "reviewer":
            source_lists = [
                ("reviewer.pending", self.list_pending_rev),
                ("reviewer.rejected", self.list_rejected_rev),
                ("reviewer.accepted_minor", self.list_accepted_minor_rev),
                ("reviewer.accepted", self.list_accepted_rev),
            ]
        else:
            source_lists = [
                ("approver.pending", self.list_pending_app),
                ("approver.rejected", self.list_rejected_app),
                ("approver.approved_minor", self.list_accepted_minor_app),
                ("approver.approved", self.list_approved_app),
            ]

        snapshot: list[dict] = []
        for list_name, lw in source_lists:
            try:
                for item in lw.selectedItems():
                    it = item.data(Qt.UserRole) or {}
                    snapshot.append({
                        "list": list_name,
                        "item_id": it.get("id"),
                        "doc_id": it.get("doc_id"),
                        "revision": it.get("revision"),
                        "final_status": it.get("status"),
                        "reviewer_status": it.get("reviewer_status"),
                        "cp_version": it.get("cp_version"),
                    })
            except Exception as exc:
                snapshot.append({"list": list_name, "error": repr(exc)})
        return snapshot

    def _debug_checkprint_action_start(self, role: str, status: str, items: list[QListWidgetItem]) -> None:
        snapshot = self._debug_role_selection_snapshot(role)
        selected_ids = []
        for item in items:
            it = item.data(Qt.UserRole) or {}
            selected_ids.append(it.get("id"))
        self._debug_checkprint_log(
            f"UI_ACTION_START role={role} requested_status={status!r} "
            f"selected_count={len(items)} selected_ids={selected_ids} snapshot={snapshot}"
        )

    def _debug_checkprint_action_item(self, role: str, status: str, item: QListWidgetItem) -> None:
        it = item.data(Qt.UserRole) or {}
        self._debug_checkprint_log(
            f"UI_ACTION_ITEM role={role} requested_status={status!r} "
            f"item_id={it.get('id')} doc_id={it.get('doc_id')!r} rev={it.get('revision')!r} "
            f"old_final={it.get('status')!r} old_reviewer={it.get('reviewer_status')!r} "
            f"cp_version={it.get('cp_version')!r}"
        )

    def _debug_checkprint_post_reload(self, role: str, item_ids: list[int]) -> None:
        """Log the post-reload DB state and the UI list each changed item should occupy."""
        try:
            if not item_ids or not self.db_path or not self.current_batch_id:
                return
            ids = {int(x) for x in item_ids if x is not None}
            batch_items = get_checkprint_items(self.db_path, self.current_batch_id)
            for it in batch_items:
                try:
                    item_id = int(it.get("id"))
                except Exception:
                    continue
                if item_id not in ids:
                    continue

                final_status = (it.get("status") or "pending").lower()
                reviewer_status = (it.get("reviewer_status") or "pending").lower()

                if role == "reviewer":
                    route = reviewer_status if reviewer_status in {"rejected", "accepted_minor", "accepted"} else "pending"
                    route_list = f"reviewer.{route}"
                else:
                    if final_status == "rejected":
                        route_list = "approver.rejected"
                    elif final_status == "accepted_minor":
                        route_list = "approver.approved_minor"
                    elif final_status in {"accepted", "approved"}:
                        route_list = "approver.approved"
                    else:
                        route_list = "approver.pending"

                self._debug_checkprint_log(
                    f"UI_ACTION_POST_RELOAD_ITEM role={role} item_id={item_id} "
                    f"doc_id={it.get('doc_id')!r} final_status={final_status!r} "
                    f"reviewer_status={reviewer_status!r} route_list={route_list!r}"
                )
        except Exception as exc:
            self._debug_checkprint_log(f"UI_ACTION_POST_RELOAD_ERROR role={role} error={exc!r}")

    def _reviewer_action(self, status: str):
        items = self._reviewer_selected_items()
        self._debug_checkprint_action_start("reviewer", status, items)
        if not items:
            QMessageBox.information(self, "Reviewer", "Select one or more documents.")
            return

        actor = SettingsManager().get("user.name", "") or ""
        comment = ""
        if status in {"accepted_minor", "rejected"} and self.chk_always_comment.isChecked():
            comment = self.le_reject_comment.text().strip() or "Review PDF"

        changed_ids: list[int] = []
        for item in items:
            self._debug_checkprint_action_item("reviewer", status, item)
            it = item.data(Qt.UserRole) or {}
            item_id = int(it["id"])
            changed_ids.append(item_id)
            update_checkprint_item_status(
                self.db_path,
                item_id=item_id,
                status=status,
                actor=actor,
                note=comment if comment else None,
                role="reviewer",
            )

        self._debug_checkprint_log(f"UI_ACTION_RELOAD role=reviewer requested_status={status!r}")
        self._load_items_for_reviewer()
        self._debug_checkprint_post_reload("reviewer", changed_ids)
        self._load_items_for_approver()

    def _reviewer_accept(self):
        self._reviewer_action("accepted")

    def _reviewer_accept_minor(self):
        self._reviewer_action("accepted_minor")

    def _reviewer_reject(self):
        self._reviewer_action("rejected")

    def _reviewer_pending(self):
        self._reviewer_action("pending")

    def _approver_action(self, status: str):
        items = self._approver_selected_items()
        self._debug_checkprint_action_start("approver", status, items)
        if not items:
            QMessageBox.information(self, "Approver", "Select one or more documents.")
            return

        actor = SettingsManager().get("user.name", "") or ""
        comment = ""
        if status in {"accepted_minor", "rejected"} and self.chk_app_always_comment.isChecked():
            comment = self.le_app_comment.text().strip() or "Approval PDF"

        changed_ids: list[int] = []
        for item in items:
            self._debug_checkprint_action_item("approver", status, item)
            it = item.data(Qt.UserRole) or {}
            item_id = int(it["id"])
            changed_ids.append(item_id)
            update_checkprint_item_status(
                self.db_path,
                item_id=item_id,
                status=status,
                actor=actor,
                note=comment if comment else None,
                role="approver",
            )

        self._debug_checkprint_log(f"UI_ACTION_RELOAD role=approver requested_status={status!r}")
        self.registerNeedsRefresh.emit()
        self._load_items_for_submitter()
        self._load_items_for_reviewer()
        self._load_items_for_approver()
        self._debug_checkprint_post_reload("approver", changed_ids)

    def _approver_reject(self):
        self._approver_action("rejected")

    def _approver_accept_minor(self):
        self._approver_action("accepted_minor")

    def _approver_approve(self):
        self._approver_action("approved")

    def _approver_pending(self):
        self._approver_action("pending")

    def _complete_checkprint(self):
        if not self.db_path or not self.current_batch_id:
            QMessageBox.information(self, "CheckPrint", "No active CheckPrint selected.")
            return

        actor = SettingsManager().get("user.name", "") or ""

        try:
            complete_and_archive_checkprint(
                self.db_path,
                batch_id=int(self.current_batch_id),
                actor=actor,
            )
        except Exception as e:
            QMessageBox.critical(self, "CheckPrint", f"Completion failed:\n{e}")
            return

        QMessageBox.information(
            self,
            "CheckPrint",
            "CheckPrint has been completed and archived.\n\n"
            "Source files have been updated with the approved versions.",
        )

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
            self.box_approver.setVisible(False)
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
        self.box_approver.setVisible(False)
        self._reload_batches()
# BEGIN CHECKPRINT REVISION MANAGEMENT UI PATCH V1
# Submitter revision context menu and revision-aware Resubmit All confirmation.


def _cp_revision_patch_format_service_error(result: dict) -> str:
    error = str(result.get("error") or "unknown_error")
    labels = {
        "batch_not_found": "The selected CheckPrint batch could not be found.",
        "batch_not_editable": "This CheckPrint batch is completed or cancelled and cannot be changed.",
        "items_not_found": "The selected CheckPrint document could not be found.",
        "source_file_missing": "The active source PDF could not be found.",
        "checkprint_file_missing": "The active CheckPrint PDF could not be found.",
        "revision_target_exists": "A file already exists with the requested revision filename.",
        "revision_target_collision": "Two revision rename operations resolve to the same target filename.",
        "preflight_failed": "A file rename was blocked during preflight.",
        "file_ops_failed": "A required file rename failed.",
        "database_update_failed": "The files were processed, but the database update failed.",
        "multiple_revision_candidates": "More than one incoming revision was found for the same CheckPrint document.",
    }
    message = labels.get(error, f"CheckPrint operation failed ({error}).")
    path = result.get("path")
    reason = result.get("reason")
    if path:
        message += f"\n\nFile:\n{path}"
    if reason:
        message += f"\n\nReason:\n{reason}"
    failures = result.get("rollback_failures") or []
    if failures:
        message += "\n\nManual file cleanup may be required:\n" + "\n".join(f"  - {x}" for x in failures[:8])
    return message


def _cp_revision_patch_change_revision(self, item: QListWidgetItem) -> None:
    if not item or not getattr(self, "db_path", None) or not self.current_batch_id:
        return
    data = item.data(Qt.UserRole) or {}
    item_id = int(data.get("id") or 0)
    doc_id = str(data.get("doc_id") or "").strip()
    old_revision = str(data.get("revision") or "").strip()
    if not item_id or not doc_id:
        QMessageBox.warning(self, "Change Revision", "The selected CheckPrint row is missing its document details.")
        return

    new_revision, accepted = QInputDialog.getText(
        self,
        "Change CheckPrint Revision",
        f"Document: {doc_id}\nCurrent revision: {old_revision or '-'}\n\nNew revision:",
        QLineEdit.Normal,
        old_revision,
    )
    if not accepted:
        return
    new_revision = str(new_revision or "").strip()
    if new_revision == old_revision:
        return

    confirm = QMessageBox.question(
        self,
        "Confirm Revision Change",
        "This will update the selected CheckPrint item and the document register.\n\n"
        f"Document: {doc_id}\n"
        f"Revision: {old_revision or '-'} -> {new_revision or '-'}\n\n"
        "The active source PDF and all related CheckPrint CP-version PDFs will be renamed. "
        "Continue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        return

    try:
        try:
            from ..services.checkprint_service import change_checkprint_item_revision
        except ImportError:
            from services.checkprint_service import change_checkprint_item_revision  # type: ignore
        result = change_checkprint_item_revision(
            Path(self.db_path),
            batch_id=int(self.current_batch_id),
            item_id=item_id,
            new_revision=new_revision,
            actor=SettingsManager().get("user.name", "") or "",
        )
    except Exception as exc:
        QMessageBox.critical(self, "Change Revision", f"Revision change failed:\n\n{exc}")
        return

    if not result.get("ok"):
        QMessageBox.critical(self, "Change Revision", _cp_revision_patch_format_service_error(result))
        return

    changed = result.get("changes") or []
    if not changed:
        return
    QMessageBox.information(
        self,
        "Revision Updated",
        f"{doc_id} was updated from revision {old_revision or '-'} to {new_revision}.\n\n"
        "The CheckPrint record, register revision and related PDF filenames were updated.",
    )
    self.registerNeedsRefresh.emit()
    self._load_items_for_submitter()
    self._load_items_for_reviewer()
    self._load_items_for_approver()


def _cp_revision_patch_show_comment_menu(
    self,
    lw: QListWidget,
    pos: QPoint,
    editable: bool,
    for_reviewer: bool,
    for_approver: bool = False,
):
    item = lw.itemAt(pos)
    if not item:
        return

    data = item.data(Qt.UserRole) or {}
    rel_cp = data.get("cp_path")
    cp_version = int(data.get("cp_version") or 1)
    menu = QMenu(self)

    if editable:
        act_comment = menu.addAction("View/Edit Comment...")
    else:
        act_comment = menu.addAction("View Comment...")

    submitter_lists = tuple(
        widget for widget in (
            getattr(self, "list_pending_sub", None),
            getattr(self, "list_rejected_sub", None),
            getattr(self, "list_accepted_minor_sub", None),
            getattr(self, "list_accepted_sub", None),
        ) if widget is not None
    )
    act_revision = None
    if lw in submitter_lists and getattr(self, "_active_role", None) == "submitter":
        batch = get_checkprint_batch(self.db_path, self.current_batch_id) if self.db_path and self.current_batch_id else None
        if batch and str(batch.get("status") or "").lower() not in {"completed", "cancelled"}:
            menu.addSeparator()
            act_revision = menu.addAction("Change Revision...")

    submenu = menu.addMenu("Show Previous Versions")
    try:
        project_root = self.db_path.parent.parent
        absolute_cp = project_root / rel_cp
        folder = absolute_cp.parent
        filename = absolute_cp.name
        stem = filename.rsplit("_CP_", 1)[0]
        extension = absolute_cp.suffix
        if cp_version > 1:
            for version in range(1, cp_version):
                action = submenu.addAction(f"Open CP_{version}")
                action.triggered.connect(
                    lambda _, base=stem, ver=version, ext=extension, fld=folder:
                    self._open_specific_cp_version(base, fld, ver, ext)
                )
        else:
            submenu.setEnabled(False)
    except Exception:
        submenu.setEnabled(False)

    chosen = menu.exec_(lw.mapToGlobal(pos))
    if act_revision is not None and chosen == act_revision:
        self._change_checkprint_revision(item)
        return
    if chosen == act_comment:
        self._open_comment_dialog(
            item,
            editable=editable,
            for_reviewer=for_reviewer,
            for_approver=for_approver,
        )


def _cp_revision_patch_resubmit_all_incoming(self) -> None:
    if not getattr(self, "db_path", None) or not self.current_batch_id:
        QMessageBox.information(self, "CheckPrint", "No active CheckPrint batch selected.")
        return

    actor = SettingsManager().get("user.name", "") or ""

    def _run(accepted_revision_changes=None):
        return resubmit_all_incoming(
            self.db_path,
            batch_id=int(self.current_batch_id),
            actor=actor,
            accepted_revision_changes=accepted_revision_changes,
        )

    try:
        result = _run()
    except Exception as exc:
        QMessageBox.critical(self, "Error", f"Resubmit failed:\n{exc}")
        return

    if result.get("error") == "revision_confirmation_required":
        accepted_changes = {}
        mismatches = result.get("revision_mismatches") or []
        for mismatch in mismatches:
            answer = QMessageBox.question(
                self,
                "Incoming Revision Changed",
                "An incoming PDF matches a CheckPrint document number but uses a different revision.\n\n"
                f"Document: {mismatch.get('doc_id')}\n"
                f"Current CheckPrint revision: {mismatch.get('current_revision') or '-'}\n"
                f"Incoming revision: {mismatch.get('detected_revision') or '-'}\n"
                f"Incoming file: {mismatch.get('file')}\n\n"
                "Accepting will rename the related source/CheckPrint PDFs and update both the "
                "CheckPrint item and document register before resubmitting.\n\n"
                "Accept this revision change?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No,
            )
            if answer == QMessageBox.Cancel:
                return
            if answer != QMessageBox.Yes:
                QMessageBox.information(
                    self,
                    "Resubmission Cancelled",
                    "No CheckPrint files or database records were changed.\n\n"
                    "The incoming PDF has been left in _CheckPrintIncoming so it can be corrected or removed.",
                )
                return
            accepted_changes[int(mismatch.get("item_id"))] = str(mismatch.get("detected_revision") or "")

        try:
            result = _run(accepted_changes)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Resubmit failed:\n{exc}")
            return

    if not result.get("ok"):
        if result.get("error") in {
            "source_file_missing", "checkprint_file_missing", "revision_target_exists",
            "revision_target_collision", "preflight_failed", "file_ops_failed",
            "database_update_failed", "multiple_revision_candidates", "batch_not_editable",
        }:
            QMessageBox.critical(self, "CheckPrint - Resubmit failed", _cp_revision_patch_format_service_error(result))
            return

        errors = dict(result.get("errors") or {})
        details = result.get("details") or []
        for detail in details:
            code = detail.get("error")
            if code == "no_match":
                errors.setdefault("unmatched", []).append(detail.get("file"))
            elif code == "bad_name":
                errors.setdefault("bad_format", []).append(detail.get("file"))
            elif code in {"duplicate_match", "duplicate_item_key_in_batch"}:
                errors.setdefault("duplicate", []).append(detail.get("file") or detail.get("doc_id"))

        incoming_dir = result.get("incoming_dir", "CheckPrint/_CheckPrintIncoming")
        register_examples = result.get("register_examples") or []
        message = "Resubmission failed.\n\n" f"Incoming folder:\n{incoming_dir}\n\n"

        def _sample(title, values, limit=5):
            values = [str(v) for v in (values or []) if v]
            if not values:
                return ""
            shown = "\n".join(f"  - {value}" for value in values[:limit])
            more = f"\n  ... and {len(values) - limit} more" if len(values) > limit else ""
            return f"{title} ({len(values)}):\n{shown}{more}\n"

        message += _sample("Files not matching any CheckPrint document", errors.get("unmatched"))
        message += _sample("Files with invalid naming (expected DOCID_REV.pdf)", errors.get("bad_format"))
        message += _sample("Duplicate submissions", errors.get("duplicate"))
        if register_examples:
            message += "Register expects filenames similar to:\n" + "\n".join(
                f"  - {value}" for value in register_examples[:5]
            ) + "\n"
        if not any(errors.values()) and result.get("reason"):
            message += f"Reason:\n{result.get('reason')}\n"
        message += "\nCorrect the incoming files and try again."
        QMessageBox.critical(self, "CheckPrint - Resubmit failed", message)
        return

    revision_changes = result.get("revision_changes") or []
    message = (
        "Resubmitted from _CheckPrintIncoming.\n\n"
        f"Updated: {result.get('updated', 0)}\n"
        f"Overwritten (pending): {result.get('overwritten', 0)}\n"
        f"Incremented (reviewed): {result.get('incremented', 0)}\n"
        f"Incoming cleaned: {result.get('deleted_incoming', 0)} file(s)"
    )
    if revision_changes:
        message += f"\nRevision changes accepted: {len(revision_changes)}"
        for change in revision_changes[:8]:
            message += (
                f"\n  - {change.get('doc_id')}: "
                f"{change.get('old_revision') or '-'} -> {change.get('new_revision') or '-'}"
            )
        if len(revision_changes) > 8:
            message += f"\n  ... and {len(revision_changes) - 8} more"
    warning = result.get("cleanup_warning")
    if warning:
        message += f"\n\nCleanup warning:\n{warning}"

    QMessageBox.information(self, "CheckPrint", message)
    self.registerNeedsRefresh.emit()
    self._load_items_for_submitter()
    self._load_items_for_reviewer()
    self._load_items_for_approver()


CheckPrintTab._change_checkprint_revision = _cp_revision_patch_change_revision
CheckPrintTab._show_comment_menu = _cp_revision_patch_show_comment_menu
CheckPrintTab._resubmit_all_incoming = _cp_revision_patch_resubmit_all_incoming

# END CHECKPRINT REVISION MANAGEMENT UI PATCH V1
