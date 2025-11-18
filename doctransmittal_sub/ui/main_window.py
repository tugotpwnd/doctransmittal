from __future__ import annotations

import warnings
from pathlib import Path
from typing import List

import pandas as pd
from PyQt5.QtWidgets import QMainWindow, QTabWidget, QAction, QMessageBox, QInputDialog, QDockWidget, QSizePolicy, \
    QApplication, QActionGroup, QFileDialog, QDialog
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

from ..services.db import get_project, list_documents_with_latest
from ..services.receipt_pdf import export_progress_report_pdf
from datetime import datetime, date
from .rfi_tab import RfiTab
from .widgets.rfi_sidebar import RfiSidebarWidget
from PyQt5.QtWidgets import QLineEdit, QPushButton, QLabel, QHBoxLayout  # if not already imported
from .checkprint_tab import CheckPrintTab


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

        # sizes
        tab_pt = target_pt + 3
        brand_title_pt = target_pt + 2

        try:
            self._brand_title.setStyleSheet(f"font-size:{brand_title_pt}pt; font-weight:700; color:{text};")
            self._brand_project.setStyleSheet(f"color:{subtext}; font-size:{target_pt}pt;")
            self._brand_user.setStyleSheet(f"color:#AFC7FF; font-weight:600; font-size:{target_pt}pt;")
        except Exception:
            pass

        # --- stylesheet ---
        self.setStyleSheet(f"""
        QWidget#CentralWrap {{
            background: {root_bg};
        }}
        QWidget#CentralWrap QLabel,
        QWidget#CentralWrap QCheckBox {{
            color: {text};
        }}

        QTabWidget::pane {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 12px;
            padding-top: 6px;
        }}

        QTabWidget::tab-bar {{
            alignment: left;
        }}

        /* === SUB-HEADERS (Register / Transmittal / Files / History) === */
        QTabBar#SubTabBar::tab {{
            min-width: 140px;
            min-height: 34px;            /* compact; still no clipping */
            padding: 7px 18px;
            margin: 3px 4px;
            border-radius: 10px;
            font-size: {tab_pt}pt;
            font-weight: 700;
            line-height: 1.35em;
            color: {tab_txt};
            background: {tab_bg};
            border: 1px solid transparent;
        }}
        QTabBar#SubTabBar::tab:hover {{
            background: {tab_bg_hover};
        }}
        QTabBar#SubTabBar::tab:selected {{
            background: {tab_bg_sel};
            color: {'#000' if theme == 'light' else '#fff'};
            border: 1px solid {accent};
        }}

        /* === MAIN HEADERS (Document Register / RFI) === */
        QTabWidget#MainTabs::pane {{
            border: none;
            background: {root_bg};
            margin-top: 4px;
        }}
        QTabBar#MainTabBar::tab {{
            min-width: 300px;
            min-height: 60px;            /* clearly taller */
            padding: 16px 36px;
            margin: 8px 10px;
            border-radius: 16px;
            font-size: {tab_pt + 5}pt;   /* larger than sub */
            font-weight: 900;
            line-height: 1.55em;
            color: {tab_txt};
            background: {tab_bg};
            border: 1px solid {border};
        }}
        QTabBar#MainTabBar::tab:hover {{
            background: {tab_bg_hover};
        }}
        QTabBar#MainTabBar::tab:selected {{
            background: {tab_bg_sel};
            color: {'#000' if theme == 'light' else '#fff'};
            border: 1px solid {accent};
        }}
        QTabWidget#MainTabs::tab-bar {{
            border-bottom: 2px solid {border};
            padding-bottom: 2px;
        }}

        QAbstractScrollArea {{
            background: transparent;
        }}
        QAbstractScrollArea::viewport {{
            background: transparent;
        }}

        QTableView {{
            background: transparent;
            gridline-color:{border};
            selection-background-color: {sel_bg};
            alternate-background-color: {tree_alt};   /* ✅ fixes bright white alt rows */
            border:1px solid {border};
            border-radius:10px;
            color:{text};
        }}
        QHeaderView::section {{
            background: {head_bg};
            color: {subtext};
            padding: 7px 8px;
            border: 0;
            border-right: 1px solid {border};
            font-weight: 600;
        }}

        QWidget#CentralWrap QGroupBox {{
            color: {text};
            border: 1px solid {border};
            border-radius: 12px;
            margin-top: 14px;
            padding-top: 8px;
            background: {pane_bg};
        }}
        QWidget#CentralWrap QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            font-weight: 700;
            color: {text};
        }}

        QWidget#CentralWrap QTreeView,
        QWidget#CentralWrap QTreeWidget {{
            background: transparent;
            color: {text};
            alternate-background-color: {tree_alt};
            border: 1px solid {border};
            border-radius: 10px;
        }}
        QWidget#CentralWrap QTreeView::item:selected,
        QWidget#CentralWrap QTreeWidget::item:selected {{
            background: {sel_bg};
            color: {'#000' if theme == 'light' else '#fff'};
        }}

        QLineEdit, QComboBox, QSpinBox, QTextEdit {{
            background: {list_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 7px 9px;
            selection-background-color: {sel_bg};
        }}
        QLineEdit::placeholder,
        QTextEdit[acceptRichText="false"]::placeholder {{
            color: {subtext};
        }}

        QPushButton {{
            background: {btn_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 12px;
            padding: 8px 12px;
            font-weight: 600;
        }}
        QPushButton:hover  {{ background: {btn_bg_hover}; }}
        QPushButton:pressed{{ background: {btn_bg_press}; }}
        QPushButton#Primary {{
            background: {accent};
            color: white;
            border: none;
        }}

        QToolTip {{
            background: {panel};
            color: {text};
            border: 1px solid {border};
            padding: 6px;
            border-radius: 6px;
        }}

        QDialog {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 12px;
        }}
        QDialog QLabel          {{ color: {text}; }}
        QDialog QLabel:disabled {{ color: {subtext}; }}
        QDialog QGroupBox {{
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
            margin-top: 12px;
            padding-top: 6px;
            background: {pane_bg};
        }}
        QDialog QLineEdit,
        QDialog QComboBox,
        QDialog QTextEdit,
        QDialog QSpinBox {{
            background: {list_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 7px 9px;
        }}
        QDialog QLineEdit::placeholder {{ color: {subtext}; }}

        QComboBox QAbstractItemView {{
            background: {list_bg};
            color: {text};
            border: 1px solid {border};
            selection-background-color: {tab_bg_hover};
            outline: 0;
        }}

        QMessageBox {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 12px;
        }}
        QMessageBox QLabel      {{ color: {text}; }}
        QMessageBox QPushButton {{ min-width: 84px; }}

        QDockWidget#LeftDock::title {{
            text-align: left;
            padding: 8px 10px;
            background: {root_bg};
            color: {subtext};
            border-bottom: 1px solid {border};
        }}

        #Sidebar {{
            background: {panel};
            border-right: 1px solid {border};
            padding: 10px;
        }}
        #Sidebar QWidget {{ background: transparent; }}
        #Sidebar QLabel, #Sidebar QCheckBox, #Sidebar QToolButton {{ color: {text}; }}
        #Sidebar QLineEdit, #Sidebar QComboBox, #Sidebar QSpinBox, #Sidebar QTextEdit {{
            background: {list_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 7px 9px;
        }}
        #Sidebar QLineEdit::placeholder {{ color: {subtext}; }}
        #Sidebar QComboBox QAbstractItemView {{
            background: {list_bg};
            color: {text};
            border: 1px solid {border};
            selection-background-color: {sel_bg};
            outline: 0;
        }}
        #Sidebar QListWidget {{
            background: {list_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
        }}
        #Sidebar QGroupBox {{
            color: {text};
            border: 1px solid {border};
            border-radius: 12px;
            margin-top: 12px;
            padding-top: 8px;
            background: {pane_bg};
        }}
        #Sidebar QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            font-weight: 700;
            color: {text};
        }}
        #Sidebar QPushButton {{
            background: {btn_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 12px;
            padding: 8px 12px;
            font-weight: 600;
        }}
        #Sidebar QPushButton:hover  {{ background: {btn_bg_hover}; }}
        #Sidebar QPushButton:pressed{{ background: {btn_bg_press}; }}

        /* Light 'More ▾' menu for readability */
        QMenu#BulkMoreMenu {{
            background: #ffffff;
            color: #111111;
            border: 1px solid {border};
            border-radius: 8px;
            padding: 6px 4px;
        }}
        QMenu#BulkMoreMenu::separator {{
            height: 1px;
            background: #e0e6f0;
            margin: 6px 10px;
        }}
        QMenu#BulkMoreMenu::item {{
            background: transparent;
            color: #111111;
            padding: 8px 12px;
            border-radius: 6px;
        }}
        QMenu#BulkMoreMenu::item:selected {{
            background: #e7f0ff;
            color: #000000;
        }}
        QMenu#BulkMoreMenu::item:disabled {{
            color: #9aa3b2;
            background: transparent;
        }}
                /* ===== CHECKPRINT TAB TEXT COLOURS ===== */
        QWidget#CheckPrintTab QLabel {{
            color: {text};
            background: transparent;
        }}

        QWidget#CheckPrintTab QGroupBox {{
            color: {text};
            background: transparent;
        }}

        QWidget#CheckPrintTab QListWidget {{
            color: {text};
            background: transparent;
        }}

        QWidget#CheckPrintTab QTreeView {{
            color: {text};
            background: transparent;
        }}

        QWidget#CheckPrintTab QTreeWidget {{
            color: {text};
            background: transparent;
        }}

        QWidget#CheckPrintTab QCheckBox {{
            color: {text};
            background: transparent;
        }}

        QWidget#CheckPrintTab QPushButton {{
            color: {text};
        }}

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

    def _build_global_db_bar(self):
        bar = QWidget(self)
        bar.setObjectName("DbBar")
        lay = QHBoxLayout(bar);
        lay.setContentsMargins(0, 0, 0, 0);
        lay.setSpacing(8)
        self.le_db_global = QLineEdit(self)
        self.le_db_global.setPlaceholderText("Select Project Database (*.db)")
        btn_open = QPushButton("Open…", self);
        btn_open.clicked.connect(self._open_db_dialog)
        btn_new = QPushButton("New…", self);
        btn_new.clicked.connect(self._new_db_dialog)
        lay.addWidget(QLabel("Database:"))
        lay.addWidget(self.le_db_global, 1)
        lay.addWidget(btn_open)
        lay.addWidget(btn_new)
        return bar

    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("DocumentTransmittal"); self.resize(1920, 1920)

        # --- Central with stacked background ----------------------------------------
        # --- Central content (no background image) ---
        # --- NEW: big headers (Documents / RFI) with global DB bar under the brand bar ---
        self.mainTabs = QTabWidget(self)

        doc_page = QWidget(self)
        doc_v = QVBoxLayout(doc_page);
        doc_v.setContentsMargins(0, 0, 0, 0);
        doc_v.setSpacing(0)
        self.tabs = QTabWidget(doc_page)  # keep existing name so the rest of the code still works
        doc_v.addWidget(self.tabs, 1)

        self.rfi_tab = RfiTab(self)

        wrap = QWidget(self);
        wrap.setObjectName("CentralWrap")
        v = QVBoxLayout(wrap);
        v.setContentsMargins(12, 12, 12, 12);
        v.setSpacing(10)
        v.addWidget(self._build_brand_bar())
        v.addWidget(self._build_global_db_bar())
        v.addWidget(self.mainTabs, 1)
        self.setCentralWidget(wrap)

        # Documents page keeps your current tabs
        self.register_tab = RegisterTab(self.settings, on_proceed=self._on_register_proceed)
        self.tabs.addTab(self.register_tab, "Register")

        self.transmittal_tab = TransmittalTab()
        self.tabs.addTab(self.transmittal_tab, "Transmittal")

        self.files_tab = FilesTab()
        self.tabs.addTab(self.files_tab, "Files")

        self.history_tab = HistoryTab()
        self.tabs.addTab(self.history_tab, "History")

        self.checkprint_tab = CheckPrintTab()
        self.idx_checkprint = self.tabs.addTab(self.checkprint_tab, "CheckPrint")
        self.tabs.setTabEnabled(self.idx_checkprint, True)
        self.files_tab.checkprintStarted.connect(self._on_checkprint_started)

        # --- name the tab bars so we can style them differently ---
        self.mainTabs.setObjectName("MainTabs")
        self.mainTabs.tabBar().setObjectName("MainTabBar")  # top-level tabs: Document Register / RFI
        self.tabs.tabBar().setObjectName("SubTabBar")  # inner tabs: Register / Transmittal / Files / History

        # Hide the old internal DB row inside RegisterTab (we use the global bar)
        try:
            self.register_tab.hide_db_controls(True)
        except Exception:
            pass

        # Top-level headers
        self.mainTabs.addTab(doc_page, "Document Register")
        # self.mainTabs.addTab(self.rfi_tab, "RFI (Disabled)")
        # Top-level headers
        self.mainTabs.setTabEnabled(1, False)  # Disable RFI tab


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

        # Use the RfiTab’s actual sidebar (so its signals & project info work)
        self.rfi_tab.sidebar.setObjectName("Sidebar")
        self.rfi_tab.sidebar.setAttribute(Qt.WA_StyledBackground, True)

        def _swap_sidebar(idx: int):
            try:
                dock = self.findChild(QDockWidget, "LeftDock")
                if idx == self.mainTabs.indexOf(self.rfi_tab):
                    dock.setWidget(self.rfi_tab.sidebar)  # ← use the instance owned by RfiTab
                else:
                    dock.setWidget(self.sidebar)
            except Exception:
                pass

        self.mainTabs.currentChanged.connect(_swap_sidebar)
        _swap_sidebar(self.mainTabs.currentIndex())  # ensure correct widget on startup

        # Menu: User
        m = self.menuBar().addMenu("User")
        act_name = QAction("Set Name…", self); act_name.triggered.connect(self._set_user_name); m.addAction(act_name)

        # === Appearance menu ===
        m_view = self.menuBar().addMenu("View")
        act_appearance = QAction("Appearance…", self)
        act_appearance.triggered.connect(self._open_appearance_dialog)
        m_view.addAction(act_appearance)
        # --- RFI Test ---
        from .rfi_test_dialog import RfiTestDialog
        # m_rfi = self.menuBar().addMenu("RFI")
        # act_rfi_test = QAction("RFI Drop Test…", self)
        # act_rfi_test.triggered.connect(lambda: RfiTestDialog(self).exec_())
        # m_rfi.addAction(act_rfi_test)

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
        self.sidebar.revisionDecrementRequested.connect(self.register_tab._rev_decrement_selected)

        self.sidebar.revisionSetRequested.connect(self.register_tab._rev_set_selected)

        # SINGLE batch import hook
        self.sidebar.importBatchRequested.connect(self.register_tab._import_batch_updates)

        # Let the sidebar mirror the project’s row options for its combos
        self.register_tab.rowOptionsReady.connect(self.sidebar.set_apply_option_lists)
        self.register_tab._refresh_option_widgets()

        # Progress report
        self.sidebar.printProgressRequested.connect(self._print_progress_report)
        # Register report
        self.sidebar.printRegisterRequested.connect(self._on_print_register)
        # Migrate Excel
        self.sidebar.migrateExcelRequested.connect(self._on_migrate_excel_clicked)

        install_excepthook()

        # Sidebar → Project Settings
        self.sidebar.projectSettingsRequested.connect(self._open_project_settings)
        self.sidebar.templatesRequested.connect(self._open_templates_viewer)
        self.rfi_tab.sidebar.projectSettingsRequested.connect(self._open_project_settings)
        self.rfi_tab.sidebar.templatesRequested.connect(self._open_templates_viewer)
        # Auto-refresh progress donut when the register table changes
        try:
            self.register_tab.model.dataChanged.connect(lambda *a, **k: self.sidebar.refresh_progress())
            self.register_tab.model.modelReset.connect(lambda *a, **k: self.sidebar.refresh_progress())
        except Exception:
            pass

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

    from PyQt5.QtWidgets import QFileDialog
    import warnings
    import pandas as pd

    def _on_migrate_excel_clicked(self):
        # 1) DB source of truth comes from Register tab
        db_path = getattr(self.register_tab, "db_path", None)
        project_id = getattr(self.register_tab, "project_id", None)
        if not (db_path and project_id):
            QMessageBox.information(self, "Project", "Open a project database first (Database tab).")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Excel Register",
            "",
            "Excel Files (*.xlsx *.xls *.xlsm)"
        )
        if not path:
            return

        print(f"[migrate] Opening workbook: {path}")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
            xf = pd.ExcelFile(path, engine="openpyxl")
            sheet = "MI Documents" if "MI Documents" in xf.sheet_names else xf.sheet_names[0]
            # Read without headers first so we can detect the header row robustly
            df0 = pd.read_excel(xf, sheet_name=sheet, header=None, dtype=object)

        print(f"[migrate] Sheet picked: {sheet}")
        print(f"[migrate] Raw sheet shape: rows={df0.shape[0]}, cols={df0.shape[1]}")

        # ---- Detect header row ----
        header_row = None
        keys = ["rev", "document no.", "document no", "doc id", "document id",
                "document type", "doc type", "type", "file type", "description", "status"]
        scan_upto = min(40, len(df0))  # look near the top only
        for i in range(scan_upto):
            cells = [str(v).strip().lower() for v in df0.iloc[i].tolist()]
            # count how many key tokens appear in this row (as substrings)
            hits = sum(any(k in cell for cell in cells) for k in keys)
            if hits >= 3 and any("document" in cell for cell in cells):
                header_row = i
                break

        # Fallback (your template usually has header at row 9 / 1-based = 9)
        if header_row is None:
            header_row = 8  # 0-based index -> Excel row 9
        print(f"[migrate] Header row auto-detected: {header_row + 1} (1-based)")

        # Re-read using the detected header row
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
            df = pd.read_excel(xf, sheet_name=sheet, header=header_row, dtype=object)

        # Drop noise columns (Unnamed, numeric “revision number” columns, etc.)
        keep_cols = []
        for c in df.columns:
            s = str(c).strip()
            sl = s.lower()
            if sl.startswith("unnamed"):
                continue
            # drop purely numeric headers like "1", "1.1" (revision number columns)
            if sl.replace(".", "", 1).isdigit():
                continue
            # drop any column whose header says "revision number"
            if "revision number" in sl:
                continue
            keep_cols.append(c)
        df = df.loc[:, keep_cols]

        print("[migrate] Cleaned headers:", [str(c) for c in df.columns.tolist()])

        # Build a normalized header map
        norm = {str(c).strip().lower(): c for c in df.columns}

        def pick(*aliases):
            # exact match or startswith fallback
            for a in aliases:
                a = a.strip().lower()
                if a in norm: return norm[a]
            for a in aliases:
                a = a.strip().lower()
                for k, raw in norm.items():
                    if k.startswith(a): return raw
            return None

        col_doc_id = pick("document no.", "document no", "doc id", "document id", "document number")
        col_type = pick("document type", "doc type", "type")
        col_filetype = pick("file type")
        col_desc = pick("description")
        col_status = pick("status")
        col_latest = pick("latest rev", "rev")

        print("[migrate] Resolved columns ->",
              dict(doc_id=col_doc_id, doc_type=col_type, file_type=col_filetype,
                   description=col_desc, status=col_status, latest_rev=col_latest))

        # Preview a few rows for sanity
        try:
            prev_cols = [c for c in [col_latest, col_doc_id, col_type, col_filetype, col_desc, col_status] if c]
            print("[migrate] Preview:")
            print(df[prev_cols].head(5).to_string(index=False))
        except Exception:
            print("[migrate] Preview skipped")

        # Build candidate rows
        rows = []
        for _, r in df.iterrows():
            did = "" if col_doc_id is None else r.get(col_doc_id, "")
            did = "" if pd.isna(did) else str(did).strip()
            if not did:
                continue
            rows.append({
                "doc_id": did,
                "doc_type": "" if col_type is None else (
                    "" if pd.isna(r.get(col_type)) else str(r.get(col_type)).strip()),
                "file_type": "" if col_filetype is None else (
                    "" if pd.isna(r.get(col_filetype)) else str(r.get(col_filetype)).strip()),
                "description": "" if col_desc is None else (
                    "" if pd.isna(r.get(col_desc)) else str(r.get(col_desc)).strip()),
                "status": "" if col_status is None else (
                    "" if pd.isna(r.get(col_status)) else str(r.get(col_status)).strip()),
                "latest_rev": "" if col_latest is None else (
                    "" if pd.isna(r.get(col_latest)) else str(r.get(col_latest)).strip()),
            })

        print(f"[migrate] Candidate rows parsed: {len(rows)}")
        if not rows:
            QMessageBox.information(self, "Import",
                                    "No valid rows found (couldn’t see a 'Document No.'/Doc ID column).")
            return

        # Skip dupes and insert
        from ..services.db import upsert_document, add_revision_by_docid, _connect
        con = _connect(db_path)
        existing = {(r[0] or "").strip().upper() for r in con.execute(
            "SELECT doc_id FROM documents WHERE project_id=?", (project_id,)
        )}
        con.close()
        print(f"[migrate] Existing doc_ids in DB: {len(existing)}")

        inserted = skipped = 0
        for doc in rows:
            key = doc["doc_id"].strip().upper()
            if not key or key in existing:
                skipped += 1
                continue
            upsert_document(db_path, project_id, doc)
            if doc.get("latest_rev"):
                try:
                    add_revision_by_docid(db_path, project_id, doc["doc_id"], doc["latest_rev"])
                except Exception as e:
                    print(f"[migrate] add_revision_by_docid failed for {doc['doc_id']}: {e}")
            inserted += 1

        print(f"[migrate] Done. Inserted={inserted}, Skipped={skipped}")
        self.register_tab._reload_rows()
        QMessageBox.information(self, "Migration complete",
                                f"Imported {inserted} new document(s).\nSkipped {skipped}.")


    def _on_print_register(self):
        try:
            from doctransmittal_sub.services.db import get_project
        except Exception:
            from ..services.db import get_project

        if not getattr(self.register_tab, "db_path", None):
            QMessageBox.information(self, "Project", "Open a project database first.")
            return

        db_path = Path(self.register_tab.db_path)
        proj = get_project(db_path) or {}
        pid = int(proj.get("id", 0))
        if not pid:
            QMessageBox.information(self, "Project", "Project metadata not set in DB.")
            return

        # === SAME PATHING AS PROGRESS REPORT ===
        base = db_path.parent
        if base.name.startswith("."):
            base = base.parent  # keep reports beside the visible project dir
        out_dir = base / "Reports"
        out_dir.mkdir(parents=True, exist_ok=True)

        fname = f"{proj.get('project_code', 'PROJECT')}-Register-{date.today().isoformat()}.pdf"
        out_pdf = out_dir / fname
        # =======================================

        header = {
            "header_title": "DOCUMENT REGISTER",
            "db_path": str(db_path),  # lets receipt_pdf gather client logos
            "_pdf_out_path": str(out_pdf),  # also helps logo fallback near output
        }

        from ..services.receipt_pdf import export_register_report_pdf
        pdf_path = export_register_report_pdf(out_pdf, header, db_path=db_path, project_id=pid)

        QMessageBox.information(self, "Register PDF", f"Saved:\n{pdf_path}")
        try:
            import webbrowser
            webbrowser.open_new(str(pdf_path))
        except Exception:
            pass

    # add this method on MainWindow
    def _print_progress_report(self):
        try:
            db_s, root_s = self.register_tab.current_paths()
        except Exception:
            db_s, root_s = "", ""
        if not db_s:
            QMessageBox.information(self, "Progress Report", "Open a project database first (Database tab).")
            return

        dbp = Path(db_s)
        proj = get_project(dbp)
        if not proj:
            QMessageBox.information(self, "Progress Report", "Project metadata not found in this DB.")
            return

        # Fetch documents (active) for chart + table
        docs = list_documents_with_latest(dbp, proj["id"], state="active")

        # Header/meta for PDF (client logos are auto-discovered same as receipts)
        header = {
            "header_title": "PROGRESS TRACKER",
            "project_code": proj.get("project_code", ""),
            "title": proj.get("project_name", ""),
            "client": proj.get("client_company", ""),
            "end_user": proj.get("end_user", ""),
            "created_by": self.settings.get("user.name", ""),
            "created_on": datetime.now().strftime("%Y-%m-%d"),
            "register_path": str(dbp),
            "db_path": str(dbp),  # for logo discovery
        }

        # Save next to the DB in a clear folder
        out_dir = dbp.parent / "Reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        job = proj.get("project_code") or "PROJECT"
        out_pdf = out_dir / f"{job}_Progress_{stamp}.pdf"

        try:
            export_progress_report_pdf(out_pdf, header, docs)
        except Exception as e:
            QMessageBox.warning(self, "Progress Report", f"Failed to build PDF:\n{e}")
            return

        QMessageBox.information(self, "Progress Report", f"Saved:\n{out_pdf}")
        try:
            import webbrowser
            webbrowser.open_new(str(out_pdf))
        except Exception:
            pass

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
        self.apply_theme_to_open_dialogs()

    def apply_theme_to_open_dialogs(self):
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog):
                try:
                    if hasattr(w, "_apply_theme"):
                        w._apply_theme()
                except Exception:
                    pass

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
                self.rfi_tab.set_db_path(db_path)  # <— add this
                self.checkprint_tab.set_db_path(db_path)
                print("Set paths")



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

    def _on_checkprint_started(self, cp_code: str, cp_dir: str):
        """
        After 'Proceed: Submit for CheckPrint' completes,
        reset the entire workflow back to the clean Register state.
        Mirrors the legacy transmittal reset behaviour.
        """

        try:
            # ---- Reset FILES TAB ----
            if hasattr(self.files_tab, "reset"):
                self.files_tab.reset()

            # ---- Reset TRANSMITTAL TAB (if running workflows before CheckPrint) ----
            if hasattr(self.transmittal_tab, "reset"):
                self.transmittal_tab.reset()

            # ---- Disable Transmittal + Files unless user starts flow again ----
            try:
                self.tabs.setTabEnabled(self.idx_transmit, False)
                self.tabs.setTabEnabled(self.idx_files, False)
            except Exception:
                pass

            # Go back to Register/Database tab
            self.tabs.setCurrentIndex(self.idx_register)

        except Exception as e:
            print("Error syncing after CheckPrint:", e)

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

    def _apply_db_path(self, path: Path):
        if not path: return
        self.le_db_global.setText(str(path))
        try:
            # Load via RegisterTab (it will emit projectInfoReady)
            self.register_tab.load_db_from_path(str(path))
        except Exception:
            pass
        try:
            # Also refresh RFI tab explicitly
            self.rfi_tab.set_db_path(Path(path))
        except Exception:
            pass

    def _open_db_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project DB", "", "Database (*.db)")
        if path:
            self._apply_db_path(Path(path))

    def _new_db_dialog(self):
        # Choose where to create
        path_s, _ = QFileDialog.getSaveFileName(self, "Create Project DB", "", "Database (*.db)")
        if not path_s:
            return
        p = Path(path_s)
        if p.suffix.lower() != ".db":
            p = p.with_suffix(".db")

        # If it already exists, offer to use it as-is (no overwrite), or overwrite fresh
        if p.exists():
            resp = QMessageBox.question(
                self,
                "Database exists",
                f"“{p.name}” already exists.\n\n"
                "Use this file as the current project?\n"
                "Choose No to overwrite it with a fresh database.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes
            )
            if resp == QMessageBox.Cancel:
                return
            if resp == QMessageBox.Yes:
                # Just load it
                self._apply_db_path(p)
                return
            # Overwrite: try to remove old file (ignore failures)
            try:
                p.unlink()
            except Exception:
                pass

        # Collect minimal project metadata
        code, ok = QInputDialog.getText(self, "Project code", "8-digit job number / code:")
        if not ok or not code.strip():
            return
        name, ok = QInputDialog.getText(self, "Project name", "Project name:")
        if not ok or not name.strip():
            return

        # Create + seed defaults (so Manage Lists isn't blank)
        from ..services.db import init_db, upsert_project, get_project, set_row_options
        from .row_attributes_editor import DEFAULT_ROW_OPTIONS

        init_db(p)
        upsert_project(p, code.strip(), name.strip(), str(p.parent))
        try:
            proj = get_project(p)
            if proj:
                set_row_options(p, proj["id"], DEFAULT_ROW_OPTIONS)
        except Exception:
            pass

        # Now load the DB we just created
        self._apply_db_path(p)

# ======== APPLICATION ENTRY POINT FOR PYINSTALLER ========

def main_window_entry():
    """
    Launches the full application when running from PyInstaller.
    Mirrors doctransmittal_sub.app.run(), but avoids relative import issues.
    """
    import sys
    from PyQt5.QtWidgets import QApplication
    from doctransmittal_sub.core.settings import SettingsManager

    # Create Qt application
    app = QApplication(sys.argv)

    # Settings manager
    settings = SettingsManager()

    # Create and show main window
    win = MainWindow(settings)
    win.show()

    # Start event loop
    sys.exit(app.exec_())
