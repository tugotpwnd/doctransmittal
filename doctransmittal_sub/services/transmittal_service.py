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
    from .file_safety import plan_copy, plan_delete_tree, preflight_ops, execute_ops
except Exception:
    from ..services.db import (
        init_db, get_project, insert_transmittal, list_transmittals, get_transmittal_items,
        find_transmittal_id_by_number, delete_transmittal_by_id, soft_delete_transmittal,
        add_items_to_transmittal, remove_items_from_transmittal, update_transmittal_header
    )
    from ..services.receipt_pdf import export_transmittal_pdf
    from ..services.file_safety import plan_copy, plan_delete_tree, preflight_ops, execute_ops

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


def _native_archive_root(db_path: Path) -> Path:
    """
    Root folder for native/source file archives.

    Layout:
        <Doc Control>/Native Archives/<TRANSMITTAL_NUMBER>/
    """
    return _base_folder_for_output(db_path) / "Native Archives"


_NATIVE_ARCHIVE_EXCLUDED_EXTENSIONS = {".pdf"}

# Directory names that are generated output/history/archive locations and must
# not be scanned for native files. This is deliberately broader than only the
# app's current "Native Archives" folder name because users may select the
# project root and that root may contain legacy archive folders.
_NATIVE_ARCHIVE_SKIP_DIR_NAMES = {
    "transmittals",
    "transmittal",
    "transmittal archive",
    "transmittal archives",
    "checkprint",
    "check print",
    "check prints",
    "native archives",
    "native archive",
    "native_archives",
    "native_archive",
    "native-archives",
    "native-archive",
    "archive",
    "archives",
    "archived",
    "_archive",
    "_archives",
    "document archive",
    "document archives",
    "issue archive",
    "issue archives",
    "issued archive",
    "issued archives",
}


def _normalise_skip_dir_name(value: object) -> str:
    """Normalise a folder name for skip-list comparison."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


_NATIVE_ARCHIVE_SKIP_DIR_KEYS = {
    _normalise_skip_dir_name(x) for x in _NATIVE_ARCHIVE_SKIP_DIR_NAMES
}


def _is_native_archive_skipped_dir(path: Path) -> bool:
    """Return True when a directory should not be traversed for natives."""
    try:
        return _normalise_skip_dir_name(path.name) in _NATIVE_ARCHIVE_SKIP_DIR_KEYS
    except Exception:
        return False


def _path_has_skipped_archive_part(path: Path) -> bool:
    """Backward-compatible helper retained for any local references."""
    try:
        return any(_normalise_skip_dir_name(part) in _NATIVE_ARCHIVE_SKIP_DIR_KEYS for part in path.parts)
    except Exception:
        return False


def _iter_native_source_files(source_root: Path):
    """
    Yield files under source_root while pruning generated/archive folders.

    This avoids a common failure mode where the source folder is the project
    root and contains previous Transmittals / Native Archives / Archive folders.
    Using an explicit stack rather than rglob lets us skip entire subtrees.
    """
    stack = [Path(source_root)]
    while stack:
        folder = stack.pop()
        try:
            entries = list(folder.iterdir())
        except Exception:
            continue

        for entry in entries:
            try:
                if entry.is_dir():
                    if _is_native_archive_skipped_dir(entry):
                        continue
                    stack.append(entry)
                    continue
                if entry.is_file():
                    yield entry
            except Exception:
                continue


def _normalise_docid(value: object) -> str:

    return str(value or "").strip()


def _native_archive_dir(db_path: Path, transmittal_number: str) -> Path:
    return _native_archive_root(db_path) / str(transmittal_number or "").strip()


def _safe_archive_destination(dst_dir: Path, filename: str, reserved: set[str]) -> Path:
    """Return a unique destination path, suffixing duplicate names as -N."""
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = dst_dir / f"{base}{ext}"
    key = candidate.name.casefold()
    if key not in reserved and not candidate.exists():
        reserved.add(key)
        return candidate

    n = 2
    while True:
        candidate = dst_dir / f"{base}-{n}{ext}"
        key = candidate.name.casefold()
        if key not in reserved and not candidate.exists():
            reserved.add(key)
            return candidate
        n += 1


def _path_has_skipped_archive_part(path: Path) -> bool:
    return any(part.casefold() in _NATIVE_ARCHIVE_SKIP_DIR_NAMES for part in path.parts)


def _find_native_archive_candidates(source_root: Path, doc_ids: list[str]) -> list[Path]:
    """
    Find native/source files for selected documents.

    Matching is intentionally strict:
      - file stem must equal the document ID exactly, case-insensitively
      - revision issue files such as DOCID_A.pdf or DOCID_A.dwg are not matched
      - PDFs are excluded because issued PDFs belong in the transmittal Files folder
      - generated/archive folders are pruned so previous archives are not re-archived
    """
    source_root = Path(source_root)
    if not source_root.exists() or not source_root.is_dir():
        return []

    wanted = {d.casefold() for d in (_normalise_docid(x) for x in doc_ids) if d}
    if not wanted:
        return []

    found: list[Path] = []
    for p in _iter_native_source_files(source_root):
        try:
            if p.suffix.casefold() in _NATIVE_ARCHIVE_EXCLUDED_EXTENSIONS:
                continue
            if p.stem.casefold() in wanted:
                found.append(p.resolve())
        except Exception:
            continue

    found.sort(key=lambda x: (x.stem.casefold(), x.suffix.casefold(), x.name.casefold(), str(x.parent).casefold()))
    return found


def rebuild_native_archive_for_transmittal(

    db_path: Path,
    transmittal_number: str,
    source_root: Optional[Path],
    items: List[Dict[str, str]],
) -> Optional[Path]:
    """
    Rebuild Native Archives/<transmittal_number>/ from the active source root.

    This is used for new transmittals only at this stage. Remap integration is
    intentionally not wired in until the remap workflow is confirmed.
    """
    if not source_root:
        return None
    source_root = Path(source_root)
    if not source_root.exists() or not source_root.is_dir():
        return None

    doc_ids = [_normalise_docid(it.get("doc_id")) for it in (items or [])]
    candidates = _find_native_archive_candidates(source_root, doc_ids)

    archive_dir = _native_archive_dir(db_path, transmittal_number)

    delete_ops = []
    if archive_dir.exists():
        delete_ops.append(plan_delete_tree(archive_dir))
        ok, bad_path, reason = preflight_ops(delete_ops)
        if not ok:
            raise RuntimeError(f"Could not clear native archive folder before rebuild:\n{bad_path}\n{reason}")
        execute_ops(delete_ops)

    archive_dir.mkdir(parents=True, exist_ok=True)

    if not candidates:
        try:
            print(f"[native-archive] No native files found for {transmittal_number} under {source_root}")
        except Exception:
            pass
        return archive_dir

    reserved: set[str] = set()
    copy_ops = []
    for src in candidates:
        dst = _safe_archive_destination(archive_dir, src.name, reserved)
        copy_ops.append(plan_copy(src, dst))

    ok, bad_path, reason = preflight_ops(copy_ops)
    if not ok:
        raise RuntimeError(f"Native archive preflight failed:\n{bad_path}\n{reason}")

    execute_ops(copy_ops)
    try:
        print(f"[native-archive] Copied {len(copy_ops)} file(s) -> {archive_dir}")
    except Exception:
        pass
    return archive_dir

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
    source_root: Optional[Path] = None,
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
    trans_dir = rebuild_transmittal_bundle(db_path, number, out_root)

    # Native/source file archive is driven from the same source folder used for
    # transmittal file matching. It is intentionally not run when no source
    # folder is supplied, such as service-driven rebuilds.
    if source_root:
        rebuild_native_archive_for_transmittal(db_path, number, source_root, items)

    return trans_dir


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
    native_dir = _native_archive_dir(db_path, transmittal_number)

    ops = []
    if trans_dir.exists():
        ops.append(plan_delete_tree(trans_dir))
    if native_dir.exists():
        ops.append(plan_delete_tree(native_dir))

    if ops:
        ok, bad_path, reason = preflight_ops(ops)
        if not ok:
            try:
                print(f"[transmittal] Purge preflight failed for {bad_path}: {reason}")
            except Exception:
                pass
            return False
        try:
            execute_ops(ops)
        except Exception as e:
            try:
                print(f"[transmittal] Purge failed: {e}")
            except Exception:
                pass
            return False

    try:
        delete_transmittal_by_id(db_path, tid)
    except Exception:
        pass

    db_gone = (find_transmittal_id_by_number(db_path, transmittal_number) is None)
    dirs_gone = (not trans_dir.exists()) and (not native_dir.exists())

    return bool(dirs_gone) and bool(db_gone)

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

