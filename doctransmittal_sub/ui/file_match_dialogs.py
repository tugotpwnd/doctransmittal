from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def _path_text(path: Path, root: Optional[Path] = None) -> str:
    try:
        p = Path(path).resolve()
        if root:
            try:
                return str(p.relative_to(Path(root).resolve()))
            except Exception:
                pass
        return str(p)
    except Exception:
        return str(path)


def _ext(path: Path) -> str:
    return (Path(path).suffix or "").lower() or "(none)"


def select_native_exact_matches(
    parent,
    native_items: Sequence[dict],
    *,
    source_root: Optional[Path] = None,
    title: str = "Native file matches found",
) -> Optional[Set[str]]:
    """
    Ask the user whether exact single-match non-PDF/native files should be used.

    native_items entries must include: doc_id, revision, path.
    Returns a set of selected doc_id values, or None if cancelled.
    """
    if not native_items:
        return set()

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(980, 460)

    root = QVBoxLayout(dlg)
    msg = QLabel(
        "The following documents have one exact filename match, but the matched file is not a PDF.\n"
        "Leave a row ticked to use the native file. Untick it to leave the document unmapped for manual selection."
    )
    msg.setWordWrap(True)
    root.addWidget(msg)

    tbl = QTableWidget(len(native_items), 5, dlg)
    tbl.setHorizontalHeaderLabels(["Use", "Doc ID", "Revision", "Type", "Matched file"])
    tbl.setSelectionBehavior(QTableWidget.SelectRows)
    tbl.setEditTriggers(QTableWidget.NoEditTriggers)

    for r, item in enumerate(native_items):
        use_item = QTableWidgetItem("")
        use_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        use_item.setCheckState(Qt.Checked)
        tbl.setItem(r, 0, use_item)
        tbl.setItem(r, 1, QTableWidgetItem(str(item.get("doc_id", ""))))
        tbl.setItem(r, 2, QTableWidgetItem(str(item.get("revision", ""))))
        p = Path(item.get("path", ""))
        tbl.setItem(r, 3, QTableWidgetItem(_ext(p)))
        tbl.setItem(r, 4, QTableWidgetItem(_path_text(p, source_root)))

    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
    root.addWidget(tbl, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
    root.addWidget(buttons)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec_() != QDialog.Accepted:
        return None

    selected: Set[str] = set()
    for r, item in enumerate(native_items):
        chk = tbl.item(r, 0)
        if chk and chk.checkState() == Qt.Checked:
            did = str(item.get("doc_id", "")).strip()
            if did:
                selected.add(did)
    return selected


def resolve_ambiguous_file_matches(
    parent,
    ambiguous: Dict[str, List[Path]],
    *,
    revisions: Optional[Dict[str, str]] = None,
    source_root: Optional[Path] = None,
    title: str = "Resolve multiple file matches",
) -> Optional[Dict[str, Optional[Path]]]:
    """
    Let the user choose one file for each document with multiple exact matches.

    Returns {doc_id: Path | None}. None value means manual selection later.
    Returns None if the whole dialog was cancelled.
    """
    if not ambiguous:
        return {}

    doc_ids = list(ambiguous.keys())
    all_exts = sorted({_ext(p) for paths in ambiguous.values() for p in paths})

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(1150, 560)

    root = QVBoxLayout(dlg)
    msg = QLabel(
        "Multiple exact file matches were found for one or more documents.\n"
        "Choose the file to use for each document, or choose manual selection."
    )
    msg.setWordWrap(True)
    root.addWidget(msg)

    pref_row = QHBoxLayout()
    pref_row.addWidget(QLabel("Preferred file type:"))
    cmb_pref = QComboBox(dlg)
    cmb_pref.addItem("Resolve individually", "")
    for ext in all_exts:
        cmb_pref.addItem(ext, ext)
    pref_row.addWidget(cmb_pref)
    btn_apply = QPushButton("Apply preferred type", dlg)
    pref_row.addWidget(btn_apply)
    pref_row.addStretch(1)
    root.addLayout(pref_row)

    tbl = QTableWidget(len(doc_ids), 4, dlg)
    tbl.setHorizontalHeaderLabels(["Doc ID", "Revision", "Available types", "Selected file"])
    tbl.setSelectionBehavior(QTableWidget.SelectRows)
    tbl.setEditTriggers(QTableWidget.NoEditTriggers)

    combos: Dict[str, QComboBox] = {}

    for r, did in enumerate(doc_ids):
        paths = sorted({Path(p).resolve() for p in ambiguous.get(did, [])}, key=lambda p: (p.suffix.lower(), p.name.lower(), str(p.parent).lower()))
        rev = (revisions or {}).get(did, "")
        types = ", ".join(sorted({_ext(p) for p in paths}))

        tbl.setItem(r, 0, QTableWidgetItem(str(did)))
        tbl.setItem(r, 1, QTableWidgetItem(str(rev)))
        tbl.setItem(r, 2, QTableWidgetItem(types))

        combo = QComboBox(dlg)
        combo.addItem("<Manual selection later>", "")
        for p in paths:
            combo.addItem(f"{_ext(p)} | {_path_text(p, source_root)}", str(p))

        # Default to PDF if there is exactly one PDF candidate; otherwise leave manual.
        pdf_indexes = [i + 1 for i, p in enumerate(paths) if p.suffix.lower() == ".pdf"]
        if len(pdf_indexes) == 1:
            combo.setCurrentIndex(pdf_indexes[0])
        elif len(paths) == 1:
            combo.setCurrentIndex(1)

        tbl.setCellWidget(r, 3, combo)
        combos[did] = combo

    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
    root.addWidget(tbl, 1)

    def _apply_preferred():
        preferred = cmb_pref.currentData() or ""
        if not preferred:
            return
        for did in doc_ids:
            combo = combos.get(did)
            if not combo:
                continue
            matches = []
            for i in range(1, combo.count()):
                data = combo.itemData(i) or ""
                if data and _ext(Path(data)) == preferred:
                    matches.append(i)
            if len(matches) == 1:
                combo.setCurrentIndex(matches[0])

    btn_apply.clicked.connect(_apply_preferred)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
    root.addWidget(buttons)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec_() != QDialog.Accepted:
        return None

    result: Dict[str, Optional[Path]] = {}
    for did, combo in combos.items():
        data = combo.currentData() or ""
        result[did] = Path(data).resolve() if data else None
    return result
