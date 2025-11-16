from __future__ import annotations
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
        self.box_submitter.setVisible(True)
        self._load_items_for_submitter()

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

        # Apply rules:
        #   rejected → pending + CP increment
        #   accepted → pending + CP increment
        #   pending  → pending + NO increment
        # resubmit_checkprint_items ALWAYS increments cp_version,
        # so we handle "no increment" case manually.
        try:
            if status == "pending":
                # overwrite current CP version, don't bump cp_version
                self._overwrite_without_increment(it, Path(fp))
            else:
                # use service (increments cp_version)
                resubmit_checkprint_items(
                    self.db_path,
                    batch_id=self.current_batch_id,
                    item_id_to_new_path={it["id"]: Path(fp)},
                    submitter="submitter"
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Resubmit failed:\n{e}")
            return

        QMessageBox.information(self, "CheckPrint", f"{doc_id} resubmitted.")
        self._load_items_for_submitter()

    # -------------------------------------------------------------------------
    # Manual overwrite without CP increment
    # -------------------------------------------------------------------------
    def _overwrite_without_increment(self, it, new_file: Path):
        """
        Pending → pending, but CP version does NOT increment.
        Replace the existing CP file with the uploaded one.

        Required:
            • keep doc_id
            • include revision in filename (if present)
            • keep CP_N the same
            • delete old CP file
            • copy new file into its place
        """
        import shutil
        from ..services.checkprint_service import _safe_rename, _split_basename
        from ..services.db import _connect, _retry_write

        doc_id       = it["doc_id"]
        revision     = it.get("revision") or ""
        cp_version   = it["cp_version"]
        old_cp_path  = Path(it["cp_path"])
        cp_dir       = old_cp_path.parent
        ext          = new_file.suffix

        # Build correct target filename
        if revision:
            final_name = f"{doc_id}_{revision}_CP_{cp_version}{ext}"
        else:
            final_name = f"{doc_id}_CP_{cp_version}{ext}"

        new_cp_path = cp_dir / final_name

        # Remove previous CP file
        print(old_cp_path)
        if old_cp_path.exists():
            try:
                old_cp_path.unlink()
            except Exception as e:
                raise RuntimeError(f"Failed to delete old CP file:\n{old_cp_path}\n{e}")
            
        else:
            print(f"old cp path: {old_cp_path} does not exist")

        # Copy uploaded file to the correct CP filename
        try:
            shutil.copy2(str(new_file), str(new_cp_path))
        except Exception as e:
            raise RuntimeError(f"Failed to copy new file into CheckPrint directory:\n{e}")

        # Update DB paths + status
        def _do():
            con = _connect(self.db_path); cur = con.cursor()
            cur.execute("""
                UPDATE checkprint_items
                   SET status='pending',
                       reviewer=NULL,
                       last_reviewer_note=NULL,
                       source_path=?,
                       cp_path=?,
                       last_submitted_on=datetime('now')
                 WHERE id=?
            """, (str(new_cp_path), str(new_cp_path), it["id"]))
            con.commit(); con.close()
        _retry_write(_do)
