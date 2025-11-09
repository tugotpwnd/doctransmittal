import os
from datetime import datetime
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QComboBox,
    QHBoxLayout, QLineEdit, QSpinBox
)
from PyQt5.QtCore import Qt
import win32com.client


class RfiTestDialog(QDialog):
    """
    Outlook integration test: choose mailbox, optionally filter by keyword,
    and load the most recent X emails (default 20).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Outlook Email Fetch Test")
        self.resize(950, 550)

        layout = QVBoxLayout(self)
        self.info_label = QLabel("Select a mailbox and click Fetch to list recent emails.")
        layout.addWidget(self.info_label)

        # --- Top row controls ---
        top_row = QHBoxLayout()
        self.account_combo = QComboBox()
        self.account_combo.setMinimumWidth(300)
        top_row.addWidget(QLabel("Mailbox:"))
        top_row.addWidget(self.account_combo, 1)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Optional: search subject for keyword (e.g. 'RFI')")
        top_row.addWidget(self.search_box, 1)

        self.limit_box = QSpinBox()
        self.limit_box.setRange(1, 200)
        self.limit_box.setValue(20)
        top_row.addWidget(QLabel("Max emails:"))
        top_row.addWidget(self.limit_box)

        self.btn_fetch = QPushButton("Fetch from Outlook")
        self.btn_fetch.clicked.connect(self.fetch_from_outlook)
        top_row.addWidget(self.btn_fetch)

        layout.addLayout(top_row)

        # --- Email table ---
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Subject", "From", "Received", "Has Attachments"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table, 1)

        # --- Save button ---
        self.btn_save = QPushButton("Save Selected Email + Attachments")
        self.btn_save.clicked.connect(self.save_selected_email)
        self.btn_save.setEnabled(False)
        layout.addWidget(self.btn_save)

        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # Outlook session
        self.messages = []
        self.outlook = None
        self.populate_accounts()

    # ------------------------------------------------------------------
    def populate_accounts(self):
        try:
            self.outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            stores = self.outlook.Stores
            self.account_combo.clear()
            for store in stores:
                self.account_combo.addItem(str(store.DisplayName))
        except Exception as e:
            QMessageBox.warning(self, "Outlook Error", f"Unable to list accounts:\n\n{e}")

    # ------------------------------------------------------------------
    def _on_selection_changed(self):
        self.btn_save.setEnabled(len(self.table.selectedItems()) > 0)

    # ------------------------------------------------------------------
    def fetch_from_outlook(self):
        self.table.setRowCount(0)
        self.messages.clear()

        try:
            selected = self.account_combo.currentText()
            if not selected:
                QMessageBox.information(self, "Select Mailbox", "Please select a mailbox first.")
                return

            keyword = self.search_box.text().strip().lower()
            limit = self.limit_box.value()

            # Locate the target mailbox
            target_store = None
            for store in self.outlook.Stores:
                if store.DisplayName == selected:
                    target_store = store
                    break

            if not target_store:
                QMessageBox.warning(self, "Mailbox Not Found", f"Could not find store for {selected}")
                return

            inbox = target_store.GetDefaultFolder(6)  # 6 = Inbox
            items = inbox.Items
            items.Sort("[ReceivedTime]", True)

            count = 0
            for msg in items:
                subj = str(msg.Subject or "")
                if keyword and keyword not in subj.lower():
                    continue

                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(subj))
                self.table.setItem(row, 1, QTableWidgetItem(str(msg.SenderName or "")))
                self.table.setItem(row, 2, QTableWidgetItem(str(msg.ReceivedTime)))
                self.table.setItem(row, 3, QTableWidgetItem("Yes" if msg.Attachments.Count > 0 else "No"))

                self.messages.append(msg)
                count += 1
                if count >= limit:
                    break

            if count == 0:
                if keyword:
                    QMessageBox.information(self, "No Matches", f"No emails found with '{keyword}' in subject.")
                else:
                    QMessageBox.information(self, "No Emails", "No recent emails found in this mailbox.")
            else:
                if keyword:
                    self.info_label.setText(f"Fetched {count} emails with '{keyword}' from {selected}.")
                else:
                    self.info_label.setText(f"Fetched {count} most recent emails from {selected}.")
        except Exception as e:
            QMessageBox.warning(self, "Outlook Error", f"Error accessing Outlook:\n\n{e}")

    # ------------------------------------------------------------------
    def save_selected_email(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return

        idx = sel[0].row()
        msg = self.messages[idx]

        try:
            subject = str(msg.Subject or "Email").replace(":", "_").replace("/", "_")
            save_dir = os.path.join(os.path.expanduser("~"), "Documents", "RFI_Imports")
            os.makedirs(save_dir, exist_ok=True)

            file_name = f"{subject}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.msg"
            msg_path = os.path.join(save_dir, file_name)
            msg.SaveAs(msg_path)
            self.info_label.setText(f"Saved: {msg_path}")

            if msg.Attachments.Count > 0:
                att_dir = os.path.join(save_dir, f"{subject}_Attachments")
                os.makedirs(att_dir, exist_ok=True)
                for att in msg.Attachments:
                    att.SaveAsFile(os.path.join(att_dir, att.FileName))

            QMessageBox.information(self, "Saved",
                                    f"Email and attachments saved to:\n{save_dir}")
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Failed to save email:\n\n{e}")
