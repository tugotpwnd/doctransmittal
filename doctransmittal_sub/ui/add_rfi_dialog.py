from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Iterable, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextListFormat, QTextCursor, QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox,
    QMessageBox, QHBoxLayout, QLabel, QCheckBox, QWidget, QGroupBox,
    QTextEdit, QToolButton
)

DATE_FMT = "%d/%m/%Y"   # DD/MM/YYYY


# ------------------------- date utils -------------------------
def _parse_date_strict(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    try:
        return datetime.strptime(s, DATE_FMT)
    except Exception:
        return None


def _fmt_date(dt: datetime) -> str:
    return dt.strftime(DATE_FMT)


def _infer_next_seq(existing_numbers: Iterable[str], job_no: str, area: str) -> int:
    """
    Find the next NNN for numbers like JOB-AREA-RFI-<NNN>.
    Returns 1 if none exist.
    """
    job_no = (job_no or "").strip()
    area = (area or "").strip()
    if not job_no or not area:
        return 1
    pat = re.compile(rf"^{re.escape(job_no)}-{re.escape(area)}-RFI-(\d+)$", re.IGNORECASE)
    max_n = 0
    for n in existing_numbers or []:
        m = pat.match((n or "").strip())
        if m:
            try:
                v = int(m.group(1))
                if v > max_n:
                    max_n = v
            except Exception:
                pass
    return max_n + 1 if max_n >= 0 else 1


# ------------------------- tiny rich editor widget -------------------------
class _RichPane(QWidget):
    """QTextEdit with a small formatting toolbar (no presets, no rewrite)."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        outer.addWidget(QLabel(f"<b>{title}</b>"))

        # mini toolbar
        tb = QHBoxLayout()
        self.btn_bold = QToolButton(self); self.btn_bold.setText("B"); self.btn_bold.setCheckable(True); self.btn_bold.setToolTip("Bold")
        self.btn_ital = QToolButton(self); self.btn_ital.setText("I"); self.btn_ital.setCheckable(True); self.btn_ital.setToolTip("Italic")
        self.btn_bul  = QToolButton(self); self.btn_bul.setText("•"); self.btn_bul.setToolTip("Toggle bullets")
        self.btn_clear= QToolButton(self); self.btn_clear.setText("Clear fmt"); self.btn_clear.setToolTip("Remove formatting")

        f = QFont(); f.setBold(True); self.btn_bold.setFont(f)
        f2 = QFont(); f2.setItalic(True); self.btn_ital.setFont(f2)

        tb.addWidget(self.btn_bold); tb.addWidget(self.btn_ital); tb.addWidget(self.btn_bul)
        tb.addSpacing(12); tb.addWidget(self.btn_clear); tb.addStretch(1)
        outer.addLayout(tb)

        # editor
        self.edit = QTextEdit(self)
        self.edit.setAcceptRichText(True)
        outer.addWidget(self.edit, 1)

        # wiring
        self.btn_bold.toggled.connect(self._toggle_bold)
        self.btn_ital.toggled.connect(self._toggle_italic)
        self.btn_bul.clicked.connect(self._toggle_bullets)
        self.btn_clear.clicked.connect(self._clear_format)

    # API
    def to_html(self) -> str:
        return self.edit.toHtml()

    def to_text(self) -> str:
        return self.edit.toPlainText()

    def set_text(self, txt: str):
        self.edit.setPlainText(txt or "")

    # formatting helpers
    def _toggle_bold(self, on: bool):
        fmt = self.edit.currentCharFormat()
        fmt.setFontWeight(QFont.Bold if on else QFont.Normal)
        self._merge_format_on_selection(fmt)

    def _toggle_italic(self, on: bool):
        fmt = self.edit.currentCharFormat()
        fmt.setFontItalic(on)
        self._merge_format_on_selection(fmt)

    def _toggle_bullets(self):
        cursor = self.edit.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.BlockUnderCursor)
        cur_list = cursor.currentList()
        if cur_list:
            # remove list
            fmt = cur_list.format()
            fmt.setIndent(0)
            cursor.createList(fmt)
            cur_list = cursor.currentList()
            if cur_list:
                cur_list.remove(cursor.block())
        else:
            lf = QTextListFormat()
            lf.setStyle(QTextListFormat.ListDisc)
            cursor.createList(lf)
        self.edit.setTextCursor(cursor)

    def _clear_format(self):
        cursor = self.edit.textCursor()
        if cursor.hasSelection():
            txt = cursor.selection().toPlainText()
            cursor.insertText(txt)  # inserts unformatted
        else:
            txt = self.edit.toPlainText()
            self.edit.clear()
            self.edit.setPlainText(txt)

    def _merge_format_on_selection(self, fmt):
        cursor = self.edit.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.edit.mergeCurrentCharFormat(fmt)


# ------------------------- main dialog -------------------------
class AddRfiDialog(QDialog):
    """
    Create a new RFI record (with Background + Information Requested rich-text panes).

    Numbering:
      default: <JOB_NO>-<AREA>-RFI-<NNN>
      or manual override (must be unique against existing_numbers)

    Pass in:
      job_no: str
      areas: List[Tuple[str, str]]  -> (code, description)
      existing_numbers: Iterable[str]
      defaults: Dict[str, str] with optional keys:
        - issued_to, issued_to_company, issued_from, subject
        - issued_date (DD/MM/YYYY). If omitted, today is used.

    Rich text is available via self.richtext_content (HTML & plain text).
    """

    def __init__(
        self,
        *,
        job_no: str,
        areas: List[Tuple[str, str]],
        existing_numbers: Iterable[str],
        defaults: Optional[Dict[str, str]] = None,
        disciplines: Optional[List[str]] = None,
        parent: QWidget = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("New RFI")
        self.setModal(True)
        self.payload: Dict[str, Any] = {}
        self.richtext_content: Dict[str, str] = {}
        self._job_no = (job_no or "").strip()
        self._areas = areas or []
        self._existing = list(existing_numbers or [])
        self._disciplines = list(disciplines or [])

        defaults = defaults or {}
        issued_to = defaults.get("issued_to", "")
        issued_to_company = defaults.get("issued_to_company", "")
        issued_from = defaults.get("issued_from", "")
        subject = defaults.get("subject", "")
        issued_date = defaults.get("issued_date") or _fmt_date(datetime.today())

        # ---- UI ----
        root = QVBoxLayout(self)

        # Top: auto/manual number
        self.chk_manual = QCheckBox("Manually set DOCUMENT NO.", self)
        self.chk_manual.toggled.connect(self._on_manual_toggled)

        self.ed_number = QLineEdit(self)
        self.ed_number.setPlaceholderText("Enter unique RFI number (e.g. JOB-AREA-RFI-001)")
        self.ed_number.setEnabled(False)

        manual_row = QHBoxLayout()
        manual_row.addWidget(self.chk_manual, 0)
        manual_row.addWidget(self.ed_number, 1)
        root.addLayout(manual_row)

        # Area + preview
        self.cb_area = QComboBox(self)
        for code, desc in (self._areas or []):
            label = f"{code} — {desc}" if desc else code
            self.cb_area.addItem(label, code)
        self.cb_area.currentIndexChanged.connect(self._regen_preview)

        self.lbl_preview = QLabel("", self)
        self.lbl_preview.setStyleSheet("font-weight:600;")
        self._regen_preview()

        area_row = QHBoxLayout()
        area_row.addWidget(QLabel("Area:", self))
        area_row.addWidget(self.cb_area, 1)
        area_row.addStretch(1)
        area_row.addWidget(QLabel("Preview:", self))
        area_row.addWidget(self.lbl_preview, 1)
        root.addLayout(area_row)

        # Form
        form = QFormLayout()

        self.cb_discipline = QComboBox(self)
        self.cb_discipline.setEditable(False)
        self.cb_discipline.addItem("")  # empty allowed
        for d in (self._disciplines or []):
            self.cb_discipline.addItem(d)

        self.ed_issued_to = QLineEdit(self);    self.ed_issued_to.setText(issued_to)
        self.ed_issued_to_co = QLineEdit(self); self.ed_issued_to_co.setText(issued_to_company)
        self.ed_issued_from = QLineEdit(self);  self.ed_issued_from.setText(issued_from)

        self.ed_issued_date = QLineEdit(self)
        self.ed_issued_date.setPlaceholderText("DD/MM/YYYY")
        self.ed_issued_date.setText(issued_date)
        self.ed_issued_date.editingFinished.connect(self._maybe_update_respond_by)

        self.ed_respond_by = QLineEdit(self)
        self.ed_respond_by.setPlaceholderText("DD/MM/YYYY")
        self._maybe_update_respond_by()

        self.ed_subject = QLineEdit(self);      self.ed_subject.setText(subject)

        form.addRow("Discipline:", self.cb_discipline)
        form.addRow("Issued To:", self.ed_issued_to)
        form.addRow("Company:", self.ed_issued_to_co)
        form.addRow("Issued From:", self.ed_issued_from)
        form.addRow("Issued Date:", self.ed_issued_date)
        form.addRow("Respond By:", self.ed_respond_by)
        form.addRow("Subject:", self.ed_subject)
        root.addLayout(form)

        # ----- Rich text: Background + Information Requested -----
        gb = QGroupBox("RFI Content")
        v = QVBoxLayout(gb); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(10)

        self.pane_bg = _RichPane("Background", self)
        self.pane_req = _RichPane("Information Requested", self)
        v.addWidget(self.pane_bg)
        v.addWidget(self.pane_req)
        root.addWidget(gb, 1)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self.resize(760, 700)

    # -------- helpers --------
    def _current_area(self) -> str:
        if self.cb_area.count() <= 0:
            return ""
        code = self.cb_area.currentData()
        return (code or "").strip()

    def _regen_preview(self):
        code = self._current_area()
        seq = _infer_next_seq(self._existing, self._job_no, code)
        preview = f"{self._job_no}-{code}-RFI-{seq:03d}" if self._job_no and code else "—"
        self.lbl_preview.setText(preview)

    def _on_manual_toggled(self, checked: bool):
        self.ed_number.setEnabled(checked)

    def _maybe_update_respond_by(self):
        d = _parse_date_strict(self.ed_issued_date.text())
        if d:
            self.ed_respond_by.setText(_fmt_date(d + timedelta(days=7)))

    # -------- accept --------
    def _ok(self):
        # number
        if self.chk_manual.isChecked():
            number = (self.ed_number.text() or "").strip()
            if not number:
                QMessageBox.warning(self, "RFI", "Please enter a DOCUMENT NO.")
                return
            if number in set(self._existing or []):
                QMessageBox.warning(self, "RFI", "That DOCUMENT NO. already exists.")
                return
        else:
            area = self._current_area()
            if not self._job_no or not area:
                QMessageBox.warning(self, "RFI", "Please select an Area.")
                return
            seq = _infer_next_seq(self._existing, self._job_no, area)
            number = f"{self._job_no}-{area}-RFI-{seq:03d}"

        # discipline + dates
        discipline = (self.cb_discipline.currentText() or "").strip()
        issued_date = (self.ed_issued_date.text() or "").strip()
        respond_by = (self.ed_respond_by.text() or "").strip()
        if not _parse_date_strict(issued_date):
            QMessageBox.warning(self, "RFI", "Issued Date must be DD/MM/YYYY.")
            return
        if respond_by and not _parse_date_strict(respond_by):
            QMessageBox.warning(self, "RFI", "Respond By must be DD/MM/YYYY.")
            return

        # capture rich text (for later PDF)
        self.richtext_content = {
            "background_html": self.pane_bg.to_html(),
            "background_text": self.pane_bg.to_text(),
            "request_html": self.pane_req.to_html(),
            "request_text": self.pane_req.to_text(),
        }

        # DB payload (unchanged)
        self.payload = {
            "number": number,
            "discipline": discipline,
            "issued_to": (self.ed_issued_to.text() or "").strip(),
            "issued_to_company": (self.ed_issued_to_co.text() or "").strip(),
            "issued_from": (self.ed_issued_from.text() or "").strip(),
            "issued_date": issued_date,
            "respond_by": respond_by,
            "subject": (self.ed_subject.text() or "").strip(),
            "response_from": "",
            "response_company": "",
            "response_date": "",
            "response_status": "Outstanding",
            "comments": "",
        }
        self.accept()
