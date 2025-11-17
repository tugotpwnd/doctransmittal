from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import shutil, re, os
from ..core.paths import company_library_root, resolve_company_library_path

try:
    from .db import (
        init_db, get_project,
        create_checkprint_batch, get_checkprint_items,
        get_latest_checkprint_versions, update_checkprint_item_status,
        append_checkprint_event, insert_transmittal, _retry_write, _connect, get_active_checkprint_batch,
        cancel_checkprint_batch, get_checkprint_batch,
)
    from .transmittal_service import _base_folder_for_output, _default_out_root, next_transmittal_number
except Exception:
    from ..services.db import (
        init_db, get_project,
        create_checkprint_batch, get_checkprint_items,
        get_latest_checkprint_versions, update_checkprint_item_status,
        append_checkprint_event, insert_transmittal,
    )
    from ..services.transmittal_service import _base_folder_for_output, _default_out_root, next_transmittal_number


def _checkprint_root(db_path: Path) -> Path:
    """
    CheckPrint root:
        <db_parent>/CheckPrint
    """
    base = _base_folder_for_output(db_path)
    root = base / "CheckPrint"
    root.mkdir(parents=True, exist_ok=True)
    return root



def _next_cp_code(db_path: Path) -> str:
    """
    Determine next CheckPrint code based on DB contents,
    NOT the filesystem, to ensure uniqueness.
    """
    con = _connect(db_path)
    rows = con.execute("""
        SELECT code FROM checkprint_batches
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    con.close()

    if not rows:
        return "CP-TRN-001"

    last_code = rows[0]  # e.g. 'CP-TRN-001'
    try:
        last_num = int(last_code.split("-")[-1])
    except Exception:
        last_num = 0

    return f"CP-TRN-{last_num + 1:03d}"



def _split_basename(name: str) -> (str, str):
    """
    Returns (base_without_ext_and_cp, extension_with_dot)
    """
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    # Strip existing _CP_N if present
    m = re.match(r"^(.*)_CP_(\d+)$", stem, re.IGNORECASE)
    if m:
        base = m.group(1)
    else:
        base = stem
    return base, (("." + ext) if ext else "")


def _safe_rename(src: Path, dst: Path) -> None:
    """
    Rename with a nicer error if the file is locked.
    """
    try:
        src.rename(dst)
    except PermissionError as e:
        raise RuntimeError(f"File appears to be in use and could not be renamed:\n{src}") from e
    except OSError as e:
        raise RuntimeError(f"Failed to rename:\n{src}\n→ {dst}\n\n{e}") from e


def start_checkprint_batch(
    db_path: Path,
    *,
    items: List[Dict[str, Any]],
    user_name: str,
    title: str,
    client: str,
    created_on_str: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Kick off a CheckPrint batch.

    items = [{doc_id, revision, file_path, ...}] as provided by FilesTab._build_snapshot_items.
    Renames source files to *_CP_N and copies them into CP folder, then records batch in DB.

    Returns dict with {'batch_id', 'code', 'dir'}.
    """
    db_path = Path(db_path)
    init_db(db_path)
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")
    project_code = proj["project_code"]
    project_id = proj["id"]

    active = get_active_checkprint_batch(db_path)
    if active:
        raise RuntimeError(
            f"Cannot start a new CheckPrint: batch {active['code']} is still {active['status']}."
        )

    cp_root = _checkprint_root(db_path)
    cp_code = _next_cp_code(db_path)
    batch_dir = cp_root / cp_code
    batch_dir.mkdir(parents=True, exist_ok=True)

    now = created_on_str or datetime.now().strftime("%Y-%m-%d")

    prepared_items: List[Dict[str, Any]] = []
    renames: List[tuple[Path, Path]] = []   # (new_path, old_path) for rollback

    try:
        for snap in items:
            doc_id = snap.get("doc_id")
            src = Path(snap.get("file_path") or "")
            if not doc_id or not src:
                continue
            if not src.exists():
                raise RuntimeError(f"Mapped file for {doc_id} does not exist:\n{src}")

            base_name, ext = _split_basename(src.name)
            cp_version = 1
            cp_suffix_name = f"{base_name}_CP_{cp_version}{ext}"

            # rename source in place
            dst_src = src.with_name(cp_suffix_name)
            _safe_rename(src, dst_src)
            renames.append((dst_src, src))

            # copy to CP folder
            dst_cp = batch_dir / cp_suffix_name
            shutil.copy2(str(dst_src), str(dst_cp))

            prepared_items.append({
                "doc_id": doc_id,
                "revision": snap.get("revision") or "",
                "base_name": base_name + ext,
                "cp_version": cp_version,
                "status": "pending",
                "submitter": user_name,
                "source_path": str(Path(dst_src).relative_to(company_library_root())),
                "cp_path": str(Path(dst_cp).relative_to(company_library_root())),
                "last_submitted_on": now,
            })

        if not prepared_items:
            raise RuntimeError("No valid mapped files to send for CheckPrint.")

        batch_id = create_checkprint_batch(
            db_path,
            project_id=project_id,
            code=cp_code,
            title=title or "",
            client=client or "",
            created_by=user_name or "",
            created_on=now,
            items=prepared_items,
        )

        # log events
        for it in get_checkprint_items(db_path, batch_id):
            append_checkprint_event(
                db_path,
                item_id=it["id"],
                actor=user_name,
                event="submitted",
                from_status=None,
                to_status="pending",
                note="Initial CheckPrint submission",
            )

        return {"batch_id": batch_id, "code": cp_code, "dir": str(batch_dir)}

    except Exception:
        # rollback renames if something blew up
        for new_path, old_path in reversed(renames):
            try:
                if new_path.exists():
                    new_path.rename(old_path)
            except OSError:
                pass
        raise
from pathlib import Path
from typing import Dict
from datetime import datetime
import shutil
from ..core.paths import company_library_root, resolve_company_library_path
from .db import get_checkprint_items, append_checkprint_event, _retry_write, _connect, init_db


def _apply_checkprint_update(
    db_path: Path,
    *,
    item: Dict,
    new_file: Path,
    submitter: str,
    mode: str,          # "overwrite" or "increment"
) -> None:
    """
    Shared logic for CheckPrint updates.

    mode = "overwrite"  → pending: overwrite same CP version
    mode = "increment"  → accepted/rejected: create new CP version
    """
    db_path = Path(db_path)
    new_file = Path(new_file)

    if not new_file.exists():
        raise RuntimeError(f"Replacement file not found:\n{new_file}")

    # Resolve current CP / source paths
    old_cp_abs = Path(resolve_company_library_path(item["cp_path"]))
    old_src_abs = Path(resolve_company_library_path(item["source_path"]))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    now_date = datetime.now().strftime("%Y-%m-%d")

    current_version = int(item.get("cp_version") or 1)

    if mode == "overwrite":
        # PENDING: overwrite same CP version
        new_version = current_version

        # Supersede *CP* file with timestamp
        superseded_dir = old_cp_abs.parent / "superseded"
        superseded_dir.mkdir(exist_ok=True)
        if old_cp_abs.exists():
            superseded_name = f"{old_cp_abs.stem}_SUPERSEDED_{ts}{old_cp_abs.suffix}"
            old_cp_abs.rename(superseded_dir / superseded_name)

        # Remove old source file (we only care about keeping current)
        if old_src_abs.exists():
            try:
                old_src_abs.unlink()
            except Exception:
                pass

    elif mode == "increment":
        # ACCEPTED / REJECTED: create new CP version
        new_version = current_version + 1

        # Do NOT touch the old CP_N – keep it in place
        # Only old source file is removed
        if old_src_abs.exists():
            try:
                old_src_abs.unlink()
            except Exception:
                pass

    else:
        raise ValueError(f"Unknown CheckPrint update mode: {mode}")

    # Build new filename: <doc>[_<rev>]_CP_<version><ext>
    doc_id = item["doc_id"]
    rev = item.get("revision") or ""
    base = f"{doc_id}_{rev}" if rev else doc_id
    new_name = f"{base}_CP_{new_version}{new_file.suffix}"

    new_cp_abs = old_cp_abs.parent / new_name
    new_src_abs = old_src_abs.parent / new_name

    # Copy new file into both locations
    new_cp_abs.parent.mkdir(parents=True, exist_ok=True)
    new_src_abs.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(str(new_file), str(new_src_abs))
    shutil.copy2(str(new_file), str(new_cp_abs))

    # DB paths relative to company library root
    rel_src = str(new_src_abs.relative_to(company_library_root()))
    rel_cp = str(new_cp_abs.relative_to(company_library_root()))

    old_status = item.get("status") or "pending"
    new_status = "pending"  # any resubmission returns to pending

    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        if mode == "overwrite":
            # CP version unchanged, status already pending
            cur.execute("""
                UPDATE checkprint_items
                   SET source_path=?,
                       cp_path=?,
                       submitter=?,
                       last_submitted_on=?
                 WHERE id=?
            """, (rel_src, rel_cp, submitter, now_date, int(item["id"])))
        else:
            # increment version and reset status/reviewer
            cur.execute("""
                UPDATE checkprint_items
                   SET source_path=?,
                       cp_path=?,
                       submitter=?,
                       cp_version=?,
                       status=?,
                       reviewer=NULL,
                       last_reviewer_note=NULL,
                       last_submitted_on=?
                 WHERE id=?
            """, (rel_src, rel_cp, submitter,
                  new_version, new_status, now_date, int(item["id"])))
        con.commit()
        con.close()

    _retry_write(_do)

    # Event log
    append_checkprint_event(
        db_path,
        item_id=int(item["id"]),
        actor=submitter,
        event="resubmitted",
        from_status=old_status,
        to_status=new_status if mode == "increment" else old_status,
        note="Document resubmitted by submitter",
    )


def overwrite_checkprint_items(
    db_path: Path,
    *,
    batch_id: int,
    item_id_to_new_path: Dict[int, Path],
    submitter: str,
) -> bool:
    """
    Pending case:
        • Overwrite same CP version
        • Supersede old CP file with timestamp
        • Replace source + CP with new file
    """
    db_path = Path(db_path)
    init_db(db_path)

    items = get_checkprint_items(db_path, batch_id)
    items_by_id = {int(it["id"]): it for it in items}

    for item_id, new_path in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it:
            continue
        _apply_checkprint_update(
            db_path,
            item=it,
            new_file=Path(new_path),
            submitter=submitter,
            mode="overwrite",
        )

    return True


def resubmit_checkprint_items(
    db_path: Path,
    *,
    batch_id: int,
    item_id_to_new_path: Dict[int, Path],
    submitter: str,
) -> bool:
    """
    Accepted / rejected case:
        • Increment CP version
        • Keep old CP_N in place (no supersede)
        • Replace source file with new version
        • New CP_N+1 created
    """
    db_path = Path(db_path)
    init_db(db_path)

    items = get_checkprint_items(db_path, batch_id)
    items_by_id = {int(it["id"]): it for it in items}

    for item_id, new_path in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it:
            continue
        _apply_checkprint_update(
            db_path,
            item=it,
            new_file=Path(new_path),
            submitter=submitter,
            mode="increment",
        )

    return True





def finalize_checkprint_to_transmittal(
    db_path: Path,
    *,
    batch_id: int,
    reviewer: str,
    out_root: Optional[Path] = None,
) -> Path:
    """
    For reviewer: once ALL docs in a batch are accepted, build the actual transmittal.

    - Renames source files back to their original base names (drop _CP_N).
    - Allocates next transmittal number.
    - Inserts transmittal header/items into DB.
    - Copies final PDFs into Transmittals/<PROJECT>-TRN-###/Files.
    Returns the transmittal directory path.
    """
    db_path = Path(db_path)
    init_db(db_path)
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")
    project_code = proj["project_code"]

    # Load batch + items
    con = _connect(db_path)
    batch_row = con.execute("""
        SELECT id, project_id, code, title, client, created_by, created_on, status
          FROM checkprint_batches WHERE id=?
    """, (int(batch_id),)).fetchone()
    con.close()
    if not batch_row:
        raise RuntimeError("CheckPrint batch not found.")
    _, project_id, code, title, client, created_by, created_on, status = batch_row

    items = get_checkprint_items(db_path, batch_id)
    if not items:
        raise RuntimeError("No items in CheckPrint batch.")

    if any(it["status"] != "accepted" for it in items):
        raise RuntimeError("All documents must be accepted before creating the transmittal.")

    # rename sources back (drop _CP_N)
    renames: List[tuple[Path, Path]] = []
    try:
        for it in items:
            src = resolve_company_library_path(it["source_path"])
            src = Path(src)
            if not src.exists():
                raise RuntimeError(f"Source CheckPrint file missing:\n{src}")
            base, ext = _split_basename(src.name)
            # base is original stem, so final name is base + ext
            final_name = base + Path(it["base_name"]).suffix
            dst = src.with_name(final_name)
            if dst.exists() and dst != src:
                # gently clobber: rename existing to *_pre_cp if needed
                backup = dst.with_name(dst.stem + "_pre_cp" + dst.suffix)
                dst.rename(backup)
            _safe_rename(src, dst)
            renames.append((dst, src))  # record for info; we don't roll back on success

        # Now create the transmittal using current (de-suffixed) sources
        out_root = out_root or _default_out_root(db_path)
        out_root.mkdir(parents=True, exist_ok=True)

        number = next_transmittal_number(project_code, out_root)
        header = {
            "project_code": project_code,
            "number": number,
            "title": title,
            "client": client,
            "created_by": reviewer or created_by,
            "created_on": created_on,
        }

        # Build items list for insert_transmittal
        trans_items: List[Dict[str, Any]] = []
        for it in items:
            src_cp = Path(it["source_path"])
            base, ext = _split_basename(src_cp.name)
            final_name = base + Path(it["base_name"]).suffix
            final_src = src_cp.with_name(final_name)
            trans_items.append({
                "doc_id": it["doc_id"],
                "doc_type": "",  # will be filled from live register snapshot
                "revision": it.get("revision") or "",
                "file_path": str(final_src),
            })

        tid = insert_transmittal(db_path, header, trans_items)

        # Create transmittal folder structure and copy files
        trans_dir = out_root / f"{project_code}-TRN-{int(number.split('-')[-1]):03d}"
        files_dir = trans_dir / "Files"
        files_dir.mkdir(parents=True, exist_ok=True)

        for it in trans_items:
            src = Path(it["file_path"])
            dst = files_dir / src.name
            shutil.copy2(str(src), str(dst))

        # mark batch completed
        def _do():
            con = _connect(db_path); cur = con.cursor()
            cur.execute("""
                UPDATE checkprint_batches
                   SET status='completed',
                       submitted_on=datetime('now'),
                       reviewer=?,
                       reviewer_notes=COALESCE(reviewer_notes,'')
                 WHERE id=?
            """, (reviewer or created_by, int(batch_id)))
            con.commit(); con.close()
        _retry_write(_do)

        return trans_dir

    except Exception:
        # We don't attempt rollback of the de-suffixed names here – at that point user intervention is safer.
        raise


def cancel_checkprint(db_path: Path, batch_id: int):
    """
    Cancels a CheckPrint:
      • Marks batch + items as cancelled in DB
      • Reverts source files to original names (removes _CP_N)
      • Renames CP folder to CANCELLED-<name>
      • Keeps all CP files for audit/markup retention
    """
    from ..services.db import cancel_checkprint_batch

    # --- 1. Fetch batch
    batch = get_checkprint_batch(db_path, batch_id)
    if batch and batch["status"] == "cancelled":
        return True  # Already cancelled

    # --- 2. Resolve CP folder
    # cp_path in DB is relative → convert to absolute
    cp_file_path = Path(resolve_company_library_path(batch["cp_path"]))
    cp_dir = cp_file_path.parent

    # --- 3. Revert source files (remove _CP_N only if present)
    con = _connect(db_path)
    cur = con.cursor()
    items = [
        {"id": r[0], "source_path": r[1]}
        for r in cur.execute("""
            SELECT id, source_path
              FROM checkprint_items
             WHERE batch_id=?
        """, (batch_id,))
    ]
    con.close()

    for it in items:
        src_rel = it["source_path"]
        abs_src = Path(resolve_company_library_path(src_rel))

        if abs_src.exists() and "_CP_" in abs_src.name:
            original_name = abs_src.name.split("_CP_")[0] + abs_src.suffix
            try:
                abs_src.rename(abs_src.with_name(original_name))
            except Exception:
                # Non-fatal: keep going, as it may already be reverted or read-only
                pass

    # --- 4. Rename CP folder to CANCELLED-<name>
    cancelled_dir = cp_dir.with_name(f"CANCELLED-{cp_dir.name}")
    try:
        cp_dir.rename(cancelled_dir)
    except Exception:
        # If rename fails (folder open, permissions), still continue
        pass

    # --- 5. Mark DB entries cancelled
    cancel_checkprint_batch(db_path, batch_id)

    return True
