# services/transmittal_service.py — create/edit/rebuild/purge transmittal bundles
from __future__ import annotations
from pathlib import Path
from datetime import date
from typing import List, Dict, Optional
import shutil, re

from ..services.db import (
    init_db, get_project, insert_transmittal, list_transmittals, get_transmittal_items,
    find_transmittal_id_by_number, delete_transmittal_by_id, soft_delete_transmittal,
    add_items_to_transmittal, remove_items_from_transmittal, update_transmittal_header
)
from ..services.receipt_pdf import export_transmittal_pdf

# ---------------- helpers ----------------

def _default_out_root(project_root: Optional[Path]) -> Path:
    return (project_root or Path.cwd()) / "Transmittals"

def next_transmittal_number(project_code: str, out_root: Path) -> str:
    out_root.mkdir(parents=True, exist_ok=True)
    pat = re.compile(rf"^{re.escape(project_code)}-TRN-(\d+)$", re.IGNORECASE)
    maxn = 0
    for p in out_root.iterdir():
        if not p.is_dir(): continue
        m = pat.match(p.name.strip())
        if m:
            try: maxn = max(maxn, int(m.group(1)))
            except: pass
    return f"{project_code}-TRN-{maxn+1:03d}"

# ---------------- core flows ----------------

def create_transmittal(
    db_path: Path,
    out_root: Optional[Path],
    user_name: str,
    title: str,
    client: str,
    items: List[Dict[str,str]],
    project_root: Optional[Path] = None
) -> Path:
    """
    items = [{doc_id, revision, file_path, (optional snapshot fields)}]
    """
    init_db(db_path)
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")
    project_code = proj["project_code"]
    out_root = out_root or _default_out_root(project_root)
    out_root.mkdir(parents=True, exist_ok=True)

    number = next_transmittal_number(project_code, out_root)
    header = {
        "project_code": project_code,
        "number": number,
        "title": title.strip(),
        "client": client.strip(),
        "created_by": user_name.strip(),
        "created_on": date.today().isoformat()
    }
    tid = insert_transmittal(db_path, header, items)
    return rebuild_transmittal_bundle(db_path, number, out_root, project_root)

def rebuild_transmittal_bundle(
    db_path: Path,
    transmittal_number: str,
    out_root: Optional[Path] = None,
    project_root: Optional[Path] = None
) -> Path:
    """
    Regenerates the on-disk folder and receipt PDF from the DB snapshot.
    """
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")

    out_root = out_root or _default_out_root(project_root)
    out_root.mkdir(parents=True, exist_ok=True)

    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError(f"Transmittal {transmittal_number} not found.")

    # Build folder layout
    trans_dir = out_root / transmittal_number
    files_dir = trans_dir / "Files"
    receipt_dir = trans_dir / "Receipt"
    # Clean Files, keep Receipt folder (we’ll overwrite the PDF)
    if files_dir.exists():
        shutil.rmtree(files_dir, ignore_errors=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)

    items = get_transmittal_items(db_path, tid)

    # Copy files if they still exist at those paths
    for it in items:
        src = (it.get("file_path") or "").strip()
        if not src: continue
        sp = Path(src)
        if sp.exists() and sp.is_file():
            try:
                shutil.copy2(sp, files_dir / sp.name)
            except Exception:
                pass

    header = [t for t in list_transmittals(db_path, include_deleted=True) if t["id"] == tid][0]
    pdf_path = receipt_dir / f"{transmittal_number}.pdf"
    export_transmittal_pdf(pdf_path, header, items)
    return trans_dir

# ---------------- edit / delete ----------------

def edit_transmittal_add_items(db_path: Path, transmittal_number: str, items: List[Dict[str,str]],
                               out_root: Optional[Path] = None, project_root: Optional[Path] = None) -> Path:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError("Transmittal not found.")
    add_items_to_transmittal(db_path, tid, items)
    return rebuild_transmittal_bundle(db_path, transmittal_number, out_root, project_root)

def edit_transmittal_remove_items(db_path: Path, transmittal_number: str, doc_ids: List[str],
                                  out_root: Optional[Path] = None, project_root: Optional[Path] = None) -> Path:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError("Transmittal not found.")
    remove_items_from_transmittal(db_path, tid, doc_ids)
    return rebuild_transmittal_bundle(db_path, transmittal_number, out_root, project_root)

def edit_transmittal_update_header(db_path: Path, transmittal_number: str, *, title: Optional[str] = None,
                                   client: Optional[str] = None,
                                   out_root: Optional[Path] = None, project_root: Optional[Path] = None) -> Path:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError("Transmittal not found.")
    update_transmittal_header(db_path, tid, title=title, client=client)
    return rebuild_transmittal_bundle(db_path, transmittal_number, out_root, project_root)

def soft_delete_transmittal_bundle(db_path: Path, transmittal_number: str, reason: str = "",
                                   out_root: Optional[Path] = None, project_root: Optional[Path] = None) -> bool:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        return False
    ok = soft_delete_transmittal(db_path, tid, reason=reason)
    # do not delete files on soft delete; it’s reversible
    return ok

def purge_transmittal_bundle(db_path: Path, transmittal_number: str,
                             out_root: Optional[Path] = None, project_root: Optional[Path] = None) -> bool:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        return False
    # delete folder
    out_root = out_root or _default_out_root(project_root)
    trans_dir = out_root / transmittal_number
    try:
        if trans_dir.exists():
            shutil.rmtree(trans_dir, ignore_errors=True)
    except Exception:
        pass
    # hard-delete rows
    return delete_transmittal_by_id(db_path, tid)
