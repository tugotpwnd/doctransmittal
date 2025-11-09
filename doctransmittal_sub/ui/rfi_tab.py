# rfi_tab.py (drop-in replacement)
from __future__ import annotations

import getpass
from pathlib import Path
from typing import List, Dict, Any, Optional

from PyQt5.QtCore import Qt, QTimer, QSize, QEvent, QSortFilterProxyModel, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QTextOption
from PyQt5.QtWidgets import QStyle, QStyleOptionButton
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTableView, QLabel, QTabWidget, QHeaderView,
    QStyledItemDelegate, QComboBox, QTextEdit, QPushButton, QMessageBox,
    QHBoxLayout, QFileDialog, QDialog, QDialogButtonBox, QVBoxLayout as QV, QApplication
)
from PyQt5.QtCore import QSettings

# ---------- DB helpers ----------
try:
    from ..services.db import (
        get_project, list_rfis, update_rfi_fields, init_db, list_areas, create_rfi
    )
except Exception:
    from services.db import get_project, list_rfis, update_rfi_fields, init_db, list_areas
    try:
        from services.db import create_rfi
    except Exception:
        create_rfi = None  # type: ignore

# ---------- Templates store ----------
try:
    from ..services.templates_store import load_templates, resolve_abs_path, CATEGORY_LABELS
except Exception:
    from services.templates_store import load_templates, resolve_abs_path, CATEGORY_LABELS

# ---------- Model ----------
try:
    from .widgets.rfi_model import RfiTableModel, RFI_COLS, DISCIPLINE_OPTS, STATUS_OPTS
except Exception:
    from rfi_model import RfiTableModel, RFI_COLS, DISCIPLINE_OPTS, STATUS_OPTS

# ---------- Add-RFI dialog ----------
try:
    from .add_rfi_dialog import AddRfiDialog
except Exception:
    from add_rfi_dialog import AddRfiDialog

# ---------- RFI sidebar ----------
try:
    from .widgets.rfi_sidebar import RfiSidebarWidget
except Exception:
    from rfi_sidebar import RfiSidebarWidget

# ---------- PDF generator ----------
try:
    from ..services.rfi_pdf import generate_rfi_pdf
except Exception:
    from rfi_pdf import generate_rfi_pdf


# ========================= Delegates =========================
class ComboDelegate(QStyledItemDelegate):
    def __init__(self, options: List[str], parent=None):
        super().__init__(parent); self._options = list(options)
    def createEditor(self, parent, option, index):
        box = QComboBox(parent); box.addItems(self._options); box.setEditable(False); return box
    def setEditorData(self, editor: QComboBox, index):
        val = (index.data(Qt.EditRole) or "").strip()
        idx = editor.findText(val, Qt.MatchFixedString)
        if idx >= 0: editor.setCurrentIndex(idx)
    def setModelData(self, editor: QComboBox, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

class WrappedTextDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent); self._wrap_mode = QTextOption.WrapAtWordBoundaryOrAnywhere
    def createEditor(self, parent, option, index):
        edit = QTextEdit(parent); edit.setAcceptRichText(False)
        opt = edit.document().defaultTextOption(); opt.setWrapMode(self._wrap_mode)
        edit.document().setDefaultTextOption(opt); edit.setFrameStyle(0); edit.installEventFilter(self); return edit
    def setEditorData(self, editor: QTextEdit, index):
        editor.setPlainText(index.data(Qt.EditRole) or "")
    def setModelData(self, editor: QTextEdit, model, index):
        model.setData(index, editor.toPlainText(), Qt.EditRole)
    def eventFilter(self, editor, ev):
        if isinstance(editor, QTextEdit) and ev.type() == QEvent.FocusOut:
            self.commitData.emit(editor); self.closeEditor.emit(editor)
        return super().eventFilter(editor, ev)
    def sizeHint(self, option, index):
        text = (index.data(Qt.DisplayRole) or "")
        if not text: return super().sizeHint(option, index)
        fm = QFontMetrics(option.font); w = max(60, option.rect.width() - 12)
        br = fm.boundingRect(0,0,w,10_000, Qt.TextWordWrap, text)
        h = max(28, br.height() + 10); return QSize(w, h)

class ButtonDelegate(QStyledItemDelegate):
    """Real push-button cell (“Edit…”). Emits clicked(index) on mouse release."""
    clicked = pyqtSignal(object)  # QModelIndex

    def paint(self, painter, option, index):
        opt = QStyleOptionButton()
        opt.state = QStyle.State_Enabled
        if option.state & QStyle.State_MouseOver:
            opt.state |= QStyle.State_MouseOver
        opt.rect = option.rect.adjusted(6, 6, -6, -6)
        opt.text = "Contents…"
        QApplication.style().drawControl(QStyle.CE_PushButton, opt, painter)

    def editorEvent(self, event, model, option, index):
        if event.type() in (event.MouseButtonRelease, event.MouseButtonDblClick):
            rect = option.rect.adjusted(6, 6, -6, -6)
            if rect.contains(event.pos()):
                self.clicked.emit(index)
                return True
        return False


# ========================= Filter proxy (Subject + Background + Request) =========================
class RfiFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent); self._needle = ""
    def setNeedle(self, s: str):
        self._needle = (s or "").strip().lower(); self.invalidateFilter()
    def filterAcceptsRow(self, r, parent):
        if not self._needle: return True
        src = self.sourceModel()
        try:
            row = src.raw_row(r)  # type: ignore
        except Exception:
            return True
        hay = " ".join([
            row.get("subject",""),
            row.get("background_text",""),
            row.get("request_text",""),
        ]).lower()
        return self._needle in hay


# ============================== RFI Tab ==============================
class RfiTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_path: Optional[Path] = None
        self._project_id: Optional[int] = None
        self._project: Dict[str, Any] = {}

        self._settings = getattr(parent, "settings", None)
        self._qsettings = None if self._settings is not None else QSettings("DocTransmittal", "DocuTrans")

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(8)

        # sub tabs container
        self.sub = QTabWidget(self)
        try: self.sub.tabBar().setObjectName("SubTabBar")
        except Exception: pass
        root.addWidget(self.sub, 1)

        # ---- Register page ----
        reg_wrap = QWidget(self); reg_lay = QVBoxLayout(reg_wrap); reg_lay.setContentsMargins(4,4,4,4)
        tools = QHBoxLayout()
        self.btn_new_rfi = QPushButton("New RFI", reg_wrap)
        self.btn_new_rfi.clicked.connect(self._on_new_rfi)
        tools.addWidget(self.btn_new_rfi); tools.addStretch(1)
        reg_lay.addLayout(tools)

        self.tbl = QTableView(self)
        self.model = RfiTableModel([])
        self.proxy = RfiFilterProxy(self); self.proxy.setSourceModel(self.model)
        self.tbl.setModel(self.proxy)
        self.tbl.setSelectionBehavior(QTableView.SelectRows)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setWordWrap(True); self.tbl.setTextElideMode(Qt.ElideNone)

        # Delegates (map proxy→source indices)
        def src_col(name: str) -> int:
            return [*RFI_COLS].index(name)

        self.tbl.setItemDelegateForColumn(src_col("Discipline"), ComboDelegate(DISCIPLINE_OPTS, self.tbl))
        self.tbl.setItemDelegateForColumn(src_col("Response Status"), ComboDelegate(STATUS_OPTS, self.tbl))
        self.tbl.setItemDelegateForColumn(src_col("Comments"), WrappedTextDelegate(self.tbl))

        # --- NEW: real button in “Contents” column ---
        self._btn_delegate = ButtonDelegate(self.tbl)
        self._btn_delegate.clicked.connect(self._on_click_contents)
        self.tbl.setItemDelegateForColumn(src_col("Contents"), self._btn_delegate)

        # persist inline edits
        self.model.set_save_callback(self._save_fields)

        reg_lay.addWidget(self.tbl, 1)
        self.sub.addTab(reg_wrap, "RFI Register")
        self.sub.addTab(QLabel("Log RFI Response — UI coming soon"), "Log Response")

        # column width persistence
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.setInterval(250)
        self.tbl.horizontalHeader().sectionResized.connect(lambda *_: self._save_timer.start())
        self._save_timer.timeout.connect(self._save_column_widths)
        self.model.dataChanged.connect(lambda *_: self.tbl.resizeRowsToContents())
        self.model.edited.connect(lambda: self.tbl.resizeRowsToContents())
        QTimer.singleShot(0, self._restore_column_widths)

        # ---- Sidebar ----
        self.sidebar = RfiSidebarWidget(self)
        self.sidebar.generatePdfRequested.connect(self._on_generate_pdf)
        self.sidebar.printRfiProgressRequested.connect(lambda: QMessageBox.information(self, "RFI", "Coming soon"))
        self.sidebar.searchTextChanged.connect(self.proxy.setNeedle)
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setAttribute(Qt.WA_StyledBackground, True)

    # ---------------- Public wiring ----------------
    def set_db_path(self, db_path: Path):
        p = Path(db_path) if db_path else None
        self._db_path = p
        if not p:
            self._project_id = None; self._project = {}; self.model.set_rows([])
            try: self.sidebar.set_project_info("", "")
            except Exception: pass
            return
        try: init_db(p)
        except Exception: pass

        proj = get_project(p) or {}
        self._project = proj; self._project_id = proj.get("id")
        try: self.sidebar.set_project_info(proj.get("project_code",""), proj.get("project_name",""))
        except Exception: pass
        self.reload()

    def reload(self):
        rows = []
        if self._db_path and self._project_id:
            try: rows = list_rfis(self._db_path, int(self._project_id)) or []
            except Exception: rows = []
        self.model.set_rows(rows); self.tbl.resizeRowsToContents()
        try: self.sidebar.refresh_progress(rows)
        except Exception: pass

    # ---------------- Inline edit persist ----------------
    def _save_fields(self, number: str, fields: Dict[str, Any]):
        if not (self._db_path and self._project_id and number):
            return
        update_rfi_fields(self._db_path, int(self._project_id), number, fields)
        self.reload()

    # ---------------- Helpers ----------------
    def _current_row_dict(self) -> Optional[Dict[str, Any]]:
        sel = self.tbl.selectionModel().selectedRows()
        if not sel: return None
        r = self.proxy.mapToSource(sel[0]).row()
        return self.model.raw_row(r)

    def _select_row_by_number(self, number: str):
        for r in range(self.model.rowCount()):
            if self.model.raw_row(r).get("number") == number:
                idx_src = self.model.index(r, 0)
                idx = self.proxy.mapFromSource(idx_src)
                self.tbl.selectRow(idx.row())
                return

    # ---------------- New RFI flow ----------------
    def _existing_numbers(self) -> List[str]:
        rows = getattr(self.model, "_rows", []) or []
        return [str(r.get("number", "")) for r in rows if r.get("number")]

    def _on_new_rfi(self):
        if not (self._db_path and self._project_id):
            QMessageBox.information(self, "New RFI", "Open a project database first.")
            return

        proj = self._project or {}
        job_no = proj.get("project_code", "") or ""
        client_contact = proj.get("client_contact", "") or ""
        client_company = proj.get("client_company", "") or ""

        user_name = ""
        try:
            s = getattr(self, "settings", None) or getattr(self.parent(), "settings", None)
            if s: user_name = s.get("user.name", "") or s.get("user.full_name", "") or ""
        except Exception: user_name = ""
        if not user_name: user_name = getpass.getuser()

        try: areas = list_areas(str(self._db_path), int(self._project_id)) or []
        except Exception: areas = []

        dlg = AddRfiDialog(
            job_no=job_no, areas=areas, existing_numbers=self._existing_numbers(),
            disciplines=DISCIPLINE_OPTS,
            defaults={"issued_to": client_contact, "issued_to_company": client_company, "issued_from": user_name},
            parent=self,
        )
        if dlg.exec_() != dlg.Accepted:
            return

        payload = dlg.payload or {}
        # attach rich text for DB
        rt = dlg.richtext_content or {}
        payload.update({
            "background_html": rt.get("background_html",""),
            "request_html": rt.get("request_html",""),
            "background_text": rt.get("background_text",""),
            "request_text": rt.get("request_text",""),
        })

        number = payload.get("number","")

        if create_rfi and self._db_path and self._project_id:
            try:
                ok = create_rfi(self._db_path, int(self._project_id), payload)
                if not ok:
                    QMessageBox.warning(self, "RFI", "An RFI with that number already exists.")
                    return
                self.reload()
            except Exception as e:
                QMessageBox.warning(self, "RFI", f"Failed to save RFI:\n{e}")
                return
        else:
            rows = getattr(self.model, "_rows", []) or []
            rows = rows + [payload]; self.model.set_rows(rows); self.tbl.resizeRowsToContents()

        # select the new row and immediately prompt PDF creation
        if number:
            self._select_row_by_number(number)
            self._on_generate_pdf()  # prompt for template/out path and create

    # ---------------- Generate / Re-generate PDF ----------------
    def _pick_default_rfi_template(self) -> Optional[Path]:
        try:
            items = load_templates() or []
        except Exception:
            items = []
        for t in items:
            cat_label = (t.get("category_label") or "").strip().lower()
            rel = (t.get("relpath") or "").strip()
            if cat_label == "rfi" and rel.lower().endswith(".pdf"):
                try:
                    p = resolve_abs_path({"relpath": rel})
                    return p if p and Path(p).is_file() else None
                except Exception:
                    continue
        for t in items:
            rel = (t.get("relpath") or "").strip()
            hay = " ".join([(t.get("doc_id") or ""), (t.get("description") or ""), rel]).lower()
            if "rfi" in hay and rel.lower().endswith(".pdf"):
                try:
                    p = resolve_abs_path({"relpath": rel})
                    return p if p and Path(p).is_file() else None
                except Exception:
                    continue
        return None

    def _on_generate_pdf(self):
        if not (self._db_path and self._project_id):
            QMessageBox.information(self, "RFI", "Open a project database first.")
            return
        rfi_row = self._current_row_dict()
        if not rfi_row:
            QMessageBox.information(self, "RFI", "Select a single RFI row first.")
            return

        template = self._pick_default_rfi_template()
        if not template:
            QMessageBox.warning(self, "RFI Template",
                                "No RFI PDF template found.\nOpen Templates… and ensure you have a template with category 'RFI' and a PDF path.")
            return
        if not str(template).lower().endswith(".pdf"):
            QMessageBox.warning(self, "RFI Template", "Selected template is not a PDF.")
            return

        default_name = f"{rfi_row.get('number','RFI')}.pdf"
        out_path, _ = QFileDialog.getSaveFileName(self, "Save RFI PDF", default_name, "PDF (*.pdf)")
        if not out_path:
            return

        proj = get_project(self._db_path) or {}
        company_logo = Path(proj.get("company_logo_path","")) if proj.get("company_logo_path") else None
        client_logo  = Path(proj.get("client_logo_path",""))  if proj.get("client_logo_path")  else None

        ok = generate_rfi_pdf(
            template_pdf=Path(template),
            out_pdf=Path(out_path),
            rfi_row=rfi_row,
            project=proj,
            background_text=rfi_row.get("background_text",""),
            request_text=rfi_row.get("request_text",""),
            company_logo=company_logo,
            client_logo=client_logo,
        )
        if ok:
            QMessageBox.information(self, "RFI", f"Created:\n{out_path}")
        else:
            QMessageBox.warning(self, "RFI", "Could not fill the PDF. Check the form fields in the template.")

    # ---------------- “Contents” editor ----------------
    class _ContentDialog(QDialog):
        def __init__(self, *, bg_html: str, req_html: str, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Edit RFI Contents")
            v = QV(self); v.setContentsMargins(8,8,8,8); v.setSpacing(8)

            from PyQt5.QtWidgets import QGroupBox, QVBoxLayout as QVL, QLabel, QTextEdit
            gb = QGroupBox("Background"); vb = QVL(gb); self.ed_bg = QTextEdit(); self.ed_bg.setAcceptRichText(True)
            self.ed_bg.setHtml(bg_html or ""); vb.addWidget(self.ed_bg)
            v.addWidget(gb, 1)

            gb2 = QGroupBox("Information Requested"); vr = QVL(gb2); self.ed_req = QTextEdit(); self.ed_req.setAcceptRichText(True)
            self.ed_req.setHtml(req_html or ""); vr.addWidget(self.ed_req)
            v.addWidget(gb2, 1)

            btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
            btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
            v.addWidget(btns)

        def values(self):
            bg_html = self.ed_bg.toHtml(); req_html = self.ed_req.toHtml()
            bg_text = self.ed_bg.toPlainText(); req_text = self.ed_req.toPlainText()
            return {"background_html": bg_html, "request_html": req_html,
                    "background_text": bg_text, "request_text": req_text}

    def _on_click_contents(self, proxy_index):
        # Button click lands here
        if proxy_index.column() != RFI_COLS.index("Contents"):
            return
        src_index = self.proxy.mapToSource(proxy_index)
        row = self.model.raw_row(src_index.row())
        number = row.get("number","")
        dlg = self._ContentDialog(bg_html=row.get("background_html",""),
                                  req_html=row.get("request_html",""),
                                  parent=self)
        if dlg.exec_() != dlg.Accepted:
            return
        vals = dlg.values()
        update_rfi_fields(self._db_path, int(self._project_id), number, vals)
        self.reload()

    # ---------------- Column widths ----------------
    def _settings_get(self, key: str, default: str = "") -> str:
        if self._settings is not None:
            try: return str(self._settings.get(key, default) or default)
            except Exception: return default
        return self._qsettings.value(key, default, type=str)

    def _settings_set(self, key: str, value: str) -> None:
        if self._settings is not None:
            try: self._settings.set(key, value); return
            except Exception: pass
        self._qsettings.setValue(key, value)

    def _col_widths_key(self) -> str:
        return "rfi.columns.widths.v2"  # bumped version due to new column

    def _save_column_widths(self):
        try:
            hh = self.tbl.horizontalHeader()
            widths = [str(hh.sectionSize(i)) for i in range(self.model.columnCount())]
            self._settings_set(self._col_widths_key(), ",".join(widths))
        except Exception:
            pass

    def _restore_column_widths(self):
        try:
            saved = self._settings_get(self._col_widths_key(), "")
            hh = self.tbl.horizontalHeader()
            if saved:
                parts = [p for p in saved.split(",") if p.strip().isdigit()]
                for i, wtxt in enumerate(parts[: self.model.columnCount()]):
                    hh.resizeSection(i, int(wtxt))
            else:
                idx = {n: i for i, n in enumerate(RFI_COLS)}
                if "Subject" in idx:  hh.resizeSection(idx["Subject"], 260)
                if "Comments" in idx: hh.resizeSection(idx["Comments"], 320)
                if "Contents" in idx: hh.resizeSection(idx["Contents"], 120)
        except Exception:
            pass
