from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

try:
    from ..services.markup_scan_service import DEFAULT_IGNORED_AUTHORS, scan_pdfs
except ImportError:
    from services.markup_scan_service import DEFAULT_IGNORED_AUTHORS, scan_pdfs  # type: ignore


class MarkupScanDialog(QDialog):
    """Review mapped-PDF annotations before a transmittal is created."""

    def __init__(self, pdf_paths: list[Path], parent=None):
        super().__init__(parent)
        self._pdf_paths = list(dict.fromkeys(Path(path) for path in pdf_paths))
        self.setWindowTitle("Transmittal Drawing Markup Scan")
        self.resize(1160, 660)
        self.setMinimumSize(820, 500)
        self._build_ui()
        self._scan()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Review the mapped PDF drawings before building the transmittal. "
            "Files with markups or scan errors must be resolved before continuing."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        author_row = QHBoxLayout()
        author_row.addWidget(QLabel("Ignore authors:"))
        self.le_authors = QLineEdit(DEFAULT_IGNORED_AUTHORS)
        self.le_authors.setPlaceholderText("Comma-separated wildcards, e.g. AutoCAD*, *stamp*")
        author_row.addWidget(self.le_authors, 1)
        layout.addLayout(author_row)

        subtype_row = QHBoxLayout()
        subtype_row.addWidget(QLabel("Ignore annotation types:"))
        self.le_subtypes = QLineEdit()
        self.le_subtypes.setPlaceholderText("Optional wildcards, e.g. Link, Widget")
        subtype_row.addWidget(self.le_subtypes, 1)
        layout.addLayout(subtype_row)

        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.clicked.connect(self._scan)
        layout.addWidget(self.btn_rescan, alignment=Qt.AlignLeft)

        self.results = QTableWidget(0, 7, self)
        self.results.setHorizontalHeaderLabels(
            ["Result", "Drawing", "Page", "Type", "Author", "Comment / details", "Ignored"]
        )
        self.results.setSelectionBehavior(QTableWidget.SelectRows)
        self.results.setSelectionMode(QTableWidget.SingleSelection)
        self.results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setColumnWidth(0, 120)
        self.results.setColumnWidth(1, 270)
        self.results.setColumnWidth(2, 55)
        self.results.setColumnWidth(3, 95)
        self.results.setColumnWidth(4, 150)
        self.results.setColumnWidth(5, 340)
        layout.addWidget(self.results, 1)

        self.lbl_summary = QLabel()
        layout.addWidget(self.lbl_summary)

        actions = QHBoxLayout()
        self.btn_open = QPushButton("Open Selected Drawing")
        self.btn_open.clicked.connect(self._open_selected)
        actions.addWidget(self.btn_open)
        actions.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.btn_continue = self.buttons.addButton("Continue Build", QDialogButtonBox.AcceptRole)
        self.btn_continue.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        actions.addWidget(self.buttons)
        layout.addLayout(actions)

    def _scan(self):
        self.btn_rescan.setEnabled(False)
        self.setCursor(Qt.WaitCursor)
        try:
            findings = scan_pdfs(self._pdf_paths, self.le_authors.text(), self.le_subtypes.text())
        except RuntimeError as exc:
            QMessageBox.critical(self, "Markup scan unavailable", str(exc))
            self.reject()
            return
        finally:
            self.unsetCursor()
            self.btn_rescan.setEnabled(True)

        self.results.setRowCount(0)
        blocked_files = {finding.file for finding in findings if finding.status != "PASS"}
        passed_files = {finding.file for finding in findings if finding.status == "PASS"}
        errors = {finding.file for finding in findings if finding.status == "CHECK ERROR"}
        for finding in findings:
            row = self.results.rowCount()
            self.results.insertRow(row)
            values = [
                finding.status, finding.file.name, finding.page, finding.subtype,
                finding.author, finding.contents or finding.error, str(finding.ignored_count),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, str(finding.file))
                    if finding.status == "PASS":
                        item.setForeground(QColor("#087f23"))
                    elif finding.status == "MARKUP FOUND":
                        item.setForeground(QColor("#a40000"))
                    else:
                        item.setForeground(QColor("#b15c00"))
                self.results.setItem(row, column, item)

        self.btn_continue.setEnabled(not blocked_files)
        self.lbl_summary.setText(
            f"Complete: {len(self._pdf_paths)} PDF drawing(s) checked — "
            f"{len(passed_files)} healthy, {len(blocked_files) - len(errors)} with markups, {len(errors)} errors."
        )

    def _open_selected(self):
        row = self.results.currentRow()
        if row < 0:
            return
        path = self.results.item(row, 0).data(Qt.UserRole)
        if not path:
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.critical(self, "Open drawing", f"Could not open drawing:\n{exc}")
