from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import shutil, re, os

try:
    from .db import (
        init_db, get_project,
        create_checkprint_batch, get_checkprint_items,
        get_latest_checkprint_versions, update_checkprint_item_status,
        append_checkprint_event, insert_transmittal, _retry_write, _connect,
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



def _next_cp_code(cp_root: Path) -> str:
    """
    Scan existing CP-TRN-### dirs under cp_root and return the next code.
    """
    pat = re.compile(r"^CP-TRN-(\d+)$", re.IGNORECASE)
    maxn = 0
    if cp_root.exists():
        for p in cp_root.iterdir():
            if not p.is_dir():
                continue
            m = pat.match(p.name)
            if m:
                try:
                    maxn = max(maxn, int(m.group(1)))
                except ValueError:
                    continue
    return f"CP-TRN-{maxn + 1:03d}"


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

    cp_root = _checkprint_root(db_path)
    cp_code = _next_cp_code(cp_root)
    batch_dir = cp_root / cp_code
    batch_dir.mkdir(parents=True, exist_ok=True)

    now = created_on_str or datetime.now().strftime("%Y-%m-%d")
    doc_ids = [s["doc_id"] for s in items if s.get("file_path")]
    latest_versions = get_latest_checkprint_versions(db_path, project_id, doc_ids)

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
            cp_version = latest_versions.get(doc_id, 0) + 1
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
                "source_path": str(dst_src),
                "cp_path": str(dst_cp),
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


def resubmit_checkprint_items(
    db_path: Path,
    *,
    batch_id: int,
    item_id_to_new_path: Dict[int, Path],
    submitter: str,
) -> None:
    """
    For submitter: resubmit rejected/pending items with new PDF files.
    Renames + copies new source files to *_CP_N and updates DB.
    """
    db_path = Path(db_path)
    init_db(db_path)
    items = {it["id"]: it for it in get_checkprint_items(db_path, batch_id)}
    if not items:
        raise RuntimeError("No items found for this CheckPrint batch.")

    proj = get_project(db_path)
    project_code = proj["project_code"]
    project_id = proj["id"]
    cp_root = _checkprint_root(db_path)

    # find this batch's directory
    batch = None
    cp_dir = None
    for p in cp_root.iterdir():
        if p.is_dir():
            if any(str(it["cp_path"]).startswith(str(p)) for it in items.values()):
                cp_dir = p
                break
    if not cp_dir:
        raise RuntimeError("CheckPrint batch folder could not be located on disk.")

    doc_ids = [items[i]["doc_id"] for i in item_id_to_new_path.keys() if i in items]
    latest_versions = get_latest_checkprint_versions(db_path, project_id, doc_ids)

    renames: List[tuple[Path, Path]] = []

    try:
        for item_id, new_file in item_id_to_new_path.items():
            it = items.get(item_id)
            if not it:
                continue
            if not new_file or not Path(new_file).exists():
                raise RuntimeError(f"New file does not exist:\n{new_file}")

            doc_id = it["doc_id"]
            base_name, ext = _split_basename(Path(new_file).name)
            cp_version = latest_versions.get(doc_id, it.get("cp_version", 1)) + 1
            cp_name = f"{base_name}_CP_{cp_version}{ext}"

            # rename source (new_file) to cp name in its folder
            src_new = Path(new_file)
            dst_src = src_new.with_name(cp_name)
            _safe_rename(src_new, dst_src)
            renames.append((dst_src, src_new))

            dst_cp = Path(cp_dir) / cp_name
            shutil.copy2(str(dst_src), str(dst_cp))

            # DB updates
            from_status = it["status"]
            update_checkprint_item_status(
                db_path,
                item_id=item_id,
                status="pending",
                reviewer=None,
                note=None,
            )
            # manual field updates for paths/version
            def _do():
                con = _connect(db_path); cur = con.cursor()
                cur.execute("""
                    UPDATE checkprint_items
                       SET cp_version=?, source_path=?, cp_path=?, last_submitted_on=datetime('now')
                     WHERE id=?
                """, (cp_version, str(dst_src), str(dst_cp), int(item_id)))
                con.commit(); con.close()
            _retry_write(_do)

            append_checkprint_event(
                db_path,
                item_id=item_id,
                actor=submitter,
                event="resubmitted",
                from_status=from_status,
                to_status="pending",
                note="Resubmitted for CheckPrint",
            )

    except Exception:
        for new_path, old_path in reversed(renames):
            try:
                if new_path.exists():
                    new_path.rename(old_path)
            except OSError:
                pass
        raise


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
            src = Path(it["source_path"])
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
