from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import shutil, re, os
from ..core.paths import company_library_root, resolve_company_library_path
from ..core.settings import SettingsManager

from ..core.paths import company_library_root, resolve_company_library_path
from ..core.settings import SettingsManager

from .file_safety import (
    plan_copy,
    plan_rename,
    plan_delete,
    preflight_ops,
    execute_ops,
    PreflightError,
)


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

def _plan_checkprint_update_ops(
    item: Dict[str, Any],
    new_file: Path,
    *,
    mode: str,  # "overwrite" or "increment"
) -> tuple[list, Dict[str, Any]]:
    """
    Build the list of FileOps needed to update a single CheckPrint item,
    without touching the filesystem yet.

    Returns:
        (ops, meta)
        ops  = list of FileOp objects
        meta = {
            "new_src_abs": Path,
            "new_cp_abs": Path,
            "new_version": int,
            "old_status": str,
            "new_status": str,
        }
    """
    new_file = Path(new_file)
    if not new_file.exists():
        raise RuntimeError(f"Replacement file not found:\n{new_file}")

    old_cp_abs = Path(resolve_company_library_path(item["cp_path"]))
    old_src_abs = Path(resolve_company_library_path(item["source_path"]))

    ops = []

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    current_version = int(item.get("cp_version") or 1)

    if mode == "overwrite":
        new_version = current_version

        # Supersede old CP file
        if old_cp_abs.exists():
            superseded_dir = old_cp_abs.parent / "superseded"
            superseded_dir.mkdir(parents=True, exist_ok=True)
            superseded_name = f"{old_cp_abs.stem}_SUPERSEDED_{ts}{old_cp_abs.suffix}"
            superseded_path = superseded_dir / superseded_name
            ops.append(plan_rename(old_cp_abs, superseded_path))

        # Remove old source
        if old_src_abs.exists():
            ops.append(plan_delete(old_src_abs))

    elif mode == "increment":
        new_version = current_version + 1

        # Only remove old source; keep old CP_N in place
        if old_src_abs.exists():
            ops.append(plan_delete(old_src_abs))
    else:
        raise ValueError(f"Unknown CheckPrint update mode: {mode}")

    # Build new filename: <doc>[_<rev>]_CP_<version><ext>
    doc_id = item["doc_id"]
    rev = item.get("revision") or ""
    base = f"{doc_id}_{rev}" if rev else doc_id
    new_name = f"{base}_CP_{new_version}{new_file.suffix}"

    new_cp_abs = old_cp_abs.parent / new_name
    new_src_abs = old_src_abs.parent / new_name

    # Ensure parents exist (safe before preflight)
    new_cp_abs.parent.mkdir(parents=True, exist_ok=True)
    new_src_abs.parent.mkdir(parents=True, exist_ok=True)

    # Copy new file into both locations
    ops.append(plan_copy(new_file, new_src_abs))
    ops.append(plan_copy(new_file, new_cp_abs))

    old_status = item.get("status") or "pending"
    # Any resubmission returns to 'pending' in increment mode; overwrite keeps status
    new_status = "pending" if mode == "increment" else old_status

    meta = {
        "new_src_abs": new_src_abs,
        "new_cp_abs": new_cp_abs,
        "new_version": new_version,
        "old_status": old_status,
        "new_status": new_status,
    }
    return ops, meta



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

    This version is file-safe:
      - Preflight all ops first
      - Execute all file ops
      - Only then touch the DB
    """
    db_path = Path(db_path)
    new_file = Path(new_file)

    # Plan all file operations for this item
    ops, meta = _plan_checkprint_update_ops(item, new_file, mode=mode)

    # Preflight – fail fast, no changes yet
    ok, bad_path, reason = preflight_ops(ops)
    if not ok:
        raise PreflightError(bad_path, reason or "File operation preflight failed.")

    # Execute all ops (best-effort rollback on error)
    execute_ops(ops)

    # Compute new relative paths
    new_src_abs = meta["new_src_abs"]
    new_cp_abs = meta["new_cp_abs"]
    new_version = meta["new_version"]
    old_status = meta["old_status"]
    new_status = meta["new_status"]

    rel_src = str(new_src_abs.relative_to(company_library_root()))
    rel_cp = str(new_cp_abs.relative_to(company_library_root()))
    now_date = datetime.now().strftime("%Y-%m-%d")

    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        if mode == "overwrite":
            # CP version unchanged, status unchanged
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
    Pending case (batch-safe):
        • Overwrite same CP version
        • Supersede old CP file with timestamp
        • Replace source + CP with new file

    Atomic across the batch:
        - If any file op cannot proceed, nothing is changed.
    """
    db_path = Path(db_path)
    init_db(db_path)

    items = get_checkprint_items(db_path, batch_id)
    items_by_id = {int(it["id"]): it for it in items}

    all_ops = []
    per_item_meta: Dict[int, Dict[str, Any]] = {}
    now_date = datetime.now().strftime("%Y-%m-%d")

    # 1) Plan ops for all items
    for item_id, new_path in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it:
            continue
        ops, meta = _plan_checkprint_update_ops(it, Path(new_path), mode="overwrite")
        all_ops.extend(ops)
        per_item_meta[int(item_id)] = meta

    if not all_ops:
        return True  # nothing to do

    # 2) Preflight entire batch
    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        raise PreflightError(bad_path, reason or "File operation preflight failed.")

    # 3) Execute all file ops
    execute_ops(all_ops)

    # 4) DB updates + events
    for item_id, new_path in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it or item_id not in per_item_meta:
            continue

        meta = per_item_meta[item_id]
        new_src_abs = meta["new_src_abs"]
        new_cp_abs = meta["new_cp_abs"]
        old_status = meta["old_status"]
        new_status = meta["new_status"]  # same as old_status here

        rel_src = str(new_src_abs.relative_to(company_library_root()))
        rel_cp = str(new_cp_abs.relative_to(company_library_root()))

        def _do():
            con = _connect(db_path)
            cur = con.cursor()
            cur.execute("""
                UPDATE checkprint_items
                   SET source_path=?,
                       cp_path=?,
                       submitter=?,
                       last_submitted_on=?
                 WHERE id=?
            """, (rel_src, rel_cp, submitter, now_date, int(it["id"])))
            con.commit()
            con.close()

        _retry_write(_do)

        append_checkprint_event(
            db_path,
            item_id=int(it["id"]),
            actor=submitter,
            event="resubmitted",
            from_status=old_status,
            to_status=new_status,
            note="Document resubmitted by submitter",
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
    Accepted / rejected case (batch-safe):
        • Increment CP version
        • Keep old CP_N in place (no supersede)
        • Replace source file with new version
        • New CP_N+1 created

    Atomic across the batch.
    """
    db_path = Path(db_path)
    init_db(db_path)

    items = get_checkprint_items(db_path, batch_id)
    items_by_id = {int(it["id"]): it for it in items}

    all_ops = []
    per_item_meta: Dict[int, Dict[str, Any]] = {}
    now_date = datetime.now().strftime("%Y-%m-%d")

    # 1) Plan ops for all items
    for item_id, new_path in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it:
            continue
        ops, meta = _plan_checkprint_update_ops(it, Path(new_path), mode="increment")
        all_ops.extend(ops)
        per_item_meta[int(item_id)] = meta

    if not all_ops:
        return True  # nothing to do

    # 2) Preflight entire batch
    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        raise PreflightError(bad_path, reason or "File operation preflight failed.")

    # 3) Execute all file ops
    execute_ops(all_ops)

    # 4) DB updates + events
    for item_id, new_path in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it or item_id not in per_item_meta:
            continue

        meta = per_item_meta[item_id]
        new_src_abs = meta["new_src_abs"]
        new_cp_abs = meta["new_cp_abs"]
        new_version = meta["new_version"]
        old_status = meta["old_status"]
        new_status = meta["new_status"]  # "pending"

        rel_src = str(new_src_abs.relative_to(company_library_root()))
        rel_cp = str(new_cp_abs.relative_to(company_library_root()))

        def _do():
            con = _connect(db_path)
            cur = con.cursor()
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
                  new_version, new_status, now_date, int(it["id"])))
            con.commit()
            con.close()

        _retry_write(_do)

        append_checkprint_event(
            db_path,
            item_id=int(it["id"]),
            actor=submitter,
            event="resubmitted",
            from_status=old_status,
            to_status=new_status,
            note="Document resubmitted by submitter",
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
    FINALIZED WORKFLOW — FILE-SAFE VERSION

    ✔ Uses CP file as final approved file
    ✔ Removes _CP_N suffix for both source & transmittal
    ✔ Replaces source file with the approved version
    ✔ Copies final files into Transmittals/<TRN>/Files
    ✔ Preflights ALL file operations before touching DB
    """
    db_path = Path(db_path)
    init_db(db_path)

    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata not set in DB.")
    project_code = proj["project_code"]

    username = SettingsManager().get("user", "Maxwell Industries")

    # --- Load batch ----
    con = _connect(db_path)
    batch = con.execute("""
        SELECT id, project_id, code, title, client, created_by, created_on, status
        FROM checkprint_batches
        WHERE id=?
    """, (int(batch_id),)).fetchone()
    con.close()

    if not batch:
        raise RuntimeError("CheckPrint batch not found.")

    (_, project_id, code, title, client,
     created_by, created_on, status) = batch

    # --- Load items ----
    items = get_checkprint_items(db_path, batch_id)
    if not items:
        raise RuntimeError("No items in CheckPrint batch.")

    # Must ALL be accepted
    if any((it.get("status") or "").lower() != "accepted" for it in items):
        raise RuntimeError("All documents must be accepted before finalizing.")

    # --- Prepare output root & TRN folder ----
    out_root = out_root or _default_out_root(db_path)
    out_root.mkdir(parents=True, exist_ok=True)

    number = next_transmittal_number(project_code, out_root)

    trn_dir = out_root / f"{project_code}-TRN-{int(number.split('-')[-1]):03d}"
    files_dir = trn_dir / "Files"
    receipt_dir = trn_dir / "Receipt"
    files_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(created_on, dict):
        created_on_str = created_on.get("date", "")
    else:
        created_on_str = created_on

    username = SettingsManager().get("user.name", "")
    if not isinstance(username, str):
        username = str(username)

    # --- Header for DB + receipt ----
    header = {
        "project_code": project_code,
        "number": number,
        "title": title,
        "client": proj.get("client_company", ""),
        "end_user": proj.get("end_user", ""),
        "created_by": username,
        "created_on": created_on_str,
    }

    # --- Plan all file operations & build trans_items ----
    all_ops = []
    trans_items: List[Dict[str, Any]] = []

    for it in items:
        rel_cp = it["cp_path"]
        cp_abs = Path(resolve_company_library_path(rel_cp))
        if not cp_abs.exists():
            raise RuntimeError(f"Approved CP file missing:\n{cp_abs}")

        # final name in source dir (no _CP_N)
        base, ext = _split_basename(cp_abs.name)
        final_name = base + ext

        rel_source = it["source_path"]
        src_abs = Path(resolve_company_library_path(rel_source))
        src_final = src_abs.with_name(final_name)

        # 1) Copy CP → final source filename (this file WILL exist after ops run)
        all_ops.append(plan_copy(cp_abs, src_final))

        # 2) Copy final source → TRN Files (this is allowed because src_final will be created)
        dst_trn = files_dir / src_final.name
        all_ops.append(plan_copy(cp_abs, dst_trn))  # <-- IMPORTANT: cp_abs is the real source

        # 3) Delete old CP_N variants AFTER we know the new file will exist
        src_dir = src_abs.parent
        base_no_cp = base
        try:
            for f in src_dir.iterdir():
                fn = f.name
                if fn.startswith(base_no_cp + "_CP_"):
                    all_ops.append(plan_delete(f))
        except Exception:
            pass

        # 4) Record final source path for transmittal DB entry
        trans_items.append({
            "doc_id": it["doc_id"],
            "doc_type": "",
            "revision": it.get("revision") or "",
            "file_path": str(src_final),
        })

    if not all_ops:
        raise RuntimeError("No file operations planned for finalization.")

    # --- Preflight entire set of operations ----
    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        raise PreflightError(bad_path, reason or "File operation preflight failed.")

    # --- Execute all file ops ----
    execute_ops(all_ops)

    # --- Insert transmittal into DB ----
    # Normalize all header fields into strings (bulletproof)
    for k, v in header.items():
        if isinstance(v, dict):
            header[k] = json.dumps(v)  # or v.get("name", "") for created_by
        elif v is None:
            header[k] = ""
        else:
            header[k] = str(v)

    tid = insert_transmittal(db_path, header, trans_items)

    # --- Generate PDF receipt / bundle ----
    from .transmittal_service import rebuild_transmittal_bundle
    rebuild_transmittal_bundle(db_path, number, out_root=out_root)

    # --- Mark batch completed ----
    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        cur.execute("""
            UPDATE checkprint_batches
               SET status='completed',
                   submitted_on=datetime('now'),
                   reviewer=?,
                   reviewer_notes=COALESCE(reviewer_notes, '')
             WHERE id=?
        """, (username, int(batch_id)))
        con.commit()
        con.close()

    _retry_write(_do)

    return trn_dir



def cancel_checkprint(db_path: Path, *, batch_id: int, actor: str) -> bool:
    """
    Cancel a CheckPrint batch.

    Correct behaviour:
        • Restore ALL source files to their original names (remove _CP_N)
        • Leave CP files in their batch folder
        • Rename batch folder to *_cancelled_<timestamp>
        • Mark batch as cancelled in DB
        • Never delete source files
        • Never delete CP folder
    """

    if batch_id is None:
        raise ValueError("No batch_id provided to cancel_checkprint().")

    batch_id = int(batch_id)

    db_path = Path(db_path)
    init_db(db_path)

    # Load batch row (to get folder name)
    batch = get_checkprint_batch(db_path, batch_id)
    if not batch:
        raise RuntimeError("Cannot cancel: Batch not found.")

    batch_code = batch["code"]          # e.g. CP-TRN-004
    cp_root = _checkprint_root(db_path)  # CheckPrint root folder
    batch_dir = cp_root / batch_code     # <CheckPrint>/CP-TRN-XXX

    batch_items = get_checkprint_items(db_path, batch_id)
    if not batch_items:
        raise RuntimeError("Cannot cancel: Batch contains no documents.")

    all_ops = []

    # --- Restore original source files ---
    for it in batch_items:
        src_abs = Path(resolve_company_library_path(it["source_path"]))
        base, ext = _split_basename(src_abs.name)  # removes _CP_N
        final_name = base + ext                    # restored name
        src_final = src_abs.with_name(final_name)

        # Rename _CP_N file back to original
        if src_abs.exists():
            all_ops.append(plan_rename(src_abs, src_final))

    # --- Rename batch folder to mark cancelled ---
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    cancelled_dir = batch_dir.with_name(f"{batch_code}_cancelled_{ts}")

    if batch_dir.exists():
        all_ops.append(plan_rename(batch_dir, cancelled_dir))

    # --- Preflight ---
    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        raise PreflightError(bad_path, reason or "CheckPrint cancellation aborted due to locked file.")

    # --- Execute ---
    execute_ops(all_ops)

    # --- DB update ---
    _mark_checkprint_cancelled(db_path, batch_id, actor)

    return True


def _mark_checkprint_cancelled(db_path: Path, batch_id: int, actor: str):
    """Internal helper to update CheckPrint batch state."""
    db_path = Path(db_path)

    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        cur.execute("""
            UPDATE checkprint_batches
               SET status='cancelled',
                   reviewer=?,
                   reviewer_notes=COALESCE(reviewer_notes, ''),
                   submitted_on=NULL
             WHERE id=?
        """, (actor, int(batch_id)))
        con.commit()
        con.close()

    _retry_write(_do)

    # No event log here — batch-level cancellation does NOT apply to item-level history.
