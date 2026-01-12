from __future__ import annotations
from pathlib import Path
from typing import Optional, Dict, List
from PyQt5.QtCore import Qt, pyqtSignal

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
                             QDialogButtonBox, QLabel, QPushButton, QWidget, QMessageBox,
                             QGroupBox, QListWidget, QListWidgetItem, QFileDialog)
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtCore import QUrl


from ..services import db as regdb
from .row_attributes_editor import RowAttributesEditor, DEFAULT_ROW_OPTIONS
from .manage_areas_dialog import ManageAreasDialog
from ..services.logo_store import list_logos, add_logos, remove_logos, logos_dir_for_db



class ProjectSettingsDialog(QDialog):
    saved = pyqtSignal(str, str)  # (job_number, project_name)

    def __init__(self,
                 settings,
                 register_path: str = "",
                 project_root: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Project Settings")
        self.resize(560, 360)

        self.settings = settings
        self.register_path = Path(register_path) if register_path else None
        self.project_root  = Path(project_root)  if project_root  else None

        self.project_code = ""
        self.project_name = ""
        if self.register_path and self.register_path.exists():
            try:
                proj = regdb.get_project(self.register_path)
                if proj:
                    self.project_code = proj.get("project_code", "") or ""
                    self.project_name = proj.get("project_name", "") or ""
                    if not self.project_root:
                        rp = proj.get("root_path") or ""
                        self.project_root = Path(rp) if rp else None
            except Exception:
                pass

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.le_job  = QLineEdit(self); self.le_job.setText(self.project_code)
        self.le_name = QLineEdit(self); self.le_name.setText(self.project_name)
        self.le_client_ref = QLineEdit(self)
        self.le_client_cont = QLineEdit(self)
        self.le_end_user = QLineEdit(self)
        self.le_client_company = QLineEdit(self)

        form.addRow("Job Number:",   self.le_job)
        form.addRow("Project Name:", self.le_name)
        form.addRow("Client Company", self.le_client_company)
        form.addRow("Client Reference", self.le_client_ref)
        form.addRow("Client Contact", self.le_client_cont)
        form.addRow("End User", self.le_end_user)

        # --- Prefill client fields from DB (if present) ---
        try:
            if self.register_path and self.register_path.exists():
                _proj = regdb.get_project(self.register_path)
            else:
                _proj = None
        except Exception:
            _proj = None

        if _proj:
            self.le_client_company.setText(_proj.get("client_company", "") or "")
            self.le_client_ref.setText(_proj.get("client_reference", "") or "")
            self.le_client_cont.setText(_proj.get("client_contact", "") or "")
            self.le_end_user.setText(_proj.get("end_user", "") or "")

        self.le_reg  = QLineEdit(self); self.le_reg.setReadOnly(True);  self.le_reg.setText(str(self.register_path) if self.register_path else "")
        self.le_root = QLineEdit(self); self.le_root.setReadOnly(True); self.le_root.setText(str(self.project_root)  if self.project_root  else "")
        form.addRow("Register DB:",  self.le_reg)
        form.addRow("Project Root:", self.le_root)
        root.addLayout(form)

        admin = QHBoxLayout()
        self.btn_lists = QPushButton("Manage Lists…", self)
        self.btn_areas = QPushButton("Manage Areas…", self)
        self.btn_lists.clicked.connect(self._on_manage_lists)
        self.btn_areas.clicked.connect(self._on_manage_areas)
        admin.addWidget(self.btn_lists); admin.addWidget(self.btn_areas); admin.addStretch(1)
        root.addLayout(admin)

        # --- Client Logos pane ------------------------------------------------
        logos_gb = QGroupBox("Client Logos", self)
        logos_vb = QVBoxLayout(logos_gb)

        self.lst_logos = QListWidget(self)
        self.lst_logos.setSelectionMode(self.lst_logos.ExtendedSelection)

        theme = (settings.get("ui.theme", "dark") or "dark").lower()
        if theme == "light":
            text_col = "#0b1325"
            border_col = "#d7deea"
            sel_bg = "rgba(45,91,255,0.14)"
            hover_bg = "rgba(45,91,255,0.08)"
        else:
            text_col = "#E7ECF4"
            border_col = "#233044"
            sel_bg = "rgba(79,125,255,0.35)"
            hover_bg = "rgba(79,125,255,0.15)"

        self.lst_logos.setStyleSheet(f"""
        QListWidget {{
            background: transparent;
            color: {text_col};
            border: 1px solid {border_col};
        }}
        QListWidget::item {{
            color: {text_col};
            padding: 4px 6px;
        }}
        QListWidget::item:selected {{
            color: {'#000' if theme == 'light' else '#fff'};
            background: {sel_bg};
        }}
        QListWidget::item:hover {{
            background: {hover_bg};
        }}
        """)

        logos_vb.addWidget(self.lst_logos, 1)

        logos_btns = QHBoxLayout()
        self.btn_logo_add = QPushButton("Add…", self)
        self.btn_logo_remove = QPushButton("Remove", self)
        self.btn_logo_open = QPushButton("Open Folder", self)
        logos_btns.addStretch(1)
        logos_btns.addWidget(self.btn_logo_add)
        logos_btns.addWidget(self.btn_logo_remove)
        logos_btns.addWidget(self.btn_logo_open)
        logos_vb.addLayout(logos_btns)

        root.addWidget(logos_gb)

        # wire up
        self.btn_logo_add.clicked.connect(self._logos_add)
        self.btn_logo_remove.clicked.connect(self._logos_remove)
        self.btn_logo_open.clicked.connect(self._logos_open)

        # initial populate
        self._logos_refresh()


        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        btns.accepted.connect(self._on_save); btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ---- admin actions ----
    def _project_id_or_warn(self) -> Optional[int]:
        if not (self.register_path and self.register_path.exists()):
            QMessageBox.information(self, "Project", "Open a project database first (Database tab).")
            return None

        try:
            proj = regdb.get_project(self.register_path)
        except Exception:
            proj = None

        if not proj:
            QMessageBox.information(self, "Project", "Project metadata not set in DB.")
            return None
        return int(proj.get("id", 1))


    def _on_manage_lists(self):
        pid = self._project_id_or_warn()
        if pid is None:
            return

        # read current options from DB (may be empty on a new DB)
        current = regdb.get_row_options(self.register_path, pid) or {}

        # always merge with defaults so the editor isn't blank
        payload = {**DEFAULT_ROW_OPTIONS, **current}

        # write back to DB on Save
        def _save_cb(new_opts):
            regdb.set_row_options(self.register_path, pid, new_opts or {})
            QMessageBox.information(self, "Saved", "Lists saved to project database.")

        dlg = RowAttributesEditor(self.le_job.text().strip() or "", payload, _save_cb, self)
        dlg.exec_()

    def _on_manage_areas(self):
        pid = self._project_id_or_warn()
        if pid is None: return
        rows = regdb.list_areas(str(self.register_path), pid)
        dlg = ManageAreasDialog(rows, parent=self)
        if dlg.exec_() != dlg.Accepted:
            return
        new_rows = dlg.get_rows() or []
        existing = {c for c, _ in rows}; updated = {c for c, _ in new_rows}
        for code, desc in new_rows:
            regdb.upsert_area(str(self.register_path), pid, code, desc)
        for code in (existing - updated):
            regdb.delete_area(str(self.register_path), pid, code)
        QMessageBox.information(self, "Areas", "Areas updated.")

    # ---- save ----
    def _on_save(self):
        job_no = (self.le_job.text() or "").strip()
        name = (self.le_name.text() or "").strip()
        root = (self.le_root.text() or str(self.project_root or "")).strip()

        regdb.upsert_project(
            self.register_path,
            job_no,
            name,
            root,
            client_company=self.le_client_company.text().strip(),
            client_reference=self.le_client_ref.text().strip(),
            client_contact=self.le_client_cont.text().strip(),
            end_user=self.le_end_user.text().strip(),
        )

        self.saved.emit(job_no, name)
        self.accept()

    # ---- Client logos helpers ----
    def _logos_refresh(self):
        self.lst_logos.clear()
        if not (self.register_path and Path(self.register_path).exists()):
            return
        for p in list_logos(Path(self.register_path)):
            it = QListWidgetItem(p.name)
            it.setToolTip(str(p))
            self.lst_logos.addItem(it)

    def _logos_add(self):
        if not (self.register_path and Path(self.register_path).exists()):
            QMessageBox.information(self, "Project", "Open a project database first (Database tab).")
            return
        start_dir = str((Path(self.register_path).parent))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select logo image(s)", start_dir,
            "Images (*.png *.jpg *.jpeg *.svg *.bmp *.tif *.tiff *.gif);;All files (*.*)"
        )
        if not paths:
            return
        add_logos(Path(self.register_path), [Path(p) for p in paths])
        self._logos_refresh()
        QMessageBox.information(self, "Client Logos", "Logo(s) added.")

    def _logos_remove(self):
        if not self.lst_logos.selectedItems():
            return
        names = [it.text() for it in self.lst_logos.selectedItems()]
        removed = remove_logos(Path(self.register_path), names)
        self._logos_refresh()
        if removed:
            QMessageBox.information(self, "Client Logos", f"Removed {removed} file(s).")

    def _logos_open(self):
        if not (self.register_path and Path(self.register_path).exists()):
            QMessageBox.information(self, "Project", "Open a project database first (Database tab).")
            return
        folder = logos_dir_for_db(Path(self.register_path))
        folder.mkdir(parents=True, exist_ok=True)
        # open in Explorer/Finder
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
