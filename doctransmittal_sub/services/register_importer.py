# services/register_importer.py
from __future__ import annotations
from pathlib import Path
from typing import Optional
from . import db as regdb
from .register_reader import read_register  # re-use existing Excel reader
from ..models.document import DocumentRow

def import_excel_register_to_db(
    excel_path: Path,
    db_path: Path,
    project_code: str,
    project_name: str,
    project_root: Optional[Path] = None,
) -> None:
    regdb.init_db(db_path)
    pid = regdb.upsert_project(db_path, project_code.strip(), project_name.strip(), str(project_root or excel_path.parent))
    rows = read_register(excel_path)  # your existing parser
    for r in rows:
        # r: DocumentRow(doc_id, doc_type, file_type, description, status, latest_rev_raw, latest_token, row_num)
        did = regdb.upsert_document(db_path, pid, {
            "doc_id": r.doc_id, "doc_type": r.doc_type, "file_type": r.file_type,
            "description": r.description, "status": r.status, "is_active": 1
        })
        if r.latest_rev_token:
            regdb.add_revision(db_path, did, r.latest_rev_token)
