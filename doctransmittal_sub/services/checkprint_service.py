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
        _connect,
        _retry_write,
        append_checkprint_event,
        cancel_checkprint_batch,
        create_checkprint_batch,
        get_active_checkprint_batch,
        get_checkprint_batch,
        get_checkprint_items,
        get_checkprint_items_by_ids,
        get_latest_checkprint_versions,
        get_project,
        init_db,
        insert_checkprint_items,
        insert_transmittal,
        mark_checkprint_items_removed,
        update_checkprint_item_status,
    )
    from .transmittal_service import _base_folder_for_output, _default_out_root, next_transmittal_number
except Exception:
    from ..services.db import (
        _connect,
        _retry_write,
        append_checkprint_event,
        cancel_checkprint_batch,
        create_checkprint_batch,
        get_active_checkprint_batch,
        get_checkprint_batch,
        get_checkprint_items,
        get_checkprint_items_by_ids,
        get_latest_checkprint_versions,
        get_project,
        init_db,
        insert_checkprint_items,
        insert_transmittal,
        mark_checkprint_items_removed,
        update_checkprint_item_status,
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

def _checkprint_incoming_dir(db_path: Path) -> Path:
    """
    CheckPrint incoming folder for resubmissions ONLY:
        <CheckPrint>/_CheckPrintIncoming
    """
    incoming = _checkprint_root(db_path) / "_CheckPrintIncoming"
    incoming.mkdir(parents=True, exist_ok=True)
    return incoming


def _project_root(db_path: Path) -> Path:
    """
    Returns the root folder of the project.

    If DB = <ProjectRoot>/1.0 Doc Control/<db>.db
    then project root = <ProjectRoot>.
    """
    db_path = Path(db_path)
    return db_path.parent.parent



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
            proj_root = _project_root(db_path)

            prepared_items.append({
                "doc_id": doc_id,
                "revision": snap.get("revision") or "",
                "base_name": base_name + ext,
                "cp_version": cp_version,
                "status": "pending",
                "submitter": user_name,
                "source_path": str(Path(dst_src).relative_to(proj_root)),
                "cp_path": str(Path(dst_cp).relative_to(proj_root)),
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
    db_path: Path,
    item: Dict[str, Any],
    new_file: Path,
    *,
    mode: str,
) -> tuple[list, Dict[str, Any]]:

    new_file = Path(new_file)
    if not new_file.exists():
        raise RuntimeError(f"Replacement file not found:\n{new_file}")

    proj_root = _project_root(db_path)

    old_cp_abs = proj_root / item["cp_path"]
    old_src_abs = proj_root / item["source_path"]
    # ---------------------------------------------------------------

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

        # remove old source only; keep CP_N files
        if old_src_abs.exists():
            ops.append(plan_delete(old_src_abs))

    else:
        raise ValueError(f"Unknown CheckPrint update mode: {mode}")

    # Build new filename
    doc_id = item["doc_id"]
    rev = item.get("revision") or ""
    base = f"{doc_id}_{rev}" if rev else doc_id
    new_name = f"{base}_CP_{new_version}{new_file.suffix}"

    new_cp_abs = old_cp_abs.parent / new_name
    new_src_abs = old_src_abs.parent / new_name

    new_cp_abs.parent.mkdir(parents=True, exist_ok=True)
    new_src_abs.parent.mkdir(parents=True, exist_ok=True)

    ops.append(plan_copy(new_file, new_src_abs))
    ops.append(plan_copy(new_file, new_cp_abs))

    old_status = item.get("status") or "pending"
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
    ops, meta = _plan_checkprint_update_ops(db_path,item, new_file, mode=mode)

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

    proj_root = _project_root(db_path)
    rel_src = str(new_src_abs.relative_to(proj_root))
    rel_cp = str(new_cp_abs.relative_to(proj_root))
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
        ops, meta = _plan_checkprint_update_ops(db_path,it, Path(new_path), mode="overwrite")
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

        proj_root = _project_root(db_path)
        rel_src = str(new_src_abs.relative_to(proj_root))
        rel_cp = str(new_cp_abs.relative_to(proj_root))

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
    global _last_db_path_for_cp
    _last_db_path_for_cp = db_path
    """
    Increment-mode resubmission (accepted/rejected case):

        • Replacement file may come from ANYWHERE on the user's PC
        • Old CP_N is kept
        • New CP_(N+1) created inside project CheckPrint folder
        • Paths in DB stored relative to project root
        • Atomic across the batch
    """

    db_path = Path(db_path)
    init_db(db_path)

    # Load batch items
    items = get_checkprint_items(db_path, batch_id)
    items_by_id = {int(it["id"]): it for it in items}

    all_ops = []
    per_item_meta: Dict[int, Dict[str, Any]] = {}
    now_date = datetime.now().strftime("%Y-%m-%d")

    # 1) Plan operations for all items
    for item_id, replacement_file in item_id_to_new_path.items():
        it = items_by_id.get(int(item_id))
        if not it:
            continue

        # Use your existing planning mechanism — this already computes:
        #   new_src_abs (where the replacement will be copied)
        #   new_cp_abs  (the new CP_(N+1) path)
        #   new_version
        ops, meta = _plan_checkprint_update_ops(db_path,it, Path(replacement_file), mode="increment")

        # IMPORTANT: Replacement file is allowed to be anywhere. No relative_to checks
        # are applied to the replacement file. Only the TARGET paths matter.

        all_ops.extend(ops)
        per_item_meta[int(item_id)] = meta

    if not all_ops:
        return True  # nothing to do

    # 2) Preflight entire batch of file operations
    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        raise PreflightError(bad_path, reason or "File operation preflight failed.")

    # 3) Execute all file operations
    execute_ops(all_ops)

    # 4) DB write-back
    for item_id, replacement_file in item_id_to_new_path.items():
        if item_id not in per_item_meta:
            continue

        it = items_by_id[int(item_id)]
        meta = per_item_meta[item_id]

        new_src_abs = meta["new_src_abs"]   # actual path inside project
        new_cp_abs = meta["new_cp_abs"]     # new CP_(N+1) inside project
        new_version = meta["new_version"]
        old_status = meta["old_status"]
        new_status = meta["new_status"]

        proj_root = _project_root(db_path)

        # Store relative paths ONLY
        rel_src = str(new_src_abs.relative_to(proj_root))
        rel_cp = str(new_cp_abs.relative_to(proj_root))

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

def resubmit_all_incoming(
    db_path: Path,
    *,
    batch_id: int,
    actor: str,
) -> Dict[str, Any]:
    """
    Resubmit workflow (strict, controlled):

    - Incoming folder is ALWAYS: <CheckPrint>/_CheckPrintIncoming
    - Files MUST be named: DOCID_REV.pdf (strict)
    - Each incoming file must match EXACTLY ONE existing checkprint_items row
      by (doc_id, revision) within the given batch.
    - Mixed-mode in one pass:
        - pending   -> overwrite (same CP version)
        - accepted/rejected -> increment (new CP_(N+1))
    - Atomic for file ops across the batch (preflight all, then execute all)
    - Incoming files are deleted ONLY AFTER successful DB write-back.
    """
    db_path = Path(db_path)
    init_db(db_path)
    batch_id = int(batch_id)

    batch = get_checkprint_batch(db_path, batch_id)
    if not batch:
        return {"ok": False, "error": "batch_not_found"}
    if batch.get("status") in {"cancelled", "completed"}:
        return {"ok": False, "error": "batch_not_editable", "status": batch.get("status")}

    incoming_dir = _checkprint_incoming_dir(db_path)

    incoming_files = sorted(
        [p for p in incoming_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"],
        key=lambda p: p.name.lower(),
    )
    if not incoming_files:
        return {"ok": False, "error": "incoming_empty", "incoming_dir": str(incoming_dir)}

    items = get_checkprint_items(db_path, batch_id)
    items_by_id: Dict[int, Dict[str, Any]] = {int(it["id"]): it for it in items}

    # index by (doc_id, revision) -> item_id (must be unique)
    index: Dict[tuple[str, str], int] = {}
    for it in items:
        did = str(it.get("doc_id") or "").strip()
        rev = str(it.get("revision") or "").strip()
        if not did:
            continue
        key = (did, rev)
        if key in index:
            # This should not happen; fail safe.
            return {"ok": False, "error": "duplicate_item_key_in_batch", "doc_id": did, "revision": rev}
        index[key] = int(it["id"])

    # Parse + match incoming files
    matched: Dict[int, Path] = {}
    match_errors: List[Dict[str, Any]] = []

    for f in incoming_files:
        stem = f.stem
        if "_" not in stem:
            match_errors.append({"file": f.name, "error": "bad_name", "reason": "Expected DOCID_REV.pdf"})
            continue

        doc_id, rev = stem.rsplit("_", 1)
        doc_id = doc_id.strip()
        rev = rev.strip()

        if not doc_id or not rev:
            match_errors.append({"file": f.name, "error": "bad_name", "reason": "Expected DOCID_REV.pdf"})
            continue

        key = (doc_id, rev)
        item_id = index.get(key)
        if not item_id:
            match_errors.append({"file": f.name, "error": "no_match", "doc_id": doc_id, "revision": rev})
            continue

        if item_id in matched:
            match_errors.append({"file": f.name, "error": "duplicate_match", "item_id": item_id})
            continue

        matched[item_id] = f

    if match_errors:
        return {
            "ok": False,
            "error": "match_failed",
            "incoming_dir": str(incoming_dir),
            "details": match_errors,
        }

    # Plan file ops (atomic across batch)
    all_ops: List[Any] = []
    per_item_meta: Dict[int, Dict[str, Any]] = {}
    per_item_mode: Dict[int, str] = {}

    for item_id, replacement_file in matched.items():
        it = items_by_id.get(int(item_id))
        if not it:
            continue

        status = (it.get("status") or "").lower()

        if status == "pending":
            mode = "overwrite"
        else:
            mode = "increment"

        ops, meta = _plan_checkprint_update_ops(db_path, it, Path(replacement_file), mode=mode)

        all_ops.extend(ops)
        per_item_meta[int(item_id)] = meta
        per_item_mode[int(item_id)] = mode

    if not all_ops:
        return {"ok": True, "updated": 0, "overwritten": 0, "incremented": 0, "deleted_incoming": 0}

    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        return {"ok": False, "error": "preflight_failed", "path": str(bad_path), "reason": reason or ""}

    # Execute file ops first
    execute_ops(all_ops)

    # DB write-back
    now_date = datetime.now().strftime("%Y-%m-%d")
    proj_root = _project_root(db_path)

    overwritten = 0
    incremented = 0

    for item_id, src_path in matched.items():
        meta = per_item_meta.get(int(item_id))
        if not meta:
            continue
        it = items_by_id[int(item_id)]
        mode = per_item_mode[int(item_id)]

        new_src_abs = meta["new_src_abs"]
        new_cp_abs = meta["new_cp_abs"]

        rel_src = str(new_src_abs.relative_to(proj_root))
        rel_cp = str(new_cp_abs.relative_to(proj_root))

        old_status = (meta.get("old_status") or "").lower()

        if old_status == "accepted_minor":
            new_status = "accepted"
        elif mode == "increment":
            new_status = "pending"
        else:
            new_status = old_status

        if mode == "overwrite":
            def _do_overwrite():
                con = _connect(db_path)
                cur = con.cursor()
                cur.execute("""
                    UPDATE checkprint_items
                       SET source_path=?,
                           cp_path=?,
                           submitter=?,
                           last_submitted_on=?
                     WHERE id=?
                """, (rel_src, rel_cp, actor or "", now_date, int(it["id"])))
                con.commit()
                con.close()

            _retry_write(_do_overwrite)
            overwritten += 1

        else:
            new_version = int(meta.get("new_version") or it.get("cp_version") or 1)

            def _do_increment():
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
                           last_submitted_on=?,
                           last_reviewed_on=NULL
                     WHERE id=?
                """, (
                    rel_src,
                    rel_cp,
                    actor or "",
                    new_version,
                    str(new_status or "pending"),
                    now_date,
                    int(it["id"]),
                ))
                con.commit()
                con.close()

            _retry_write(_do_increment)
            incremented += 1

        # Event log (best-effort)
        try:
            append_checkprint_event(
                db_path,
                item_id=int(it["id"]),
                actor=actor or "",
                event="resubmitted",
                from_status=old_status,
                to_status=new_status,
                note=f"Resubmitted via _CheckPrintIncoming ({Path(src_path).name})",
            )
        except Exception:
            pass

    # Delete incoming files ONLY after DB success
    delete_ops = [plan_delete(p) for p in incoming_files]
    ok2, bad2, reason2 = preflight_ops(delete_ops)
    if not ok2:
        # We do NOT roll back the resubmission; just report the cleanup issue.
        return {
            "ok": True,
            "updated": overwritten + incremented,
            "overwritten": overwritten,
            "incremented": incremented,
            "deleted_incoming": 0,
            "cleanup_warning": {"path": str(bad2), "reason": reason2 or ""},
        }

    execute_ops(delete_ops)

    return {
        "ok": True,
        "updated": overwritten + incremented,
        "overwritten": overwritten,
        "incremented": incremented,
        "deleted_incoming": len(incoming_files),
        "incoming_dir": str(incoming_dir),
    }

def complete_and_archive_checkprint(
    db_path: Path,
    *,
    batch_id: int,
    actor: str,
) -> None:
    """
    Complete a CheckPrint batch WITHOUT creating a transmittal.

    Behaviour:
      • All items MUST be accepted
      • Latest CP version replaces source files
      • All _CP_N artefacts are removed from source folders
      • CheckPrint batch marked as 'completed'
      • CheckPrint folder retained as archive
    """
    db_path = Path(db_path)
    init_db(db_path)

    batch = get_checkprint_batch(db_path, batch_id)
    if not batch:
        raise RuntimeError("CheckPrint batch not found.")

    if batch.get("status") in {"completed", "cancelled"}:
        raise RuntimeError(f"CheckPrint batch already {batch['status']}.")

    items = get_checkprint_items(db_path, batch_id)
    if not items:
        raise RuntimeError("No items in CheckPrint batch.")

    # Enforce acceptance
    not_accepted = [
        it for it in items
        if (it.get("status") or "").lower() != "accepted"
    ]
    if not_accepted:
        raise RuntimeError("All CheckPrint items must be accepted before completion.")

    proj_root = _project_root(db_path)

    all_ops = []

    for it in items:
        cp_abs = proj_root / it["cp_path"]
        src_abs = proj_root / it["source_path"]

        if not cp_abs.exists():
            raise RuntimeError(f"Approved CP file missing:\n{cp_abs}")

        # Strip _CP_N suffix
        base, ext = _split_basename(cp_abs.name)
        final_name = base + ext
        final_src = src_abs.with_name(final_name)

        # 1) Copy CP → final source filename
        all_ops.append(plan_copy(cp_abs, final_src))

        # 2) Remove ALL _CP_N files from source directory
        src_dir = src_abs.parent
        try:
            for f in src_dir.iterdir():
                if f.name.startswith(base + "_CP_"):
                    all_ops.append(plan_delete(f))
        except Exception:
            pass

    # --- Preflight ---
    ok, bad, reason = preflight_ops(all_ops)
    if not ok:
        raise PreflightError(bad, reason or "File operation preflight failed.")

    # --- Execute ---
    execute_ops(all_ops)

    # --- DB close-out ---
    now = datetime.now().strftime("%Y-%m-%d")

    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        cur.execute("""
            UPDATE checkprint_batches
               SET status='completed',
                   submitted_on=?,
                   reviewer=?
             WHERE id=?
        """, (now, actor or "", int(batch_id)))
        con.commit()
        con.close()

    _retry_write(_do)

    # --- Event log (batch-level, item-level for traceability) ---
    for it in items:
        try:
            append_checkprint_event(
                db_path,
                item_id=int(it["id"]),
                actor=actor or "",
                event="completed",
                from_status="accepted",
                to_status="accepted",
                note="CheckPrint completed and archived",
            )
        except Exception:
            pass


def cancel_checkprint(db_path: Path, *, batch_id: int, actor: str) -> dict:
    """
    Enhanced cancellation:
      • Returns structured result for UI
      • Provides clear error details
      • Allows UI to decide whether to force DB-only cancellation
    """

    if batch_id is None:
        return {
            "ok": False,
            "mode": "aborted",
            "error_path": None,
            "error_reason": "No batch_id provided."
        }

    batch_id = int(batch_id)
    db_path = Path(db_path)
    init_db(db_path)

    batch = get_checkprint_batch(db_path, batch_id)
    if not batch:
        return {
            "ok": False,
            "mode": "aborted",
            "error_path": None,
            "error_reason": "Batch not found."
        }

    batch_code = batch["code"]
    cp_root = _checkprint_root(db_path)
    batch_dir = cp_root / batch_code
    batch_items = get_checkprint_items(db_path, batch_id)

    if not batch_items:
        return {
            "ok": False,
            "mode": "aborted",
            "error_path": None,
            "error_reason": "Batch contains no documents."
        }

    proj_root = _project_root(db_path)
    all_ops = []

    # Plan restores
    for it in batch_items:
        src_abs = proj_root / it["source_path"]
        base, ext = _split_basename(src_abs.name)
        final_name = base + ext
        final_path = src_abs.with_name(final_name)

        if src_abs.exists():
            all_ops.append(plan_rename(src_abs, final_path))

    # Plan CP folder rename
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    cancelled_dir = batch_dir.with_name(f"{batch_code}_cancelled_{ts}")
    if batch_dir.exists():
        all_ops.append(plan_rename(batch_dir, cancelled_dir))

    # -------- Preflight --------
    ok, bad_path, reason = preflight_ops(all_ops)
    if not ok:
        return {
            "ok": False,
            "mode": "blocked",
            "error_path": bad_path,
            "error_reason": reason
        }

    # -------- Execute --------
    try:
        execute_ops(all_ops)
    except Exception as e:
        # Execution failure → may be partial
        err_text = str(e)
        return {
            "ok": False,
            "mode": "partial",
            "error_path": None,
            "error_reason": err_text
        }

    # -------- DB update --------
    _mark_checkprint_cancelled(db_path, batch_id, actor)

    return {
        "ok": True,
        "mode": "full",
        "error_path": None,
        "error_reason": None
    }


def add_documents_to_checkprint(
    db_path: Path,
    batch_id: int,
    *,
    items: List[Dict[str, Any]],
    actor: str,
) -> Dict[str, Any]:
    """
    Add documents to an existing (active) CheckPrint batch.

    items must match the FilesTab snapshot format at minimum:
        [{"doc_id": "...", "revision": "...", "file_path": "..."}, ...]

    This operation is highly controlled:
      - Only allows adding from the register (doc_id must exist in DB project)
      - Source file must NOT already have a _CP_N suffix
      - Renames source to _CP_1 and copies into the batch folder
      - Inserts new checkprint_items rows (state='active')
    """
    db_path = Path(db_path)
    init_db(db_path)
    batch_id = int(batch_id)

    batch = get_checkprint_batch(db_path, batch_id)
    if not batch:
        return {"ok": False, "error": "batch_not_found"}
    if batch.get("status") not in {"in_progress", "submitted", "awaiting_review"}:
        return {"ok": False, "error": "batch_not_editable", "status": batch.get("status")}

    existing = get_checkprint_items(db_path, batch_id)
    existing_doc_ids = {str(it["doc_id"]) for it in existing}

    proj_root = _project_root(db_path)
    cp_root = _checkprint_root(db_path)
    batch_dir = cp_root / str(batch["code"])
    batch_dir.mkdir(parents=True, exist_ok=True)

    planned_ops = []
    prepared_items: List[Dict[str, Any]] = []
    now = datetime.now().strftime("%Y-%m-%d")

    # Validate + plan file ops first
    for snap in items:
        doc_id = str(snap.get("doc_id") or "").strip()
        if not doc_id:
            continue
        if doc_id in existing_doc_ids:
            return {"ok": False, "error": "duplicate_doc_in_batch", "doc_id": doc_id}

        src = Path(str(snap.get("file_path") or "")).expanduser()
        if not src.exists():
            return {"ok": False, "error": "source_missing", "doc_id": doc_id, "path": str(src)}
        if not src.is_file():
            return {"ok": False, "error": "source_not_file", "doc_id": doc_id, "path": str(src)}

        # refuse if already a CP file
        stem = src.stem
        if re.search(r"_CP_\d+$", stem, flags=re.IGNORECASE):
            return {"ok": False, "error": "source_already_cp", "doc_id": doc_id, "path": str(src)}

        base_name, ext = _split_basename(src.name)
        cp_version = 1
        cp_name = f"{base_name}_CP_{cp_version}{ext}"
        dst_src = src.with_name(cp_name)
        dst_cp = batch_dir / cp_name

        # 1) Copy ORIGINAL source into CheckPrint folder
        planned_ops.append(plan_copy(src, dst_cp))

        # 2) Rename ORIGINAL source in-place
        planned_ops.append(plan_rename(src, dst_src))

        prepared_items.append(
            {
                "doc_id": doc_id,
                "revision": str(snap.get("revision") or ""),
                "base_name": base_name + ext,
                "cp_version": cp_version,
                "status": "pending",
                "submitter": actor or "",
                "reviewer": "",
                "last_submitted_on": now,
                "last_reviewed_on": "",
                "last_reviewer_note": "",
                "source_path": str(dst_src.relative_to(proj_root)),
                "cp_path": str(dst_cp.relative_to(proj_root)),
                "state": "active",
            }
        )

    if not prepared_items:
        return {"ok": False, "error": "no_items"}

    ok, bad_path, reason = preflight_ops(planned_ops)
    if not ok:
        return {"ok": False, "error": "blocked", "path": bad_path, "reason": reason}

    try:
        execute_ops(planned_ops)
    except Exception as e:
        return {"ok": False, "error": "file_ops_failed", "reason": str(e)}

    inserted_ids = insert_checkprint_items(db_path, batch_id=batch_id, items=prepared_items)

    # Log events (best-effort; do not fail the operation if event insert fails)
    for item_id in inserted_ids:
        try:
            append_checkprint_event(
                db_path,
                item_id=int(item_id),
                actor=actor,
                event="added",
                from_status=None,
                to_status="pending",
                note="Added to CheckPrint batch",
            )
        except Exception:
            pass

    return {"ok": True, "inserted": len(inserted_ids)}

from typing import Literal
from datetime import datetime
import re

def remove_documents_from_checkprint(
    db_path: Path,
    batch_id: int,
    *,
    item_ids: List[int],
    actor: str,
    mode: Literal["keep_latest", "revert_original"],
) -> Dict[str, Any]:
    """
    Remove documents from an existing CheckPrint batch.

    Behaviour (batch-local, per doc_id):
      - Cleans up ALL CP artefacts for the document in this batch
      - Soft-removes DB rows (state='removed')
      - User-selected source handling:
          * keep_latest     → latest CP becomes the source file
          * revert_original → original source restored

    No hard deletes. Full audit preserved.
    """

    if not item_ids:
        return {"ok": False, "error": "no_items"}

    if mode not in {"keep_latest", "revert_original"}:
        return {"ok": False, "error": "invalid_mode"}

    db_path = Path(db_path)
    init_db(db_path)
    batch_id = int(batch_id)

    batch = get_checkprint_batch(db_path, batch_id)
    if not batch:
        return {"ok": False, "error": "batch_not_found"}
    if batch.get("status") not in {"in_progress", "submitted", "awaiting_review"}:
        return {"ok": False, "error": "batch_not_editable", "status": batch.get("status")}

    # Load selected items, restrict to active + this batch
    seed_items = get_checkprint_items_by_ids(db_path, [int(x) for x in item_ids])
    seed_items = [
        it for it in seed_items
        if int(it.get("batch_id") or 0) == batch_id
        and it.get("state") == "active"
    ]
    if not seed_items:
        return {"ok": False, "error": "items_not_found"}

    # Expand to ALL CP entries for the affected doc_ids (batch-local)
    affected_doc_ids = {it["doc_id"] for it in seed_items}
    all_batch_items = get_checkprint_items(db_path, batch_id)
    per_doc: Dict[str, List[Dict[str, Any]]] = {}
    for it in all_batch_items:
        if it.get("state") != "active":
            continue
        if it["doc_id"] in affected_doc_ids:
            per_doc.setdefault(it["doc_id"], []).append(it)

    proj_root = _project_root(db_path)
    planned_ops = []
    now = datetime.now().strftime("%Y-%m-%d")

    # ------------------------------------------------------------
    # Plan file operations per document
    # ------------------------------------------------------------
    for doc_id, items in per_doc.items():
        # sort by cp_version
        items = sorted(items, key=lambda x: int(x.get("cp_version") or 0))
        first = items[0]
        last = items[-1]

        src_abs = proj_root / str(first["source_path"])
        base_name = str(first.get("base_name") or "").strip()

        # Collect all CP files for deletion consideration
        cp_files = [
            proj_root / str(it["cp_path"])
            for it in items
            if it.get("cp_path")
        ]

        # --------------------------------------------------------
        # ALSO sweep filesystem for any stray CP_N files
        # (covers legacy DB-only removals or partial failures)
        # --------------------------------------------------------
        try:
            batch_dir = cp_files[0].parent if cp_files else None
            if batch_dir and batch_dir.exists():
                import os, re

                base_stem, base_ext = os.path.splitext(base_name)
                cp_pattern = re.compile(
                    rf"^{re.escape(base_stem)}_CP_\d+{re.escape(base_ext)}$",
                    re.IGNORECASE,
                )

                for p in batch_dir.iterdir():
                    if not p.is_file():
                        continue
                    if cp_pattern.match(p.name) and p not in cp_files:
                        cp_files.append(p)
        except Exception:
            # Never fail removal due to cleanup discovery
            pass


        if mode == "keep_latest":
            # Latest CP becomes source
            latest_cp_abs = proj_root / str(last["cp_path"])
            if not latest_cp_abs.exists():
                return {
                    "ok": False,
                    "error": "latest_cp_missing",
                    "doc_id": doc_id,
                    "path": str(latest_cp_abs),
                }

            if base_name:
                new_src_abs = latest_cp_abs.with_name(base_name)
                # Promote latest CP to become the new source file (PRE-FLIGHT SAFE)
                latest_cp_abs = proj_root / str(last["cp_path"])
                if not latest_cp_abs.exists():
                    return {
                        "ok": False,
                        "error": "latest_cp_missing",
                        "doc_id": doc_id,
                        "path": str(latest_cp_abs),
                    }

                if not base_name:
                    return {
                        "ok": False,
                        "error": "missing_base_name",
                        "doc_id": doc_id,
                    }

                new_src_abs = src_abs.with_name(base_name)

                # 1) Remove existing source first (if present)
                if new_src_abs.exists():
                    planned_ops.append(plan_delete(new_src_abs))

                # 2) Copy latest CP → canonical source name
                planned_ops.append(plan_copy(latest_cp_abs, new_src_abs))

                # 3) Delete ALL CP artefacts (including the promoted one)
                for cp in cp_files:
                    if cp.exists():
                        planned_ops.append(plan_delete(cp))



        else:  # revert_original
            # Restore original source name if needed
            if base_name and src_abs.exists():
                restore_abs = src_abs.with_name(base_name)
                if restore_abs != src_abs:
                    planned_ops.append(plan_rename(src_abs, restore_abs))

            # Delete all CP artefacts
            for cp in cp_files:
                if cp.exists():
                    planned_ops.append(plan_delete(cp))

    # ------------------------------------------------------------
    # Preflight + execution
    # ------------------------------------------------------------
    ok, bad_path, reason = preflight_ops(planned_ops)
    if not ok:
        return {"ok": False, "error": "blocked", "path": bad_path, "reason": reason}

    try:
        execute_ops(planned_ops)
    except Exception as e:
        return {"ok": False, "error": "file_ops_failed", "reason": str(e)}

    # ------------------------------------------------------------
    # DB updates + audit
    # ------------------------------------------------------------
    removed_ids: List[int] = []

    for doc_id, items in per_doc.items():
        for it in items:
            removed_ids.append(int(it["id"]))
            try:
                append_checkprint_event(
                    db_path,
                    item_id=int(it["id"]),
                    actor=actor,
                    event="removed",
                    from_status=str(it.get("status") or ""),
                    to_status="removed",
                    note=f"Removed from CheckPrint batch ({mode})",
                )
            except Exception:
                pass

    mark_checkprint_items_removed(
        db_path,
        batch_id=batch_id,
        item_ids=removed_ids,
    )

    return {
        "ok": True,
        "removed": len(removed_ids),
        "mode": mode,
    }


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
