from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import re

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox,
    QMessageBox, QPushButton, QHBoxLayout, QLabel, QCheckBox, QSpinBox, QToolButton
)

from ..services.templates_store import load_templates
from .row_attributes_editor import DOC_TYPE_NAMES, DEFAULT_ROW_OPTIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_type_code(txt: str) -> str:
    t = (txt or "").strip().upper()
    return t.split("—", 1)[0].strip()

def _parse_area_code(txt: str) -> str:
    s = (txt or "").strip().upper()
    return s.split("—", 1)[0].strip()

def _next_suffix(existing_ids: List[str], prefix: str) -> str:
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    max_n = 0
    for did in existing_ids:
        m = pat.match((did or "").strip().upper())
        if not m:
            continue
        try:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
        except ValueError:
            pass
    return f"{max_n + 1:03d}"

def _scan_next_n_standard(existing_ids_upper: set, prefix: str, count: int) -> List[str]:
    start = int(_next_suffix(list(existing_ids_upper), prefix))
    width = len(_next_suffix(list(existing_ids_upper), prefix))
    width = max(width, 3)
    ids: List[str] = []
    n = start
    while len(ids) < count:
        candidate = f"{prefix}-{str(n).zfill(width)}".upper()
        if candidate not in existing_ids_upper:
            ids.append(candidate)
        n += 1
    return ids

_PLACEHOLDER_RE = re.compile(r"\{([Xx]+)\}")

def _expand_custom_pattern(pattern: str, start: int, count: int) -> List[str]:
    if not pattern or "{" not in pattern or "}" not in pattern:
        raise ValueError("Pattern must include a placeholder like {XX} or {XXXXX}.")
    matches = list(_PLACEHOLDER_RE.finditer(pattern))
    if len(matches) != 1:
        raise ValueError("Pattern must contain exactly one placeholder with only X characters, e.g. {XXX}.")
    m = matches[0]
    width = len(m.group(1))
    if width < 1:
        raise ValueError("Placeholder must be at least one X, e.g. {X}.")
    pre = pattern[:m.start()]
    post = pattern[m.end():]
    out: List[str] = []
    for i in range(count):
        num = start + i
        out.append(f"{pre}{str(num).zfill(width)}{post}".upper())
    return out


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class AddDocumentDialog(QDialog):
    """
    New document dialog with locked, auto-generated Doc ID (single or batch).
    """
    def __init__(self, existing_doc_ids: List[str], row_options: Dict[str, List[str]],
                 project_code: str, areas: List[Tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Document")
        self._existing = set(x.strip().upper() for x in (existing_doc_ids or []))
        self._project_code = (project_code or "").strip().upper()
        self._areas = areas or []

        # -------------------------------------------------------------------
        # Top: ID row (preview + "Edit ID…")
        # -------------------------------------------------------------------
        self.ed_id = QLineEdit(self)
        self.ed_id.setReadOnly(True)
        self.ed_id.setEnabled(False)  # default: grey until type/area chosen regenerates
        self.ed_id.setPlaceholderText("Will be generated as <JOB>-<AREA>-<TYPE>-NNN")

        self.btn_toggle_id = QPushButton("Edit ID…", self)
        self.btn_toggle_id.setCheckable(True)
        self.btn_toggle_id.toggled.connect(self._toggle_id_edit)

        id_row = QHBoxLayout()
        id_row.addWidget(self.ed_id, 1)
        id_row.addWidget(self.btn_toggle_id)

        # -------------------------------------------------------------------
        # Core fields
        # -------------------------------------------------------------------
        self.cb_area = QComboBox(self)
        self.cb_area.setEditable(False)
        for code, desc in self._areas:
            label = f"{code} — {desc}" if desc else code
            self.cb_area.addItem(label)
        self.cb_area.currentTextChanged.connect(self._regen_id)

        self.cb_type = QComboBox(self)
        self.cb_type.setEditable(True)
        for opt in (row_options.get("doc_types") or DEFAULT_ROW_OPTIONS["doc_types"]):
            label = f"{opt} — {DOC_TYPE_NAMES.get(opt, '')}".rstrip(" —")
            self.cb_type.addItem(label)
        self.cb_type.currentTextChanged.connect(self._regen_id)

        self.cb_file = QComboBox(self)
        self.cb_file.setEditable(True)
        for opt in (row_options.get("file_types") or []):
            self.cb_file.addItem(opt)

        self.cb_status = QComboBox(self)
        self.cb_status.setEditable(True)
        for opt in (row_options.get("statuses") or []):
            self.cb_status.addItem(opt)

        self.ed_desc = QLineEdit(self)

        # -------------------------------------------------------------------
        # Batch controls
        # -------------------------------------------------------------------
        self.chk_batch = QCheckBox("Create batch", self)
        self.chk_batch.setToolTip("Create many sequential document IDs in one go.")
        self.chk_batch.toggled.connect(self._on_batch_toggled)

        self.spin_batch_count = QSpinBox(self)
        self.spin_batch_count.setRange(1, 9999)
        self.spin_batch_count.setValue(5)
        self.spin_batch_count.setEnabled(False)
        self.spin_batch_count.setToolTip("How many documents to create in the train. Enable 'Create batch' first.")

        self.chk_custom_pattern = QCheckBox("Use custom ID pattern (with {XXX})", self)
        self.chk_custom_pattern.setEnabled(False)
        self.chk_custom_pattern.toggled.connect(self._on_custom_pattern_toggled)
        self.chk_custom_pattern.setToolTip(
            "Provide a full ID pattern like 'DWG-PMP-{XXX}'. Number of X's = zero-pad width. "
            "Enable 'Create batch' first."
        )

        self.ed_custom_pattern = QLineEdit(self)
        self.ed_custom_pattern.setEnabled(False)
        self.ed_custom_pattern.setPlaceholderText("e.g. DWG-PUMP-{XXX}-A")
        self.ed_custom_pattern.setToolTip("Disabled until 'Use custom ID pattern' is ticked.")

        self.spin_custom_start = QSpinBox(self)
        self.spin_custom_start.setRange(0, 999999)
        self.spin_custom_start.setValue(1)
        self.spin_custom_start.setEnabled(False)
        self.spin_custom_start.setToolTip("Disabled until 'Use custom ID pattern' is ticked.")

        self.btn_train_help = QToolButton(self)
        self.btn_train_help.setText("?")
        self.btn_train_help.setToolTip("How trains work")
        self.btn_train_help.clicked.connect(self._show_train_help)

        batch_row_1 = QHBoxLayout()
        batch_row_1.addWidget(QLabel("Count:", self))
        batch_row_1.addWidget(self.spin_batch_count, 0)
        batch_row_1.addSpacing(12)
        batch_row_1.addWidget(self.chk_custom_pattern, 1)
        batch_row_1.addWidget(self.btn_train_help, 0, Qt.AlignRight)

        batch_row_2 = QHBoxLayout()
        batch_row_2.addWidget(QLabel("Custom pattern:", self))
        batch_row_2.addWidget(self.ed_custom_pattern, 1)

        batch_row_3 = QHBoxLayout()
        batch_row_3.addWidget(QLabel("Start number:", self))
        batch_row_3.addWidget(self.spin_custom_start, 0)
        batch_row_3.addStretch(1)

        # -------------------------------------------------------------------
        # Templates (optional) — disabled until checked; disabled in batch mode
        # -------------------------------------------------------------------
        self.chk_use_template = QCheckBox("Use template", self)
        self.chk_use_template.setToolTip("Tick to select and apply a template (single add only).")
        self.chk_use_template.toggled.connect(self._on_use_template_toggled)

        self.cb_template = QComboBox(self)
        self.cb_template.setEnabled(False)
        self.cb_template.setToolTip("Disabled until 'Use template' is ticked.")
        for t in (load_templates() or []):
            self.cb_template.addItem(t.get("description", ""), t)

        # -------------------------------------------------------------------
        # Buttons
        # -------------------------------------------------------------------
        btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)

        # -------------------------------------------------------------------
        # Layout
        # -------------------------------------------------------------------
        form = QFormLayout()
        form.addRow(QLabel("<b>Document Number</b>"))
        form.addRow(id_row)
        form.addRow("Area *", self.cb_area)
        form.addRow("Type", self.cb_type)
        form.addRow("File Type", self.cb_file)
        form.addRow("Status", self.cb_status)
        form.addRow("Description", self.ed_desc)

        # Batch block (above template)
        form.addRow(self.chk_batch)
        form.addRow(batch_row_1)
        form.addRow(batch_row_2)
        form.addRow(batch_row_3)

        # Template block
        form.addRow(self.chk_use_template)
        form.addRow("Template", self.cb_template)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

        # Outputs
        self.payload: Optional[Dict[str, str]] = None
        self.payloads: Optional[List[Dict[str, str]]] = None

        # Initial preview
        self._regen_id()
        # Enable Doc ID view (but still read-only) for clarity once we have a prefix
        self.ed_id.setEnabled(True)

    # -----------------------------------------------------------------------
    # UI wiring
    # -----------------------------------------------------------------------
    def _toggle_id_edit(self, checked: bool):
        # Disable manual editing when batch mode is active
        if self.chk_batch.isChecked():
            # Force off and keep disabled in batch
            self.btn_toggle_id.blockSignals(True)
            self.btn_toggle_id.setChecked(False)
            self.btn_toggle_id.blockSignals(False)
            self.ed_id.setEnabled(False)
            self.ed_id.setReadOnly(True)
            self.btn_toggle_id.setEnabled(False)
            self.btn_toggle_id.setToolTip("Disabled while creating a batch.")
            return

        self.ed_id.setEnabled(True)
        self.ed_id.setReadOnly(not checked)
        self.btn_toggle_id.setText("Lock ID" if checked else "Edit ID…")
        self.btn_toggle_id.setToolTip("" if checked else "Enable to manually edit the ID for a single add.")

        if checked:
            self.ed_id.setPlaceholderText("Custom ID (auto-update paused)")
        else:
            self.ed_id.setPlaceholderText("Will be generated as <JOB>-<AREA>-<TYPE>-NNN")
            self._regen_id()

    def _on_batch_toggled(self, checked: bool):
        # Enable/disable batch inputs
        self.spin_batch_count.setEnabled(checked)
        self.chk_custom_pattern.setEnabled(checked)
        self.ed_custom_pattern.setEnabled(checked and self.chk_custom_pattern.isChecked())
        self.spin_custom_start.setEnabled(checked and self.chk_custom_pattern.isChecked())

        # When batching: hard-disable Doc ID editing and grey the row
        if checked:
            # Switch off manual edit if it was on
            if self.btn_toggle_id.isChecked():
                self.btn_toggle_id.blockSignals(True)
                self.btn_toggle_id.setChecked(False)
                self.btn_toggle_id.blockSignals(False)
            self.ed_id.setEnabled(False)
            self.ed_id.setReadOnly(True)
            self.btn_toggle_id.setEnabled(False)
            self.btn_toggle_id.setToolTip("Disabled while creating a batch.")
        else:
            self.ed_id.setEnabled(True)
            self.btn_toggle_id.setEnabled(True)
            self.btn_toggle_id.setToolTip("Edit/lock the ID for a single add.")

        # Batch mode forbids templates (and is reciprocal with Use template)
        if checked:
            self.chk_use_template.blockSignals(True)
            self.chk_use_template.setChecked(False)
            self.chk_use_template.blockSignals(False)
            self.chk_use_template.setEnabled(False)
            self.cb_template.setEnabled(False)
            self.chk_use_template.setToolTip("Disabled while creating a batch.")
            self.cb_template.setToolTip("Disabled while creating a batch.")
        else:
            self.chk_use_template.setEnabled(True)
            self.chk_use_template.setToolTip("Tick to select and apply a template (single add only).")
            self.cb_template.setToolTip("Disabled until 'Use template' is ticked.")
            if self.chk_use_template.isChecked():
                self.cb_template.setEnabled(True)

        self._regen_id()

    def _on_custom_pattern_toggled(self, checked: bool):
        self.ed_custom_pattern.setEnabled(checked and self.chk_batch.isChecked())
        self.spin_custom_start.setEnabled(checked and self.chk_batch.isChecked())

    def _on_use_template_toggled(self, checked: bool):
        # Template is single-add only; reciprocally disable batch controls
        self.cb_template.setEnabled(checked)
        if checked:
            # Turn off batch if it was on
            if self.chk_batch.isChecked():
                self.chk_batch.blockSignals(True)
                self.chk_batch.setChecked(False)
                self.chk_batch.blockSignals(False)
            # Disable batch widgets explicitly
            self.spin_batch_count.setEnabled(False)
            self.chk_custom_pattern.setEnabled(False)
            self.ed_custom_pattern.setEnabled(False)
            self.spin_custom_start.setEnabled(False)
            self.chk_batch.setEnabled(False)
            # Re-enable ID editing controls (single mode UX)
            self.ed_id.setEnabled(True)
            self.btn_toggle_id.setEnabled(True)
            self.chk_batch.setToolTip("Disabled while using a template.")
        else:
            # Re-enable batch check (not selected)
            self.chk_batch.setEnabled(True)
            self.chk_batch.setToolTip("Create many sequential document IDs in one go.")
            # Keep template combo disabled until user checks again
            self.cb_template.setEnabled(False)

    def _show_train_help(self):
        QMessageBox.information(
            self,
            "About trains",
            (
                "Batch creation supports two modes:\n\n"
                "1) Standard train:\n"
                "   Uses <JOB>-<AREA>-<TYPE>-NNN.\n"
                "   Starts from the next unused number after any existing documents.\n"
                "   Example: if DWG-003 exists, and you create 5, you'll get DWG-004 .. DWG-008.\n\n"
                "2) Custom pattern:\n"
                "   Enter a full ID pattern with one placeholder like {XX} or {XXXXX}.\n"
                "   The number of X's sets the zero-padding. You must provide a start number.\n"
                "   Example: 'PFD-PUMP-{XXX}-A', start=12 → PFD-PUMP-012-A, 013, 014, ...\n\n"
                "Templates are disabled while creating a batch."
            ),
        )

    def _regen_id(self):
        if self.btn_toggle_id.isChecked():
            # custom ID mode: ignore Area/Type changes
            return

        area = _parse_area_code(self.cb_area.currentText())
        tcode = _parse_type_code(self.cb_type.currentText())
        pieces = [p for p in [self._project_code, area, tcode] if p]
        prefix = "-".join(pieces)
        suffix = _next_suffix(list(self._existing), prefix) if prefix else "001"
        self._current_prefix = prefix
        self.ed_id.setText(f"{prefix}-{suffix}" if prefix else "")

    # -----------------------------------------------------------------------
    # Accept / build outputs
    # -----------------------------------------------------------------------
    def _ok(self):
        if not self._project_code:
            QMessageBox.information(self, "Required", "This project has no Job Number / Project Code.")
            return
        if self.cb_area.count() == 0:
            QMessageBox.information(self, "Required", "Define at least one Area in Manage Areas.")
            return

        doc_type = _parse_type_code(self.cb_type.currentText())
        base_fields = {
            "description": self.ed_desc.text().strip(),
            "doc_type": doc_type,
            "file_type": self.cb_file.currentText().strip(),
            "status": self.cb_status.currentText().strip(),
            "is_active": 1,
        }

        if self.chk_batch.isChecked():
            # ---------------------- BATCH MODE -------------------------------
            count = int(self.spin_batch_count.value())
            try:
                if self.chk_custom_pattern.isChecked():
                    pattern = (self.ed_custom_pattern.text() or "").strip()
                    start = int(self.spin_custom_start.value())
                    if not pattern:
                        QMessageBox.information(self, "Required", "Enter a custom pattern containing {XXX}.")
                        return
                    ids = _expand_custom_pattern(pattern, start=start, count=count)
                else:
                    area = _parse_area_code(self.cb_area.currentText())
                    tcode = _parse_type_code(self.cb_type.currentText())
                    pieces = [p for p in [self._project_code, area, tcode] if p]
                    prefix = "-".join(pieces)
                    if not prefix:
                        QMessageBox.information(self, "Required", "Document Number could not be generated.")
                        return
                    ids = _scan_next_n_standard(self._existing, prefix, count)
            except ValueError as e:
                QMessageBox.information(self, "Invalid pattern", str(e))
                return

            if self.chk_custom_pattern.isChecked():
                dups = [i for i in ids if i in self._existing]
                if dups:
                    QMessageBox.information(
                        self,
                        "Already exists",
                        "The following IDs already exist and block batch creation:\n\n" + "\n".join(dups[:20]) + (
                            "\n…" if len(dups) > 20 else ""
                        ),
                    )
                    return

            self.payload = None
            self.payloads = [{"doc_id": did, **base_fields} for did in ids]

            try:
                print("[AddDocumentDialog] batch payloads ->", self.payloads[:3],
                      ("... ({} total)".format(len(self.payloads)) if len(self.payloads) > 3 else ""), flush=True)
            except Exception:
                pass

            self.accept()
            return

        # ---------------------- SINGLE MODE --------------------------------
        did = (self.ed_id.text() or "").strip().upper()
        if not did:
            QMessageBox.information(self, "Required", "Document Number could not be generated.")
            return
        if did in self._existing:
            QMessageBox.information(self, "Exists", f"'{did}' already exists.")
            return

        self.payload = {"doc_id": did, **base_fields}
        self.payloads = None

        if self.chk_use_template.isChecked() and self.cb_template.currentIndex() >= 0:
            tpl = self.cb_template.currentData()
            if isinstance(tpl, dict):
                self.payload["use_template"]          = True
                self.payload["template_category"]     = tpl.get("category", "document")
                self.payload["template_kind"]         = tpl.get("kind", "excel")
                self.payload["template_doc_id"]       = tpl.get("doc_id", "")
                self.payload["template_revision"]     = tpl.get("revision", "")
                self.payload["template_description"]  = tpl.get("description", "")
                self.payload["template_relpath"]      = tpl.get("relpath", "")
                self.payload["template_abspath"]      = tpl.get("abs_path", "")
                self.payload["template_path"]         = tpl.get("abs_path", "")

        try:
            print("[AddDocumentDialog] payload ->", self.payload, flush=True)
        except Exception:
            pass

        self.accept()
