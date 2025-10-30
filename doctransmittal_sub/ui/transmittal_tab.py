from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QTableWidget, QTableWidgetItem, QMessageBox, QSizePolicy, QGroupBox
)

# Keep your existing DocumentRow type if present; fall back to dict usage
try:
    from ..models.document import DocumentRow
except Exception:
    DocumentRow = dict  # type: ignore

# Optional helper (used only for the dropdown in History tab, not here)
# from ..services.db import list_transmittals

# We still call your existing auto-match function if available
def _try_find_matches(pairs, roots):
    try:
        from ..services.autofind import find_docid_rev_matches  # your existing helper
        return find_docid_rev_matches(pairs, roots, extensions=None)
    except Exception:
        return {}

class TransmittalTab(QWidget):
    """
    Step 1 of the flow: confirm header details and selected documents.
    - Shows a mirror of the selected register rows (doc_id, type, file_type, status, latest_rev, description)
    - Lets the user pick a source folder and run 'Match Files to Doc IDs'
    - Emits signals to move back to Register or forward to Files
    """
    backRequested = pyqtSignal()
    proceedRequested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path: Optional[Path] = None
        self.project_root: Optional[Path] = None
        self.items: List[DocumentRow] = []
        self.file_mapping: Dict[str, str] = {}

        # ---------- Layout ----------
        root = QVBoxLayout(self)
        root.setSpacing(10)

        header = QLabel("<b>Please enter the details for the submission</b>", self)
        root.addWidget(header)

        # Header form
        form_box = QGroupBox("Submission Details", self)
        form = QFormLayout(form_box)
        self.le_user = QLineEdit(self);  self.le_user.setPlaceholderText("Prepared by")
        self.le_title = QLineEdit(self); self.le_title.setPlaceholderText("Transmittal title")
        self.le_client = QLineEdit(self); self.le_client.setPlaceholderText("Client / Recipient")
        form.addRow("Prepared by", self.le_user)
        form.addRow("Title", self.le_title)
        form.addRow("Client", self.le_client)
        root.addWidget(form_box)

        # Selection mirror
        tbl_box = QGroupBox("Selected documents (preview of register rows)", self)
        tbl_lay = QVBoxLayout(tbl_box)
        self.tbl = QTableWidget(0, 6, self)
        self.tbl.setHorizontalHeaderLabels(["Doc ID", "Type", "File Type", "Status", "Latest Rev", "Description"])
        self.tbl.setSelectionBehavior(self.tbl.SelectRows)
        self.tbl.setEditTriggers(self.tbl.NoEditTriggers)
        tbl_lay.addWidget(self.tbl)
        root.addWidget(tbl_box, 1)

        # Source + match row
        src_row = QHBoxLayout()
        self.le_source = QLineEdit(self); self.le_source.setPlaceholderText("Optional: Source folder for file matching")
        self.btn_browse = QPushButton("Browse…", self)
        self.btn_match = QPushButton("Match Files to Doc IDs", self)
        src_row.addWidget(self.le_source, 1)
        src_row.addWidget(self.btn_browse)
        src_row.addWidget(self.btn_match)
        root.addLayout(src_row)

        # Nav buttons
        nav = QHBoxLayout()
        self.btn_back = QPushButton("◀ Back to Register", self)
        nav.addWidget(self.btn_back)
        nav.addStretch(1)
        root.addLayout(nav)

        # Signals
        self.btn_browse.clicked.connect(self._pick_source)
        self.btn_match.clicked.connect(self._run_match_and_proceed)
        self.btn_back.clicked.connect(lambda: self.backRequested.emit())

    # ---------------- Public API (flow wiring) ----------------

    def set_db(self, db_path: Path):
        self.db_path = Path(db_path) if db_path else None

    def set_db_path(self, db_path: Path):
        self.set_db(db_path)

    def set_items(self, rows: List[DocumentRow]):
        self.items = list(rows or [])
        self._populate_table()

    def set_file_mapping(self, mapping: Dict[str, str]):
        self.file_mapping = dict(mapping or {})

    def set_selection(self, rows, db_path, user: str = ""):
        """Compatibility shim used by MainWindow._on_register_proceed(...) in your stacktrace."""
        self.set_db(db_path)
        self.set_items(rows or [])
        if user:
            self.le_user.setText(str(user))

    def set_flow_context(self, *, db_path: Path, project_root: Optional[Path], rows: List[DocumentRow], user: str = ""):
        self.set_db(db_path, project_root)
        self.set_items(rows or [])
        if user:
            self.le_user.setText(user)

    # ---------------- Internals ----------------

    def _populate_table(self):
        rows = self.items or []
        self.tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            def _get(k, default=""):
                if isinstance(row, dict):
                    return row.get(k, default)
                return getattr(row, k, default)

            doc_id = _get("doc_id")
            doc_type = _get("doc_type")
            file_type = _get("file_type")
            status = _get("status")
            latest_rev = _get("latest_rev_token") or _get("latest_rev") or _get("rev")
            description = _get("description")

            self.tbl.setItem(r, 0, QTableWidgetItem(str(doc_id or "")))
            self.tbl.setItem(r, 1, QTableWidgetItem(str(doc_type or "")))
            self.tbl.setItem(r, 2, QTableWidgetItem(str(file_type or "")))
            self.tbl.setItem(r, 3, QTableWidgetItem(str(status or "")))
            self.tbl.setItem(r, 4, QTableWidgetItem(str(latest_rev or "")))
            self.tbl.setItem(r, 5, QTableWidgetItem(str(description or "")))

        self.tbl.resizeColumnsToContents()

    def _pick_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Select source folder", "")
        if folder:
            self.le_source.setText(folder)

    def _payload_items(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for r in (self.items or []):
            get = (lambda k, default="": getattr(r, k, default)) if not isinstance(r, dict) else (lambda k, d="": r.get(k, d))
            doc_id     = get("doc_id", "")
            doc_type   = get("doc_type", "")
            file_type  = get("file_type", "")
            description= get("description", "")
            status     = get("status", "")
            revision   = get("latest_rev_token", "") or get("latest_rev", "") or get("rev", "")
            file_path  = self.file_mapping.get(doc_id, "")
            out.append({
                "doc_id": doc_id, "doc_type": doc_type, "file_type": file_type,
                "description": description, "status": status, "revision": revision,
                "file_path": file_path
            })
        return out

    def _emit_proceed(self):
        if not self.db_path:
            QMessageBox.information(self, "Project", "Open a project database first.")
            return
        payload = {
            "db_path": self.db_path,
            "items": self._payload_items(),
            "file_mapping": dict(self.file_mapping),
            "user": self.le_user.text().strip() or "User",
            "title": self.le_title.text().strip() or "Transmittal",
            "client": self.le_client.text().strip() or "Client",
            # NEW: pass the nominated source folder along so the Files tab can prime its file tree
            "source_root": self.le_source.text().strip() or "",
        }
        self.proceedRequested.emit(payload)

    def _run_match_and_proceed(self):
        folder = self.le_source.text().strip()
        if not folder:
            QMessageBox.information(self, "Match", "Pick a Source Folder first.")
            return
        pairs = []
        for r in (self.items or []):
            if isinstance(r, dict):
                did = r.get("doc_id", "")
                rev = r.get("latest_rev_token") or r.get("latest_rev") or r.get("rev") or ""
            else:
                did = getattr(r, "doc_id", "")
                rev = getattr(r, "latest_rev_token", "") or getattr(r, "latest_rev", "") or getattr(r, "rev", "")
            if did and rev:
                pairs.append((did, rev))

        mapping = _try_find_matches(pairs, [Path(folder)])
        # Update mapping
        for d, p in (mapping or {}).items():
            self.file_mapping[d] = str(p)

        # Advance
        self._emit_proceed()

    def reset(self):
        """Clear form + payload so a fresh transmittal can be created next time."""
        try:
            self.le_user.clear()
            self.le_title.clear()
            self.le_client.clear()
        except Exception:
            pass
        self.items = []
        self.file_mapping = {}
        # If you render a table of selected docs, clear it here too:
        try:
            self.tbl_selected.clearContents()
            self.tbl_selected.setRowCount(0)
        except Exception:
            pass