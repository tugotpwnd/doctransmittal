from __future__ import annotations
from typing import List, Dict, Optional, Tuple
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QDialogButtonBox, QMessageBox, QPushButton, QHBoxLayout, QLabel, QCheckBox
)
from PyQt5.QtCore import Qt
from ..services.templates_store import load_templates
import re
# replace the local mapping with this import
from .row_attributes_editor import DOC_TYPE_NAMES, DEFAULT_ROW_OPTIONS


_DOC_TYPE_NAMES = {
    "CAL": "Calculations","DOC": "General Document","DWG": "Drawing","ITC": "Inspection & Test Checklist",
    "ITP": "Inspection & Test Plan","MAN": "Manual","MDL": "Model","PGM": "Program","REG": "Register",
    "REP": "Report","RFI": "Request for Information","SCH": "Schedule","TRN": "Document Transmittal","VAR": "Variation",
}

def _parse_type_code(txt: str) -> str:
    t = (txt or "").strip().upper()
    return t.split("—", 1)[0].strip()

def _parse_area_code(txt: str) -> str:
    # accepts "00 — Pump Station 1" => "00"
    s = (txt or "").strip().upper()
    return s.split("—", 1)[0].strip()

def _next_suffix(existing_ids: List[str], prefix: str) -> str:
    """
    existing_ids: list of existing doc_ids (any doc)
    prefix: JOB-AREA-TYPE (no trailing -NNN)
    Return 'NNN' as zero-padded train number >= 001.
    """
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    max_n = 0
    for did in existing_ids:
        m = pat.match((did or "").strip().upper())
        if not m: continue
        try:
            n = int(m.group(1))
            if n > max_n: max_n = n
        except ValueError:
            pass
    return f"{max_n + 1:03d}"

class AddDocumentDialog(QDialog):
    """
    New document dialog with locked, auto-generated Doc ID:
        <JOB>-<AREA>-<TYPE>-NNN
    """
    def __init__(self, existing_doc_ids: List[str], row_options: Dict[str, List[str]],
                 project_code: str, areas: List[Tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Document")
        self._existing = set(x.strip().upper() for x in existing_doc_ids)
        self._project_code = (project_code or "").strip().upper()
        self._areas = areas or []

        # --- widgets ----------------------------------------------------------
        self.ed_id = QLineEdit(self)
        self.ed_id.setReadOnly(True)
        self.ed_id.setEnabled(False)
        self.ed_id.setPlaceholderText("Will be generated as <JOB>-<AREA>-<TYPE>-NNN")

        self.btn_toggle_id = QPushButton("Edit ID…", self)
        self.btn_toggle_id.setCheckable(True)
        self.btn_toggle_id.toggled.connect(self._toggle_id_edit)

        id_row = QHBoxLayout()
        id_row.addWidget(self.ed_id, 1)
        id_row.addWidget(self.btn_toggle_id)

        self.cb_area = QComboBox(self); self.cb_area.setEditable(False)
        for code, desc in self._areas:
            label = f"{code} — {desc}" if desc else code
            self.cb_area.addItem(label)
        self.cb_area.currentTextChanged.connect(self._regen_id)

        self.cb_type = QComboBox(self);
        self.cb_type.setEditable(True)
        for opt in (row_options.get("doc_types") or DEFAULT_ROW_OPTIONS["doc_types"]):
            label = f"{opt} — {DOC_TYPE_NAMES.get(opt, '')}".rstrip(" —")
            self.cb_type.addItem(label)

        self.cb_type.currentTextChanged.connect(self._regen_id)

        self.cb_file = QComboBox(self); self.cb_file.setEditable(True)
        for opt in (row_options.get("file_types") or []): self.cb_file.addItem(opt)

        self.cb_status = QComboBox(self); self.cb_status.setEditable(True)
        for opt in (row_options.get("statuses") or []): self.cb_status.addItem(opt)

        self.ed_desc = QLineEdit(self)

        btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow(QLabel("<b>Document Number</b>"))
        form.addRow(id_row)
        form.addRow("Area *", self.cb_area)
        form.addRow("Type", self.cb_type)
        form.addRow("File Type", self.cb_file)
        form.addRow("Status", self.cb_status)
        form.addRow("Description", self.ed_desc)
        # --- Templates (optional) ---
        self.chk_use_template = QCheckBox("Use template", self)
        self.cb_template = QComboBox(self)
        self.cb_template.setEnabled(False)

        # populate with descriptions only
        for t in (load_templates() or []):
            # show description; stash full record in userData
            self.cb_template.addItem(t.get("description", ""), t)

        self.chk_use_template.toggled.connect(self.cb_template.setEnabled)

        form.addRow(self.chk_use_template)
        form.addRow("Template", self.cb_template)


        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

        self.payload: Optional[Dict[str, str]] = None
        self._regen_id()

    def _toggle_id_edit(self, checked: bool):
        self.ed_id.setEnabled(checked)
        self.ed_id.setReadOnly(not checked)
        self.btn_toggle_id.setText("Lock ID" if checked else "Edit ID…")
        if not checked:
            self._regen_id()

    def _regen_id(self):
        area = _parse_area_code(self.cb_area.currentText())
        tcode = _parse_type_code(self.cb_type.currentText())
        pieces = [p for p in [self._project_code, area, tcode] if p]
        prefix = "-".join(pieces)
        suffix = _next_suffix(list(self._existing), prefix) if prefix else "001"
        self._current_prefix = prefix
        self.ed_id.setText(f"{prefix}-{suffix}" if prefix else "")

    def _ok(self):
        did = (self.ed_id.text() or "").strip().upper()
        if not self._project_code:
            QMessageBox.information(self, "Required", "This project has no Job Number / Project Code."); return
        if self.cb_area.count() == 0:
            QMessageBox.information(self, "Required", "Define at least one Area in Manage Areas."); return
        if not did:
            QMessageBox.information(self, "Required", "Document Number could not be generated."); return
        if did in self._existing:
            QMessageBox.information(self, "Exists", f"'{did}' already exists."); return

        doc_type = _parse_type_code(self.cb_type.currentText())
        self.payload = {
            "doc_id": did,
            "description": self.ed_desc.text().strip(),
            "doc_type": doc_type,
            "file_type": self.cb_file.currentText().strip(),
            "status": self.cb_status.currentText().strip(),
            "is_active": 1,
        }
        # Optional template selection
        # Optional template selection
        if self.chk_use_template.isChecked() and self.cb_template.currentIndex() >= 0:
            tpl = self.cb_template.currentData()
            if isinstance(tpl, dict):
                # NB: our templates_store.load_templates() provides 'abs_path' and 'relpath'
                self.payload["use_template"]          = True
                self.payload["template_category"]     = tpl.get("category", "document")
                self.payload["template_kind"]         = tpl.get("kind", "excel")
                self.payload["template_doc_id"]       = tpl.get("doc_id", "")
                self.payload["template_revision"]     = tpl.get("revision", "")
                self.payload["template_description"]  = tpl.get("description", "")
                self.payload["template_relpath"]      = tpl.get("relpath", "")
                self.payload["template_abspath"]      = tpl.get("abs_path", "")
                # For backwards-compat, also set template_path to the absolute path
                self.payload["template_path"]         = tpl.get("abs_path", "")

        # DEBUG: see exactly what will be sent to the Register tab
        try:
            print("[AddDocumentDialog] payload ->", self.payload, flush=True)
        except Exception:
            pass

        self.accept()
