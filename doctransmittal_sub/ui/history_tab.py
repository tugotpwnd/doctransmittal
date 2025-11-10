from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional
import traceback

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QTableWidget, QTableWidgetItem, QPushButton, QMessageBox, QLabel,
    QComboBox, QLineEdit
)

from ..services.transmittal_service import (
    rebuild_receipt_only,           # <-- add
    rebuild_transmittal_bundle,     # keep for other flows if needed
    edit_transmittal_add_items,
    edit_transmittal_remove_items,
    edit_transmittal_update_header,
    soft_delete_transmittal_bundle,
    purge_transmittal_bundle,
)
# ---- DB / services (robust imports) -----------------------------------------
try:
    from ..services.db import (
        init_db, get_project,
        list_transmittals, get_transmittal_items,
        list_documents_with_latest,          # <-- use the same API as RegisterTab
    )
except Exception:
    # fallback if package layout differs
    from ..services.db import init_db, get_project, list_transmittals, get_transmittal_items  # type: ignore
    list_documents_with_latest = None  # type: ignore

try:
    from ..services.transmittal_service import (
        rebuild_transmittal_bundle,
        edit_transmittal_add_items,
        edit_transmittal_remove_items,
        edit_transmittal_update_header,
        soft_delete_transmittal_bundle,
        purge_transmittal_bundle,
    )
except Exception:
    from ..transmittal_service import (  # type: ignore
        rebuild_transmittal_bundle,
        edit_transmittal_add_items,
        edit_transmittal_remove_items,
        edit_transmittal_update_header,
        soft_delete_transmittal_bundle,
        purge_transmittal_bundle,
    )


class HistoryTab(QWidget):
    """
    Transmittal editor:
      • Top: transmittal picker
      • Split panes:
          - Left: Register (available)  -> all register docs MINUS current transmittal docs
          - Right: Transmittal items    -> current snapshot (with file_path)
      • Actions: Add →, ← Remove, Attempt Remap…, Save & Rebuild, Soft Delete, Purge
    """
    # MainWindow listens to this and pushes user into FilesTab in "edit" mode
    remapRequested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path: Optional[Path] = None
        self.project_id: Optional[int] = None
        self._headers: List[dict] = []
        self._current_header: Optional[dict] = None
        self._items_right: List[dict] = []   # items in transmittal
        self._items_left: List[dict] = []    # register - right

        root = QVBoxLayout(self)

        # --- Top: transmittal selector ---
        top = QHBoxLayout()
        self.cb_trans = QComboBox(self)
        self.cb_trans.currentIndexChanged.connect(self._on_transmittal_changed)
        self.btn_reload = QPushButton("Reload", self); self.btn_reload.clicked.connect(self.refresh)
        self.lbl_sel = QLabel("No transmittal selected", self)

        top.addWidget(QLabel("Transmittal:", self))
        top.addWidget(self.cb_trans, 1)
        top.addWidget(self.btn_reload)
        top.addWidget(self.lbl_sel, 1, alignment=Qt.AlignRight)

        top.addWidget(QLabel("Submission date:", self))
        self.le_date = QLineEdit(self); self.le_date.setPlaceholderText("DD/MM/YYYY or DD/MM/YYYY HH:MM"); self.le_date.setFixedWidth(200)
        top.addWidget(self.le_date)

        top.addWidget(QLabel("Title:", self))
        self.le_title = QLineEdit(self); self.le_title.setPlaceholderText("Transmittal title"); self.le_title.setFixedWidth(260)
        top.addWidget(self.le_title)

        top.addWidget(QLabel("Who by:", self))
        self.le_by = QLineEdit(self); self.le_by.setPlaceholderText("Created by"); self.le_by.setFixedWidth(160)
        top.addWidget(self.le_by)

        top.addWidget(QLabel("To:", self))
        self.le_to = QLineEdit(self); self.le_to.setPlaceholderText("Recipient (Client Reference)")
        self.le_to.setFixedWidth(220)
        top.addWidget(self.le_to)

        self.btn_save_hdr = QPushButton("Save Header", self)
        self.btn_save_hdr.clicked.connect(self._save_header_edits)
        top.addWidget(self.btn_save_hdr)

        root.addLayout(top)
        # --- Split panes ---
        split = QSplitter(self); split.setOrientation(Qt.Horizontal)
        root.addWidget(split, 1)

        # LEFT: Register (available)
        gb_left = QGroupBox("Register (available)", self)
        left_l = QVBoxLayout(gb_left)
        self.tbl_left = QTableWidget(0, 6, gb_left)
        self.tbl_left.setHorizontalHeaderLabels(
            ["Doc ID", "Revision", "Type", "Status", "Description", "File Type"]
        )
        self.tbl_left.setSelectionBehavior(self.tbl_left.SelectRows)
        left_l.addWidget(self.tbl_left, 1)

        add_row = QHBoxLayout()
        self.btn_add = QPushButton("Add →", gb_left); self.btn_add.clicked.connect(self._add_selected)
        add_row.addStretch(1); add_row.addWidget(self.btn_add)
        left_l.addLayout(add_row)
        split.addWidget(gb_left)

        # RIGHT: Transmittal items
        gb_right = QGroupBox("Transmittal items", self)
        right_l = QVBoxLayout(gb_right)
        self.tbl_right = QTableWidget(0, 7, gb_right)
        self.tbl_right.setHorizontalHeaderLabels(
            ["Doc ID", "Revision", "Type", "Status", "Description", "File Type", "File Path"]
        )
        self.tbl_right.setSelectionBehavior(self.tbl_right.SelectRows)
        right_l.addWidget(self.tbl_right, 1)

        btns = QHBoxLayout()
        self.btn_remove = QPushButton("← Remove", gb_right); self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_remap = QPushButton("Remap Files…", gb_right); self.btn_remap.clicked.connect(self._request_remap)
        self.btn_save = QPushButton("Reprint Receipt…", gb_right); self.btn_save.clicked.connect(self._save_and_rebuild)
        btns.addWidget(self.btn_remove); btns.addStretch(1); btns.addWidget(self.btn_remap); btns.addWidget(self.btn_save)
        right_l.addLayout(btns)

        danger = QHBoxLayout()
        self.btn_softdel = QPushButton("Soft Delete", gb_right); self.btn_softdel.clicked.connect(self._soft_delete_current)
        self.btn_purge = QPushButton("Purge Permanently", gb_right); self.btn_purge.clicked.connect(self._purge_current)
        danger.addWidget(self.btn_softdel); danger.addWidget(self.btn_purge); danger.addStretch(1)
        right_l.addLayout(danger)

        split.addWidget(gb_right)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2)

        # UX
        self.tbl_left.itemDoubleClicked.connect(lambda _: self._add_selected())
        self.tbl_right.itemDoubleClicked.connect(lambda _: self._remove_selected())

    # -------- Public API --------
    def set_db_path(self, db_path: Path):
        self.db_path = Path(db_path) if db_path else None
        self.refresh()

    def set_db(self, db_path: Path):
        self.set_db_path(db_path)

    # -------- Refresh pipeline --------
    def refresh(self):
        if not self.db_path:
            return
        try:
            init_db(self.db_path)
            proj = get_project(self.db_path)
            self.project_id = (proj or {}).get("id", None)
            self._headers = list_transmittals(self.db_path, include_deleted=False) or []

            self.cb_trans.blockSignals(True)
            self.cb_trans.clear()
            for h in self._headers:
                self.cb_trans.addItem(f"{h.get('number','')} — {h.get('title','')}", userData=h)
            self.cb_trans.blockSignals(False)

            if self._headers:
                self.cb_trans.setCurrentIndex(0)
                self._on_transmittal_changed(0)
            else:
                self._current_header = None
                self.lbl_sel.setText("No transmittal selected")
                self._items_right, self._items_left = [], []
                self._render_tables()

        except Exception:
            print("[HistoryTab] refresh error:\n" + traceback.format_exc(), flush=True)

    def _on_transmittal_changed(self, idx: int):
        if idx < 0 or not self.db_path:
            self._current_header = None
            self.lbl_sel.setText("No transmittal selected")
            self._items_right, self._items_left = [], []
            if hasattr(self, "le_date"):  self.le_date.setText("")
            if hasattr(self, "le_title"): self.le_title.setText("")
            if hasattr(self, "le_by"):    self.le_by.setText("")
            if hasattr(self, "le_to"):    self.le_to.setText("")
            self._render_tables()
            return

        self._current_header = self.cb_trans.itemData(idx)
        number = (self._current_header or {}).get("number", "—")
        self.lbl_sel.setText(f"Transmittal: {number}")

        # Prefill date
        try:
            co = (self._current_header or {}).get("created_on", "") or ""
            if len(co) >= 10 and co[4:5] == "-" and co[7:8] == "-":
                from datetime import datetime as _dt
                fmt_in = "%Y-%m-%d %H:%M" if ":" in co else "%Y-%m-%d"
                dt = _dt.strptime(co, fmt_in)
                co = dt.strftime("%d/%m/%Y %H:%M") if ":" in co else dt.strftime("%d/%m/%Y")
            if hasattr(self, "le_date"):
                self.le_date.setText(co)
            # Prefill title / who by
            try:
                if hasattr(self, "le_date"):  self.le_date.setText(co)
                if hasattr(self, "le_title"): self.le_title.setText((self._current_header or {}).get("title", "") or "")
                if hasattr(self, "le_by"):    self.le_by.setText(
                    (self._current_header or {}).get("created_by", "") or "")
                if hasattr(self, "le_to"):
                    # store recipient in transmittals.client; fallback to project client_reference
                    to_val = (self._current_header or {}).get("client", "") or ""
                    if not to_val:
                        try:
                            from ..services.db import get_project
                            proj = get_project(self.db_path) or {}
                            to_val = proj.get("client_reference", "") or ""
                        except Exception:
                            pass
                    self.le_to.setText(to_val)
            except Exception:
                pass
        except Exception:
            pass

        # RIGHT: snapshot from DB
        try:
            tid = (self._current_header or {}).get("id", None)
            self._items_right = get_transmittal_items(self.db_path, tid) if tid is not None else []
        except Exception:
            self._items_right = []

        # LEFT: list_documents_with_latest(db, pid, state='active') MINUS right
        self._items_left = self._load_register_minus_right()
        self._render_tables()

    def _load_register_minus_right(self) -> List[dict]:
        if not (self.db_path and self.project_id and list_documents_with_latest):
            return []
        try:
            rows = list_documents_with_latest(self.db_path, self.project_id, state="active") or []
            right_ids = {(it.get("doc_id") or "").strip() for it in self._items_right}
            out = []
            for r in rows:
                did = (r.get("doc_id") or "").strip()
                if did and did not in right_ids:
                    out.append(r)
            return out
        except Exception:
            print("[HistoryTab] load register error:\n" + traceback.format_exc(), flush=True)
            return []

    # -------- Render --------
    def _render_tables(self):
        # LEFT
        self.tbl_left.setRowCount(len(self._items_left))
        for r, it in enumerate(self._items_left):
            self.tbl_left.setItem(r, 0, QTableWidgetItem(it.get("doc_id", "")))
            self.tbl_left.setItem(r, 1, QTableWidgetItem(it.get("latest_rev") or it.get("revision", "")))
            self.tbl_left.setItem(r, 2, QTableWidgetItem(it.get("doc_type", "")))
            self.tbl_left.setItem(r, 3, QTableWidgetItem(it.get("status", "")))
            self.tbl_left.setItem(r, 4, QTableWidgetItem(it.get("description", "")))
            self.tbl_left.setItem(r, 5, QTableWidgetItem(it.get("file_type", "")))
        self.tbl_left.resizeColumnsToContents()

        # RIGHT
        self.tbl_right.setRowCount(len(self._items_right))
        for r, it in enumerate(self._items_right):
            self.tbl_right.setItem(r, 0, QTableWidgetItem(it.get("doc_id", "")))
            self.tbl_right.setItem(r, 1, QTableWidgetItem(it.get("revision", "")))
            self.tbl_right.setItem(r, 2, QTableWidgetItem(it.get("doc_type", "")))
            self.tbl_right.setItem(r, 3, QTableWidgetItem(it.get("status", "")))
            self.tbl_right.setItem(r, 4, QTableWidgetItem(it.get("description", "")))
            self.tbl_right.setItem(r, 5, QTableWidgetItem(it.get("file_type", "")))
            self.tbl_right.setItem(r, 6, QTableWidgetItem(it.get("file_path", "")))
        self.tbl_right.resizeColumnsToContents()

    # -------- Selection helpers --------
    def _selected_doc_ids(self, table: QTableWidget) -> List[str]:
        out: List[str] = []
        for idx in table.selectionModel().selectedRows():
            item = table.item(idx.row(), 0)
            if item:
                did = (item.text() or "").strip()
                if did:
                    out.append(did)
        # dedupe, preserve order
        seen, uniq = set(), []
        for d in out:
            if d not in seen:
                seen.add(d); uniq.append(d)
        return uniq

    # -------- Add / Remove --------
    def _add_selected(self):
        if not (self.db_path and self._current_header):
            return
        dids = self._selected_doc_ids(self.tbl_left)
        if not dids:
            QMessageBox.information(self, "Select", "Select one or more rows in the left pane."); return
        number = self._current_header.get("number", "")
        try:
            items = [{"doc_id": d} for d in dids]
            edit_transmittal_add_items(self.db_path, number, items)
            self._on_transmittal_changed(self.cb_trans.currentIndex())
        except Exception as e:
            QMessageBox.warning(self, "Add failed", str(e))

    def _remove_selected(self):
        if not (self.db_path and self._current_header):
            return
        dids = self._selected_doc_ids(self.tbl_right)
        if not dids:
            QMessageBox.information(self, "Select", "Select one or more rows in the right pane."); return
        number = self._current_header.get("number", "")
        try:
            edit_transmittal_remove_items(self.db_path, number, dids)
            self._on_transmittal_changed(self.cb_trans.currentIndex())
        except Exception as e:
            QMessageBox.warning(self, "Remove failed", str(e))

    # -------- Save & Rebuild (clears Files/ and regenerates PDF) -------------
    def _save_and_rebuild(self):
        if not (self.db_path and self._current_header):
            return
        number = self._current_header.get("number", "")
        if not number:
            return
        try:
            # reprint the receipt PDF only
            trans_dir = rebuild_receipt_only(self.db_path, number)
            QMessageBox.information(self, "Receipt reprinted",
                                    f"Reprinted receipt for {number}.\n\n{trans_dir}")
        except Exception as e:
            QMessageBox.warning(self, "Reprint failed", str(e))

    # -------- Remap handoff (to FilesTab) -----------------------------------
    def _request_remap(self):
        if not (self.db_path and self._current_header):
            return
        number = self._current_header.get("number", "")
        if not number:
            return

        # Build payload for FilesTab edit mode
        items = [dict(it) for it in self._items_right]  # carry file_path + metadata
        file_mapping: Dict[str, str] = {}
        for it in items:
            did = (it.get("doc_id") or "").strip()
            fp = (it.get("file_path") or "").strip()
            if did and fp:
                file_mapping[did] = fp

        payload = {
            "mode": "edit",
            "transmittal_number": number,
            "db_path": self.db_path,
            "items": items,
            "file_mapping": file_mapping,
            "user": (self._current_header or {}).get("created_by", ""),
            "title": (self._current_header or {}).get("title", ""),
            "client": (self._current_header or {}).get("client", ""),
            "created_on": (self._current_header or {}).get("created_on", ""),
        }
        self.remapRequested.emit(payload)

    # -------- Save  -------------------------------------------------
    def _save_header_edits(self):
        if not (self.db_path and self._current_header):
            return
        number = (self._current_header or {}).get("number", "")
        if not number:
            return
        try:
            # Save date only (extend to title/client later if you want)
            co_text = (self.le_date.text().strip() if hasattr(self, "le_date") else "")
            title_txt = (self.le_title.text().strip() if hasattr(self, "le_title") else "")
            by_text = (self.le_by.text().strip() if hasattr(self, "le_by") else "")
            to_text = (self.le_to.text().strip() if hasattr(self, "le_to") else "")

            edit_transmittal_update_header(
                self.db_path, number,
                created_on_str=co_text or None,
                title=title_txt or None,
                created_by=by_text or None,
                client=to_text or None  # store “To” in transmittals.client
            )
            self.refresh()
            QMessageBox.information(self, "Saved", "Header updated.")
        except Exception as e:
            QMessageBox.warning(self, "Update failed", str(e))

    # -------- Delete / Purge -------------------------------------------------
    def _soft_delete_current(self):
        if not (self.db_path and self._current_header): return
        number = self._current_header.get("number", "")
        if not number: return
        if QMessageBox.question(self, "Soft Delete",
                                f"Mark {number} as deleted (DB only, files untouched)?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        if soft_delete_transmittal_bundle(self.db_path, number, reason="User soft delete"):
            self.refresh()

    def _purge_current(self):
        if not (self.db_path and self._current_header): return
        number = self._current_header.get("number", "")
        if not number: return
        if QMessageBox.question(self, "Purge Permanently",
                                f"Permanently remove {number} (DB + folder)?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        ok = purge_transmittal_bundle(self.db_path, number)
        if ok:
            self.refresh()
        else:
            QMessageBox.warning(self, "Purge failed",
                                f"Could not fully remove on-disk folder for {number}.\n"
                                "Close any programs using files in that folder and try again.")
