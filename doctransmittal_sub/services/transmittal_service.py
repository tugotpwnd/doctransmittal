from __future__ import annotations
from pathlib import Path
from datetime import date, datetime
from typing import List, Dict, Optional
import shutil, re

# Robust imports across package layouts
try:
    from .db import (
        init_db, get_project, insert_transmittal, list_transmittals, get_transmittal_items,
        find_transmittal_id_by_number, delete_transmittal_by_id, soft_delete_transmittal,
        add_items_to_transmittal, remove_items_from_transmittal, update_transmittal_header
    )
    from .receipt_pdf import export_transmittal_pdf
except Exception:
    from ..services.db import (
        init_db, get_project, insert_transmittal, list_transmittals, get_transmittal_items,
        find_transmittal_id_by_number, delete_transmittal_by_id, soft_delete_transmittal,
        add_items_to_transmittal, remove_items_from_transmittal, update_transmittal_header
    )
    from ..services.receipt_pdf import export_transmittal_pdf

# ---------------- helpers ----------------

# NEW: accept DD/MM/YYYY, DD/MM/YYYY HH:MM, ISO date/datetime
from datetime import datetime, date
from typing import Optional

def _normalize_created_on(s: Optional[str]) -> str:
    """
    Normalize various date formats to DD-MM-YYYY or DD-MM-YYYY HH:MM.
    Accepts:
      - DD/MM/YYYY
      - DD/MM/YYYY HH:MM
      - YYYY-MM-DD
      - YYYY-MM-DD HH:MM
    Returns:
      String formatted as DD-MM-YYYY (or DD-MM-YYYY HH:MM if time present).
    """
    s = (s or "").strip()
    if not s:
        return date.today().strftime("%d-%m-%Y")

    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            # Keep time if it existed in input
            if "%H:%M" in fmt:
                return dt.strftime("%d-%m-%Y %H:%M")
            else:
                return dt.strftime("%d-%m-%Y")
        except Exception:
            continue

    # Fallback to today's date if parsing fails
    return date.today().strftime("%d-%m-%Y")


def _base_folder_for_output(db_path: Path) -> Path:
    """
    Put 'Transmittals' one level up from the DB file.

    If DB is under a dot-folder ('.docutrans'), go up an extra level.
    """
    db_path = Path(db_path).resolve()
    parent = db_path.parent
    if parent.name.startswith("."):
        parent = parent.parent
    return parent

def _default_out_root(db_path: Path) -> Path:
    return _base_folder_for_output(db_path) / "Transmittals"

def _last_transmittal_number(project_code: str, out_root: Path) -> int:
    pat = re.compile(rf"^{re.escape(project_code)}-TRN-(\d+)$", re.IGNORECASE)
    maxn = 0
    for p in out_root.iterdir():
        if not p.is_dir():
            continue
        m = pat.match(p.name)
        if m:
            try:
                maxn = max(maxn, int(m.group(1)))
            except ValueError:
                continue
    return maxn


def next_transmittal_number(project_code: str, out_root: Path) -> str:
    out_root.mkdir(parents=True, exist_ok=True)
    last_used = max(1, _last_transmittal_number(project_code, out_root))
    candidate = f"{project_code}-TRN-{last_used:03d}"
    if not (out_root / candidate).exists():
        return candidate
    return f"{project_code}-TRN-{last_used + 1:03d}"

# NEW: CheckPrint root helper lives here to avoid circular imports
def _checkprint_root(db_path: Path) -> Path:
    """
    Root folder for all CheckPrint sessions for a given project.

    Resulting path:
        <Doc Control>/CheckPrint
    e.g.  .../Doc Control/CheckPrint
    """
    base = _base_folder_for_output(db_path)
    root = base / "CheckPrint"
    root.mkdir(parents=True, exist_ok=True)
    return root


def reserve_transmittal_for_checkprint(
    db_path: Path,
    out_root: Optional[Path] = None,
) -> tuple[str, Path, Path]:
    """
    Reserves the next transmittal number for a CheckPrint session.

    - Chooses the next transmittal number using the existing Transmittals folder.
    - Creates a placeholder folder:
          <Doc Control>/Transmittals/<transmittal_number>
      so that number is "burnt" and later calls will move to the next TRN.
    - Creates a CheckPrint folder:
          <Doc Control>/CheckPrint/CP-<transmittal_number>

    Returns:
        (transmittal_number, checkprint_dir, transmittal_dir)
    """
    init_db(db_path)
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")
    project_code = proj["project_code"]

    # Normal transmittal root (e.g. <Doc Control>/Transmittals)
    trans_root = out_root or _default_out_root(db_path)
    trans_root.mkdir(parents=True, exist_ok=True)

    # Get the next project_code-TRN-00N
    transmittal_number = next_transmittal_number(project_code, trans_root)

    # Placeholder transmittal folder so the number is reserved
    trans_dir = trans_root / transmittal_number
    trans_dir.mkdir(parents=True, exist_ok=True)

    # CheckPrint root and folder: <Doc Control>/CheckPrint/CP-<TRN>
    cp_root = _checkprint_root(db_path)
    cp_dir = cp_root / f"CP-{transmittal_number}"
    cp_dir.mkdir(parents=True, exist_ok=True)

    return transmittal_number, cp_dir, trans_dir



# ---------------- core flows ----------------

def create_transmittal(
    db_path: Path,
    out_root: Optional[Path],
    user_name: str,
    title: str,
    client: str,
    items: List[Dict[str, str]],
    created_on_str: Optional[str] = None,
    transmittal_number: Optional[str] = None,
) -> Path:
    """
    items = [{doc_id, revision, file_path, (optional snapshot fields)}]

    If 'transmittal_number' is provided, that value is used directly
    (for example, when finalising a CheckPrint that already reserved TRN-00N).
    Otherwise, the next available transmittal number is chosen.
    """
    init_db(db_path)
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")
    project_code = proj["project_code"]

    out_root = out_root or _default_out_root(db_path)
    out_root.mkdir(parents=True, exist_ok=True)

    # Use reserved TRN if supplied, otherwise allocate a new one
    number = transmittal_number or next_transmittal_number(project_code, out_root)

    header = {
        "project_code": project_code,
        "number": number,
        "title": title.strip(),
        "client": client.strip(),
        "created_by": user_name.strip(),
        "created_on": _normalize_created_on(created_on_str),
    }
    insert_transmittal(db_path, header, items)
    return rebuild_transmittal_bundle(db_path, number, out_root)


def rebuild_transmittal_bundle(
    db_path: Path,
    transmittal_number: str,
    out_root: Optional[Path] = None,
) -> Path:
    """
    Regenerates the on-disk folder and receipt PDF from the DB snapshot.
    """
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")

    out_root = out_root or _default_out_root(db_path)
    out_root.mkdir(parents=True, exist_ok=True)

    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError(f"Transmittal {transmittal_number} not found.")

    # Folder layout
    trans_dir = out_root / transmittal_number
    files_dir = trans_dir / "Files"
    receipt_dir = trans_dir / "Receipt"

    # Rebuild Files folder (keep Receipt; overwrite PDF anyway)
    if files_dir.exists():
        shutil.rmtree(files_dir, ignore_errors=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)

    items = get_transmittal_items(db_path, tid)

    # Copy files that still exist
    copy_errors = []
    copied = 0
    for it in items:
        # accept both 'file_path' (preferred) and legacy 'path'
        src = (it.get("file_path") or it.get("path") or "").strip()
        if not src:
            # carry useful context in the error report
            copy_errors.append(f"{it.get('doc_id','?')} Rev {it.get('revision','?')}: no file mapped")
            continue

        sp = Path(src)
        if not (sp.exists() and sp.is_file()):
            copy_errors.append(f"{it.get('doc_id','?')} Rev {it.get('revision','?')}: missing -> {src}")
            continue

        try:
            dst = files_dir / sp.name
            # ensure parent exists (paranoia; files_dir was created above)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dst)
            copied += 1
        except Exception as e:
            copy_errors.append(f"{it.get('doc_id','?')} Rev {it.get('revision','?')}: {type(e).__name__}: {e}")

    # minimal console visibility (so you can see what happened in the run output)
    try:
        print(f"[transmittal] Copied {copied} file(s) → {files_dir}")
        if copy_errors:
            print("[transmittal] Copy issues:")
            for msg in copy_errors:
                print(" -", msg)
    except Exception:
        pass

    header = [t for t in list_transmittals(db_path, include_deleted=True) if t["id"] == tid][0]

    # --- Add these two lines ---
    header["db_path"] = str(db_path)  # let receipt_pdf find DM-Logos via list_logos()
    header["_pdf_out_path"] = str(receipt_dir)  # optional; helps fallback search

    pdf_path = receipt_dir / f"{transmittal_number}.pdf"
    export_transmittal_pdf(pdf_path, header, items)
    return trans_dir

# --- NEW: targeted rebuild helpers -------------------------------------------

def rebuild_files_only(
    db_path: Path,
    transmittal_number: str,
    out_root: Optional[Path] = None,
) -> Path:
    """Rebuild the Files/ folder only. Do NOT regenerate the receipt PDF."""
    init_db(db_path)
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError(f"Transmittal {transmittal_number} not found.")

    out_root = out_root or _default_out_root(db_path)
    trans_dir = out_root / transmittal_number
    files_dir = trans_dir / "Files"
    receipt_dir = trans_dir / "Receipt"   # keep structure stable

    # clear and recreate Files; keep /Receipt untouched
    if files_dir.exists():
        shutil.rmtree(files_dir, ignore_errors=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)

    items = get_transmittal_items(db_path, tid) or []

    copied, copy_errors = 0, []
    for it in items:
        src = (it.get("file_path") or it.get("path") or "").strip()
        if not src:
            copy_errors.append(f"{it.get('doc_id','?')} Rev {it.get('revision','?')}: no file mapped")
            continue
        sp = Path(src)
        if not (sp.exists() and sp.is_file()):
            copy_errors.append(f"{it.get('doc_id','?')} Rev {it.get('revision','?')}: missing -> {src}")
            continue
        try:
            dst = files_dir / sp.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dst)
            copied += 1
        except Exception as e:
            copy_errors.append(f"{it.get('doc_id','?')} Rev {it.get('revision','?')}: {type(e).__name__}: {e}")

    try:
        print(f"[transmittal] (files-only) Copied {copied} file(s) → {files_dir}")
        if copy_errors:
            print("[transmittal] Copy issues:")
            for msg in copy_errors:
                print(" -", msg)
    except Exception:
        pass

    return trans_dir


def rebuild_receipt_only(
    db_path: Path,
    transmittal_number: str,
    out_root: Optional[Path] = None,
) -> Path:
    """Reprint the receipt PDF only. Do NOT touch the Files/ folder."""
    init_db(db_path)
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError(f"Transmittal {transmittal_number} not found.")

    out_root = out_root or _default_out_root(db_path)
    trans_dir = out_root / transmittal_number
    receipt_dir = trans_dir / "Receipt"
    receipt_dir.mkdir(parents=True, exist_ok=True)

    items = get_transmittal_items(db_path, tid) or []
    header = [t for t in list_transmittals(db_path, include_deleted=True) if t["id"] == tid][0]
    header["db_path"] = str(db_path)
    header["_pdf_out_path"] = str(receipt_dir)

    pdf_path = receipt_dir / f"{transmittal_number}.pdf"
    export_transmittal_pdf(pdf_path, header, items)
    try:
        print(f"[transmittal] (receipt-only) Wrote {pdf_path}")
    except Exception:
        pass
    return trans_dir


# ---------------- edit / delete ----------------

def edit_transmittal_add_items(
    db_path: Path,
    transmittal_number: str,
    items: List[Dict[str, str]],
    out_root: Optional[Path] = None,
) -> Path:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError("Transmittal not found.")
    add_items_to_transmittal(db_path, tid, items)
    return rebuild_transmittal_bundle(db_path, transmittal_number, out_root)

def edit_transmittal_remove_items(
    db_path: Path,
    transmittal_number: str,
    doc_ids: List[str],
    out_root: Optional[Path] = None,
) -> Path:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError("Transmittal not found.")
    remove_items_from_transmittal(db_path, tid, doc_ids)
    return rebuild_transmittal_bundle(db_path, transmittal_number, out_root)

def edit_transmittal_update_header(
    db_path: Path,
    number: str,
    *,
    created_on_str: Optional[str] = None,
    title: Optional[str] = None,
    created_by: Optional[str] = None,
    client: Optional[str] = None
) -> bool:
    tid = find_transmittal_id_by_number(db_path, number)
    if tid is None:
        return False
    return update_transmittal_header(
        db_path, tid,
        title=title,
        client=client,
        created_on=created_on_str,
        created_by=created_by
    )

def soft_delete_transmittal_bundle(
    db_path: Path,
    transmittal_number: str,
    reason: str = "",
) -> bool:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        return False
    ok = soft_delete_transmittal(db_path, tid, reason=reason)
    return ok



import os, stat, time, shutil  # keep near top of file if not already imported

def _rmtree_force(path: Path, tries: int = 3, sleep_sec: float = 0.2) -> bool:
    """
    Robustly remove a directory tree on Windows (handles read-only files).
    Returns True if the path no longer exists.
    """
    path = Path(path)
    if not path.exists():
        return True

    def _onerror(func, p, exc_info):
        # Make read-only files writable then retry the failing func(path)
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    for _ in range(tries):
        try:
            shutil.rmtree(str(path), onerror=_onerror)
        except Exception:
            # swallow and retry
            pass
        if not path.exists():
            return True
        time.sleep(sleep_sec)
    return not path.exists()

def purge_transmittal_bundle(
    db_path: Path,
    transmittal_number: str,
    out_root: Optional[Path] = None,
) -> bool:
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        return False
    out_root = out_root or _default_out_root(db_path)
    trans_dir = out_root / transmittal_number

    # Force-delete the whole transmittal folder in one go.
    dir_gone = _rmtree_force(trans_dir)

    # Delete DB record (function returns None), then verify by lookup
    try:
        delete_transmittal_by_id(db_path, tid)
    except Exception:
        # we'll still verify via lookup below
        pass

    db_gone = (find_transmittal_id_by_number(db_path, transmittal_number) is None)

    return bool(dir_gone) and bool(db_gone)

def edit_transmittal_replace_items(
    db_path: Path,
    transmittal_number: str,
    items: List[Dict[str, str]],
    out_root: Optional[Path] = None,
) -> Path:
    """
    Replace ALL items … then rebuild on-disk.
    """
    init_db(db_path)
    tid = find_transmittal_id_by_number(db_path, transmittal_number)
    if tid is None:
        raise RuntimeError(f"Transmittal {transmittal_number} not found.")

    current = get_transmittal_items(db_path, tid) or []
    curr_ids = [(it.get("doc_id") or "").strip() for it in current if (it.get("doc_id") or "").strip()]
    if curr_ids:
        remove_items_from_transmittal(db_path, tid, curr_ids)

    if items:
        add_items_to_transmittal(db_path, tid, items)

    # OLD: rebuild_transmittal_bundle(db_path, transmittal_number, out_root)
    # NEW: files only (do NOT reprint receipt)
    return rebuild_files_only(db_path, transmittal_number, out_root)

