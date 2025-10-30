from __future__ import annotations
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QDialogButtonBox, QMessageBox, QLabel
)
from PyQt5.QtCore import Qt

class ManageAreasDialog(QDialog):
    def __init__(self, areas, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Areas")
        self._areas = list(areas)  # [(code, desc), ...]

        self.tbl = QTableWidget(0, 2, self)
        self.tbl.setHorizontalHeaderLabels(["Code (2-digit)", "Description"])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        for code, desc in self._areas:
            self._append_row(code, desc)

        btn_add = QPushButton("Add")
        btn_del = QPushButton("Delete Selected")
        btn_add.clicked.connect(lambda: self._append_row("", ""))
        btn_del.clicked.connect(self._delete_selected)

        row_btns = QHBoxLayout()
        row_btns.addWidget(btn_add); row_btns.addWidget(btn_del); row_btns.addStretch()

        info = QLabel("Tip: Codes are used in the Doc ID (e.g. 00, PS1, DB). Two to four chars recommended.")
        info.setWordWrap(True)

        btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(row_btns)
        lay.addWidget(self.tbl)
        lay.addWidget(btns)

    def _append_row(self, code, desc):
        r = self.tbl.rowCount(); self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(code))
        self.tbl.setItem(r, 1, QTableWidgetItem(desc))

    def _delete_selected(self):
        rows = sorted({idx.row() for idx in self.tbl.selectedIndexes()}, reverse=True)
        for r in rows: self.tbl.removeRow(r)

    def get_rows(self):
        out = []
        for r in range(self.tbl.rowCount()):
            code = (self.tbl.item(r, 0).text() if self.tbl.item(r, 0) else "").strip().upper()
            desc = (self.tbl.item(r, 1).text() if self.tbl.item(r, 1) else "").strip()
            if code: out.append((code, desc))
        # Basic duplicate check
        seen = set()
        for code, _ in out:
            if code in seen:
                QMessageBox.information(self, "Duplicate Code", f"Code '{code}' appears more than once.")
                return None
            seen.add(code)
        return out
