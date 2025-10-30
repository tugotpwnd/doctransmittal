from __future__ import annotations
from pathlib import Path
from typing import List
from PyQt5.QtWidgets import QMainWindow, QTabWidget, QAction, QMessageBox, QInputDialog, QDockWidget, QSizePolicy, \
    QApplication, QActionGroup
from PyQt5.QtCore import Qt
from doctransmittal_sub.core.settings import SettingsManager
from doctransmittal_sub.core.excepthook import install_excepthook
from .register_tab import RegisterTab
from .transmittal_tab import TransmittalTab
from .files_tab import FilesTab
from .history_tab import HistoryTab
from ..models.document import DocumentRow
from .widgets.sidebar import SidebarWidget
from .project_settings_dialog import ProjectSettingsDialog
from .templates_dialog import TemplatesDialog
from ..services.db import list_transmittals
from PyQt5.QtGui import QIcon, QPixmap, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout
import sys
from PyQt5.QtWidgets import QLabel, QWidget, QVBoxLayout, QStackedLayout, QSizePolicy
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QAction



# --- UI assets helper (works in dev + PyInstaller) --------------------------
from pathlib import Path

def _res(*parts):
    """
    Returns an absolute path to a file inside doctransmittal_sub/resources/.
    Works both in dev and when packaged with PyInstaller.
    """
    import sys
    if getattr(sys, "_MEIPASS", None):
        # PyInstaller bundle path
        base = Path(sys._MEIPASS) / "doctransmittal_sub" / "resources"
    else:
        base = Path(__file__).resolve().parent.parent / "resources"
    return str((base.joinpath(*parts)).resolve())




class MainWindow(QMainWindow):

    # ---- THEME --------------------------------------------------------------
    def _apply_theme(self):
        theme = (self.settings.get("ui.theme", "dark") or "dark").lower()
        font_delta = int(self.settings.get("ui.font_delta", 0) or 0)

        # --- stable base: never compound the delta ---
        target_pt = max(8, int(self._base_font_pt) + font_delta)
        app = QApplication.instance()
        if app:
            f = QFont(self._base_font_family, target_pt)
            app.setFont(f)

        # --- theme palettes ---
        if theme == "light":
            accent = "#2D5BFF"
            panel = "#f7f8fb"
            border = "#d7deea"
            text = "#0b1325"
            subtext = "#5c6b82"
            pane_bg = "#ffffff"
            head_bg = "#eef2f8"
            list_bg = "#ffffff"
            sel_bg = "rgba(45,91,255,0.14)"
            tree_alt = "#f2f6fc"
            root_bg = "#ffffff"
            tab_txt = "#0b1325"
            tab_bg = "#e9eef7"
            tab_bg_hover = "#dfe7f4"
            tab_bg_sel = "#dfe7f4"
            btn_bg = "#f0f3f9"
            btn_bg_hover = "#e7ecf7"
            btn_bg_press = "#dfe7f4"
        else:
            accent = "#4F7DFF"
            panel = "#0f1724"
            border = "#233044"
            text = "#E7ECF4"
            subtext = "#9fb3c8"
            pane_bg = "#0d1526"
            head_bg = "#121b2d"
            list_bg = "#0f1724"
            sel_bg = "rgba(79,125,255,0.35)"
            tree_alt = "#101a30"
            root_bg = "#0b1220"
            tab_txt = "rgba(255,255,255,0.92)"
            tab_bg = "#1b253a"
            tab_bg_hover = "#223154"
            tab_bg_sel = "#223154"
            btn_bg = "#19233a"
            btn_bg_hover = "#20304c"
            btn_bg_press = "#2b3e64"

        # derive sizes once from target_pt
        tab_pt = target_pt + 3
        brand_title_pt = target_pt + 2

        try:
            self._brand_title.setStyleSheet(f"font-size:{brand_title_pt}pt; font-weight:700; color:{text};")
            self._brand_project.setStyleSheet(f"color:{subtext}; font-size:{target_pt}pt;")
            self._brand_user.setStyleSheet(f"color:#AFC7FF; font-weight:600; font-size:{target_pt}pt;")
        except Exception:
            pass

        self.setStyleSheet(f"""
        QWidget#CentralWrap {{ background: {root_bg}; }}
        QWidget#CentralWrap QLabel, QWidget#CentralWrap QCheckBox {{ color: {text}; }}

        QTabWidget::pane {{
            background: {panel}; border: 1px solid {border}; border-radius: 12px; padding-top: 6px;
        }}
        QTabWidget::tab-bar {{ alignment: left; }}
        QTabBar::tab {{
            min-width: 128px; padding: 10px 24px 10px 26px; margin: 2px 6px;
            border-radius: 12px; font-size: {tab_pt}pt; font-weight: 800;
            color: {tab_txt}; background: {tab_bg};
        }}
        QTabBar::tab:hover    {{ background: {tab_bg_hover}; }}
        QTabBar::tab:selected {{ background: {tab_bg_sel}; color: {'#000' if theme == 'light' else '#fff'}; }}

        QAbstractScrollArea {{ background: transparent; }}
        QAbstractScrollArea::viewport {{ background: transparent; }}

        QTableView {{
            background: transparent; gridline-color:{border}; selection-background-color: {sel_bg};
            border:1px solid {border}; border-radius:10px; color:{text};
        }}
        QHeaderView::section {{
            background:{head_bg}; color:{subtext}; padding:7px 8px; border:0; border-right:1px solid {border}; font-weight:600;
        }}

        QWidget#CentralWrap QGroupBox {{
            color: {text}; border: 1px solid {border}; border-radius: 12px; margin-top: 14px; padding-top: 8px; background: {pane_bg};
        }}
        QWidget#CentralWrap QGroupBox::title {{
            subcontrol-origin: margin; left: 12px; padding: 0 6px; font-weight: 700; color: {text};
        }}

        QWidget#CentralWrap QTreeView, QWidget#CentralWrap QTreeWidget {{
            background: transparent; color: {text}; alternate-background-color: {tree_alt};
            border: 1px solid {border}; border-radius: 10px;
        }}
        QWidget#CentralWrap QTreeView::item:selected, QWidget#CentralWrap QTreeWidget::item:selected {{
            background: {sel_bg}; color: {'#000' if theme == 'light' else '#fff'};
        }}

        QLineEdit, QComboBox, QSpinBox, QTextEdit {{
            background:{list_bg}; color:{text}; border:1px solid {border}; border-radius:10px; padding:7px 9px;
            selection-background-color: {sel_bg};
        }}
        QLineEdit::placeholder, QTextEdit[acceptRichText="false"]::placeholder {{ color: {subtext}; }}

        QPushButton {{
            background:{btn_bg}; color:{text}; border:1px solid {border}; border-radius:12px; padding:8px 12px; font-weight:600;
        }}
        QPushButton:hover  {{ background:{btn_bg_hover}; }}
        QPushButton:pressed{{ background:{btn_bg_press}; }}
        QPushButton#Primary {{ background:{accent}; color:white; border:none; }}

        QToolTip {{ background:{panel}; color:{text}; border:1px solid {border}; padding:6px; border-radius:6px; }}

        QDialog {{
            background: {panel}; border: 1px solid {border}; border-radius: 12px;
        }}
        QDialog QLabel          {{ color: {text}; }}
        QDialog QLabel:disabled {{ color: {subtext}; }}
        QDialog QGroupBox {{
            color:{text}; border:1px solid {border}; border-radius:10px; margin-top:12px; padding-top:6px; background: {pane_bg};
        }}
        QDialog QLineEdit, QDialog QComboBox, QDialog QTextEdit, QDialog QSpinBox {{
            background:{list_bg}; color:{text}; border:1px solid {border}; border-radius:10px; padding:7px 9px;
        }}
        QDialog QLineEdit::placeholder {{ color: {subtext}; }}

        QComboBox QAbstractItemView {{
            background:{list_bg}; color:{text}; border:1px solid {border};
            selection-background-color:{tab_bg_hover}; outline: 0;
        }}

        QMessageBox {{ background: {panel}; border: 1px solid {border}; border-radius: 12px; }}
        QMessageBox QLabel      {{ color: {text}; }}
        QMessageBox QPushButton {{ min-width: 84px; }}

        QDockWidget#LeftDock::title {{
            text-align:left; padding:8px 10px; background: {root_bg}; color: {subtext}; border-bottom: 1px solid {border};
        }}

        #Sidebar {{
            background: {panel}; border-right: 1px solid {border}; padding: 10px;
        }}
        #Sidebar QWidget {{ background: transparent; }}
        #Sidebar QLabel, #Sidebar QCheckBox, #Sidebar QToolButton {{ color: {text}; }}
        #Sidebar QLineEdit, #Sidebar QComboBox, #Sidebar QSpinBox, #Sidebar QTextEdit {{
            background: {list_bg}; color: {text}; border: 1px solid {border}; border-radius: 10px; padding: 7px 9px;
        }}
        #Sidebar QLineEdit::placeholder {{ color: {subtext}; }}
        #Sidebar QComboBox QAbstractItemView {{
            background: {list_bg}; color: {text}; border: 1px solid {border}; selection-background-color: {sel_bg}; outline: 0;
        }}
        #Sidebar QListWidget {{
            background: {list_bg}; color: {text}; border: 1px solid {border}; border-radius: 10px;
        }}
        #Sidebar QGroupBox {{
            color: {text}; border: 1px solid {border}; border-radius: 12px; margin-top: 12px; padding-top: 8px; background: {pane_bg};
        }}
        #Sidebar QGroupBox::title {{
            subcontrol-origin: margin; left: 12px; padding: 0 6px; font-weight: 700; color: {text};
        }}
        #Sidebar QPushButton {{
            background: {btn_bg}; color: {text}; border: 1px solid {border}; border-radius: 12px; padding: 8px 12px; font-weight: 600;
        }}
        #Sidebar QPushButton:hover  {{ background: {btn_bg_hover}; }}
        #Sidebar QPushButton:pressed{{ background: {btn_bg_press}; }}

        /* Light 'More ▾' menu for readability */
        QMenu#BulkMoreMenu {{
            background: #ffffff; color: #111111; border: 1px solid {border}; border-radius: 8px; padding: 6px 4px;
        }}
        QMenu#BulkMoreMenu::separator {{ height: 1px; background: #e0e6f0; margin: 6px 10px; }}
        QMenu#BulkMoreMenu::item {{
            background: transparent; color: #111111; padding: 8px 12px; border-radius: 6px;
        }}
        QMenu#BulkMoreMenu::item:selected {{ background: #e7f0ff; color: #000000; }}
        QMenu#BulkMoreMenu::item:disabled {{ color: #9aa3b2; background: transparent; }}
        """)

    def _build_brand_bar(self):
        bar = QWidget(self)
        bar.setObjectName("BrandBar")
        bar.setFixedHeight(60)
        bar.setStyleSheet("""
            #BrandBar {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(10,16,28,0.92),
                    stop:1 rgba(19,27,44,0.82));
                border:1px solid #233044; border-radius:12px;
            }
        """)
        lay = QHBoxLayout(bar); lay.setContentsMargins(12,8,12,8); lay.setSpacing(12)

        logo = QLabel(bar)
        try:
            pm = QPixmap(_res("logo.png"))
            logo.setPixmap(pm.scaledToHeight(36, Qt.SmoothTransformation))
        except Exception:
            logo.setText(" ")  # fallback
        lay.addWidget(logo)

        self._brand_title = QLabel("Document Manager", bar)
        self._brand_title.setStyleSheet("font-size:16px; font-weight:700; color:#E7ECF4;")
        lay.addWidget(self._brand_title)

        self._brand_project = QLabel("—", bar)
        self._brand_project.setStyleSheet("color:#9fb3c8;")
        lay.addWidget(self._brand_project, 1)

        self._brand_user = QLabel("—", bar)
        self._brand_user.setStyleSheet("color:#AFC7FF; font-weight:600;")
        lay.addWidget(self._brand_user)

        return bar

    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("DocumentTransmittal"); self.resize(1400, 900)

        # --- Central with stacked background ----------------------------------------
        # --- Central content (no background image) ---
        self.tabs = QTabWidget(self)

        wrap = QWidget(self)
        wrap.setObjectName("CentralWrap")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)
        v.addWidget(self._build_brand_bar())
        v.addWidget(self.tabs, 1)

        self.setCentralWidget(wrap)

        self.register_tab = RegisterTab(self.settings, on_proceed=self._on_register_proceed); self.tabs.addTab(self.register_tab, "Database")
        self.transmittal_tab = TransmittalTab(); self.tabs.addTab(self.transmittal_tab, "Transmittal")
        self.files_tab = FilesTab(); self.tabs.addTab(self.files_tab, "Files")
        self.history_tab = HistoryTab(); self.tabs.addTab(self.history_tab, "History")


        # Tab indexes and gating
        self.idx_register = self.tabs.indexOf(self.register_tab)
        self.idx_transmit = self.tabs.indexOf(self.transmittal_tab)
        self.idx_files = self.tabs.indexOf(self.files_tab)
        self.idx_history = self.tabs.indexOf(self.history_tab)
        self.tabs.setTabEnabled(self.idx_transmit, False)
        self.tabs.setTabEnabled(self.idx_files, False)

        # Step navigation (Transmittal -> Files / back)
        self.transmittal_tab.backRequested.connect(self._go_back_to_register)
        self.transmittal_tab.proceedRequested.connect(self._go_to_files_step)
        self.files_tab.backRequested.connect(self._go_back_to_transmittal)
        self.files_tab.proceedCompleted.connect(self._reset_to_register)
        # History ↔ Files (remap edit flow)
        self.history_tab.remapRequested.connect(self._start_remap_from_history)
        self.files_tab.remapCompleted.connect(self._return_to_history_after_remap)


        # LEFT SIDEBAR
        self.sidebar = SidebarWidget()
        self.sidebar.set_user_name(self.settings.get("user.name",""))

        dock = QDockWidget("Filters & Actions", self)
        dock.setWidget(self.sidebar)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)  # keep it fixed
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        dock.setMinimumWidth(320)  # ~double the common default
        dock.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.resizeDocks([dock], [340], Qt.Horizontal)
        self.register_tab.highlightedDocIdsChanged.connect(self.sidebar.update_doc_history_selection)

        dock.setObjectName("LeftDock")
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setAttribute(Qt.WA_StyledBackground, True)  # <— add this

        # Menu: User
        m = self.menuBar().addMenu("User")
        act_name = QAction("Set Name…", self); act_name.triggered.connect(self._set_user_name); m.addAction(act_name)

        # === Appearance menu ===
        m_view = self.menuBar().addMenu("View")
        act_appearance = QAction("Appearance…", self)
        act_appearance.triggered.connect(self._open_appearance_dialog)
        m_view.addAction(act_appearance)

        # Listen for project info from the Register tab
        self.register_tab.projectInfoReady.connect(self._on_project_info_ready)

        # Filters & selection wires
        self.sidebar.filtersChanged.connect(self.register_tab.apply_filters)
        self.sidebar.showOnlySelectedToggled.connect(self.register_tab.set_only_selected_filter)
        self.sidebar.selectAllRequested.connect(self.register_tab.select_all_in_view)
        self.sidebar.clearSelectionRequested.connect(self.register_tab.clear_selection_in_view)
        self.sidebar.clearAllRequested.connect(self.register_tab.clear_selection_all)
        self.register_tab.selectionCountChanged.connect(self.sidebar.set_selected_count)

        # Preset wires
        self.register_tab.presetsReady.connect(self.sidebar.set_preset_names)
        self.sidebar.savePresetRequested.connect(self.register_tab.save_preset_as)
        self.sidebar.loadPresetRequested.connect(self.register_tab.load_preset)
        self.sidebar.unloadPresetRequested.connect(self.register_tab.unload_preset)
        self.sidebar.deletePresetRequested.connect(lambda name: self._delete_preset(name))
        self.register_tab.matchingPresetChanged.connect(self.sidebar.set_loaded_preset_hint)

        # Bulk edit / revisions
        self.sidebar.bulkApplyRequested.connect(self.register_tab.apply_bulk_to_selected)
        self.sidebar.revisionIncrementRequested.connect(self.register_tab._rev_increment_selected)
        self.sidebar.revisionSetRequested.connect(self.register_tab._rev_set_selected)

        # SINGLE batch import hook
        self.sidebar.importBatchRequested.connect(self.register_tab._import_batch_updates)

        # Let the sidebar mirror the project’s row options for its combos
        self.register_tab.rowOptionsReady.connect(self.sidebar.set_apply_option_lists)
        self.register_tab._refresh_option_widgets()

        install_excepthook()

        # Sidebar → Project Settings
        self.sidebar.projectSettingsRequested.connect(self._open_project_settings)
        self.sidebar.templatesRequested.connect(self._open_templates_viewer)

        # Window icon + theme
        try:
            self.setWindowIcon(QIcon(_res("logo.png")))
        except Exception:
            pass

        # Style primary CTAs (gives them the blue accent)
        try:
            self.register_tab.btn_proceed.setObjectName("Primary")
        except Exception:
            pass
        try:
            self.files_tab.btn_proceed.setObjectName("Primary")
        except Exception:
            pass

        try:
            self._brand_user.setText(self.settings.get("user.name","") or "—")
        except Exception:
            pass

        # Apply global theme last
        self._init_appearance_defaults()
        self._apply_theme()

    def _open_appearance_dialog(self):
        # Lazy import to avoid circulars if any
        from .widgets.appearance_dialog import AppearanceDialog
        cur_theme = (self.settings.get("ui.theme", "dark") or "dark").lower()
        cur_delta = int(self.settings.get("ui.font_delta", 0) or 0)
        dlg = AppearanceDialog(cur_theme, cur_delta, parent=self)
        if dlg.exec_() == dlg.Accepted:
            theme, delta = dlg.values()
            self._apply_appearance_values(theme, delta)

    def _apply_appearance_values(self, theme: str, delta: int):
        # Persist and re-skin; this is also used by the dialog's Apply button
        self.settings.set("ui.theme", theme)
        self.settings.set("ui.font_delta", int(delta))
        self._apply_theme()

    def _init_appearance_defaults(self):
        """Capture a stable base font once and load saved appearance prefs."""
        app = QApplication.instance()
        default_font = app.font() if app else QFont()

        # Persist a stable base so we don't 'compound' deltas on every apply()
        if self.settings.get("ui.base_point_size") is None:
            self.settings.set("ui.base_point_size", default_font.pointSize() or 10)
        if self.settings.get("ui.base_font_family") is None:
            self.settings.set("ui.base_font_family", default_font.family() or QFont().family())

        # Cache for quick access
        self._base_font_pt = int(self.settings.get("ui.base_point_size") or 10)
        self._base_font_family = self.settings.get("ui.base_font_family") or default_font.family()

        # Ensure defaults exist for theme and font delta
        if self.settings.get("ui.theme") is None:
            self.settings.set("ui.theme", "dark")
        if self.settings.get("ui.font_delta") is None:
            self.settings.set("ui.font_delta", 0)

    def _delete_preset(self, name: str):
        name = (name or "").strip()
        if not name:
            QMessageBox.information(self, "Presets", "No preset selected."); return

        try:
            db_s, _ = self.register_tab.current_paths()
            pid = self.register_tab.project_id
        except Exception:
            db_s, pid = "", None
        if not db_s or pid is None:
            QMessageBox.information(self, "No project", "Open a project database first (Database tab)."); return

        # Confirm delete
        resp = QMessageBox.question(
            self, "Delete preset?",
            f"Delete preset “{name}”?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return

        from ..services.db import delete_preset
        ok = delete_preset(Path(db_s), int(pid), name)
        if not ok:
            QMessageBox.information(self, "Presets", f"Preset '{name}' not found.")
        self.register_tab.refresh_presets()

        # Toast
        try:
            from .widgets.toast import toast
            toast(self, f"Preset “{name}” deleted")
        except Exception:
            pass


    def _on_project_info_ready(self, job_no: str, project_name: str, project_root: object, db_path: object):
        self.sidebar.set_project_info(job_no, project_name)
        if isinstance(db_path, Path) and db_path.exists():
            print(f"[MainWindow] wiring tabs to db_path={db_path}", flush=True)
            try:
                self.history_tab.set_db_path(db_path)
                self.transmittal_tab.set_db_path(db_path)   # no project_root now
                self.sidebar.set_db_path(db_path)

            except Exception as e:
                print(f"[MainWindow] ERROR wiring tabs: {e}", flush=True)

            # Update brand bar project text
            try:
                self._brand_project.setText(f"{job_no or '—'} — {project_name or '—'}")
            except Exception:
                pass



    def _set_user_name(self):
        cur = self.settings.get("user.name","")
        name, ok = QInputDialog.getText(self, "Your name", "Enter display name:", text=cur)
        if ok:
            self.settings.set("user.name", name)
            self.sidebar.set_user_name(name)

        try:
            self._brand_user.setText(name or "—")
        except Exception:
            pass


    def finalize_mapping_to_transmittal(self):
        mapping = self.files_tab.get_mapping(); self.transmittal_tab.set_file_mapping(mapping)

    def _open_project_settings(self):
        try:
            reg_path, root_path = self.register_tab.current_paths()
        except Exception:
            reg_path, root_path = "", ""

        reg_s = str(reg_path) if reg_path else ""
        root_s = str(root_path) if root_path else ""

        dlg = ProjectSettingsDialog(self.settings, reg_s, root_s, self)
        dlg.saved.connect(self._on_project_settings_saved)
        dlg.exec_()

    def _open_templates_viewer(self):
        dlg = TemplatesDialog(self)
        dlg.exec_()

    def _on_project_settings_saved(self, job_no: str, project_name: str):
        self.sidebar.set_project_info(job_no, project_name)
        self.settings.set("project.job_number", job_no)
        self.settings.set("project.name", project_name)

    # === Register → Transmittal (no project_root) ===
    def _on_register_proceed(self, items: List[DocumentRow], db_path: Path):
        user = self.settings.get("user.name","")
        if not user:
            QMessageBox.information(self,"Who are you?","Please set your name (User → Set Name…)"); return
        self.transmittal_tab.set_selection(items, db_path, user)
        self.tabs.setTabEnabled(self.idx_transmit, True)
        self.tabs.setTabEnabled(self.idx_files, False)
        self.tabs.setCurrentIndex(self.idx_transmit)

    def _go_back_to_register(self):
        self.tabs.setCurrentIndex(self.idx_register)
        # Optional: lock down the later steps again
        self.tabs.setTabEnabled(self.idx_transmit, False)
        self.tabs.setTabEnabled(self.idx_files, False)

    # === Transmittal → Files (no project_root) ===
    def _go_to_files_step(self, payload: dict):
        try:
            self.files_tab.set_flow_context(
                db_path=payload.get("db_path"),
                items=payload.get("items") or [],
                file_mapping=payload.get("file_mapping") or {},
                user=payload.get("user", ""),
                title=payload.get("title", ""),
                client=payload.get("client", ""),
            )
            # If a source folder was nominated in Transmittal, prime the Files tree with it
            src = (payload.get("source_root") or "").strip()
            if src and hasattr(self.files_tab, "set_root_folder"):
                try:
                    self.files_tab.set_root_folder(src)
                except Exception:
                    pass

        except Exception:
            # Back-compat shims (older FilesTab)
            if hasattr(self.files_tab, "set_db"):
                self.files_tab.set_db(payload.get("db_path"))
            if hasattr(self.files_tab, "set_items"):
                self.files_tab.set_items(payload.get("items") or [])

        self.tabs.setTabEnabled(self.idx_files, True)
        self.tabs.setCurrentIndex(self.idx_files)

    def _go_back_to_transmittal(self):
        # Ensure the transmittal tab is enabled and navigate back
        try:
            self.tabs.setTabEnabled(self.idx_transmit, True)
        except Exception:
            pass
        self.tabs.setCurrentIndex(self.idx_transmit)

    def _reset_to_register(self, trans_dir_path: str = ""):
        """After building a transmittal, clear state and return to Register (database) tab."""
        # Clear both workflow tabs
        try:
            if hasattr(self.transmittal_tab, "reset"):
                self.transmittal_tab.reset()
        except Exception:
            pass
        try:
            if hasattr(self.files_tab, "reset"):
                self.files_tab.reset()
        except Exception:
            pass

        # Gate the steps off until a new run starts
        try:
            self.tabs.setTabEnabled(self.idx_transmit, False)
            self.tabs.setTabEnabled(self.idx_files, False)
        except Exception:
            pass

        # Go back to Register/Database tab
        self.tabs.setCurrentIndex(self.idx_register)


    def _start_remap_from_history(self, payload: dict):
        """
        Payload from HistoryTab: { mode:'edit', transmittal_number, db_path, items, file_mapping, user/title/client }
        """
        try:
            self.files_tab.set_flow_context_edit(payload)
        except Exception as e:
            QMessageBox.warning(self, "Remap", f"Could not start remap:\n{e}")
            return
        self.tabs.setTabEnabled(self.idx_files, True)
        self.tabs.setCurrentIndex(self.idx_files)

    def _return_to_history_after_remap(self, trans_number: str, trans_dir_path: str):
        # Refresh history and bounce user back there
        try:
            if hasattr(self.history_tab, "refresh"):
                self.history_tab.refresh()
        except Exception:
            pass
        self.tabs.setCurrentIndex(self.idx_history)
        if trans_dir_path:
            QMessageBox.information(
                self, "Remap complete",
                f"Updated {trans_number} and rebuilt.\n\n{trans_dir_path}"
            )
