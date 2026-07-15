from __future__ import annotations

import contextlib
import datetime as _dt
import getpass
import json
import os
import socket
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

HEARTBEAT_SECONDS = 15
STALE_TIMEOUT_SECONDS = 15 * 60
LOCK_SCHEMA_VERSION = 3
LOCK_FOLDER_NAME = "DB-Lock"
REQUEST_STALE_DAYS = 7


class ReadOnlyLockError(RuntimeError):
    """Raised when a database write is attempted without edit ownership."""


class LockConflictError(RuntimeError):
    """Raised when lock layers disagree unsafely."""


_tls = threading.local()
_contexts: Dict[str, Dict[str, Any]] = {}
_context_lock = threading.RLock()

_MUTATING_TOKENS = {
    "insert", "update", "delete", "replace", "create", "alter", "drop",
    "vacuum", "reindex", "attach", "detach",
}


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def utc_iso(value: Optional[_dt.datetime] = None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = _dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.astimezone(_dt.timezone.utc)
    except Exception:
        return None


def _path_key(db_path: Path | str) -> str:
    return str(Path(db_path).expanduser().resolve()).lower()


def lock_dir_path(db_path: Path | str) -> Path:
    p = Path(db_path).expanduser().resolve()
    return p.parent / LOCK_FOLDER_NAME


def lock_file_path(db_path: Path | str) -> Path:
    p = Path(db_path).expanduser().resolve()
    return lock_dir_path(p) / f"{p.stem}.lock.json"


def legacy_lock_file_path(db_path: Path | str) -> Path:
    p = Path(db_path).expanduser().resolve()
    return p.with_name(f"{p.stem}.lock.json")


def request_file_path(db_path: Path | str, requester_token: str) -> Path:
    p = Path(db_path).expanduser().resolve()
    safe = "".join(ch for ch in (requester_token or "") if ch.isalnum() or ch in "-_") or str(uuid.uuid4())
    return lock_dir_path(p) / f"{p.stem}.request.{safe}.json"


def request_file_glob(db_path: Path | str) -> str:
    p = Path(db_path).expanduser().resolve()
    return f"{p.stem}.request.*.json"


def initials_for(name: str) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "".join(p[0].upper() for p in parts[:3])


def default_owner_name() -> str:
    return getpass.getuser() or os.environ.get("USERNAME") or os.environ.get("USER") or "Unknown User"


def owner_identity(name: str = "") -> Dict[str, Any]:
    name = (name or "").strip() or default_owner_name()
    return {
        "owner_name": name,
        "owner_initials": initials_for(name),
        "machine_name": socket.gethostname() or "unknown-machine",
        "process_id": os.getpid(),
    }


def normalize_identity_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def current_machine_name() -> str:
    return socket.gethostname() or "unknown-machine"


def identity_matches_lock(status: Dict[str, Any], owner_name: str = "", machine_name: str = "") -> bool:
    """Return True when a lock appears to belong to the same person on the same PC."""
    if not status:
        return False
    target_name = normalize_identity_text(owner_name or default_owner_name())
    target_machine = normalize_identity_text(machine_name or current_machine_name())
    lock_name = normalize_identity_text(status.get("owner_name") or "")
    lock_machine = normalize_identity_text(status.get("machine_name") or "")
    return bool(target_name and lock_name and target_name == lock_name and target_machine and lock_machine == target_machine)


def _local_process_is_alive(process_id: Any) -> bool:
    """Best-effort check for whether a lock process is still alive on this machine."""
    try:
        pid = int(process_id or 0)
    except Exception:
        return False
    if pid <= 0 or pid == os.getpid():
        return False

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                # Access denied usually means the process exists; invalid parameter usually means it does not.
                err = ctypes.get_last_error()
                return err == 5
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            # If the OS-specific check fails, fall back to a conservative False so the user can reclaim a self-owned stale file.
            return False

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def same_identity_can_reclaim(status: Dict[str, Any], owner_name: str = "", machine_name: str = "") -> bool:
    """Allow automatic reclaim of an active-looking lock when it is this user on this PC and the old local process is gone."""
    if not status or not status.get("locked"):
        return False
    if not identity_matches_lock(status, owner_name=owner_name, machine_name=machine_name):
        return False
    # Avoid silently taking the lock from another still-running instance on the same PC.
    if _local_process_is_alive(status.get("process_id")):
        return False
    return True


def is_mutating_sql(sql: str) -> bool:
    text = (sql or "").strip().lower()
    if not text:
        return False
    while text.startswith("--"):
        parts = text.split("\n", 1)
        if len(parts) == 1:
            return False
        text = parts[1].lstrip()
    if text.startswith("/*"):
        end = text.find("*/")
        if end >= 0:
            text = text[end + 2:].lstrip()
    if not text:
        return False
    token = text.split(None, 1)[0].strip(";()")
    if token == "with":
        tail = text[-160:]
        return any(f" {t} " in tail for t in ("insert", "update", "delete", "replace"))
    return token in _MUTATING_TOKENS


def _bypass_depth() -> int:
    return int(getattr(_tls, "bypass_write_guard", 0) or 0)


@contextlib.contextmanager
def bypass_write_guard() -> Iterator[None]:
    _tls.bypass_write_guard = _bypass_depth() + 1
    try:
        yield
    finally:
        _tls.bypass_write_guard = max(0, _bypass_depth() - 1)


def set_lock_context(db_path: Path | str, owner_token: str = "", read_only: bool = True) -> None:
    key = _path_key(db_path)
    with _context_lock:
        _contexts[key] = {
            "db_path": str(Path(db_path).expanduser().resolve()),
            "owner_token": owner_token or "",
            "read_only": bool(read_only),
            "last_validated": 0.0,
        }


def clear_lock_context(db_path: Path | str) -> None:
    key = _path_key(db_path)
    with _context_lock:
        _contexts.pop(key, None)


def get_lock_context(db_path: Path | str) -> Dict[str, Any]:
    key = _path_key(db_path)
    with _context_lock:
        return dict(_contexts.get(key) or {})


def is_read_only_context(db_path: Path | str) -> bool:
    ctx = get_lock_context(db_path)
    return bool(ctx and ctx.get("read_only"))


def ensure_lock_schema(db_path: Path | str) -> None:
    p = Path(db_path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_dir_path(p).mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(p), timeout=10.0) as con:
        con.execute("PRAGMA busy_timeout = 5000;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS edit_lock (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                schema_version INTEGER NOT NULL DEFAULT 1,
                owner_token TEXT,
                owner_name TEXT,
                owner_initials TEXT,
                machine_name TEXT,
                process_id INTEGER,
                acquired_utc TEXT,
                heartbeat_utc TEXT,
                expires_utc TEXT,
                requested_by_token TEXT,
                requested_by_name TEXT,
                requested_by_initials TEXT,
                requested_by_machine TEXT,
                requested_utc TEXT,
                message TEXT
            );
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS edit_lock_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_utc TEXT NOT NULL,
                actor_name TEXT,
                actor_token TEXT,
                machine_name TEXT,
                event TEXT NOT NULL,
                details TEXT
            );
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS db_activity (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_write_utc TEXT,
                last_writer_token TEXT,
                last_writer_name TEXT,
                last_write_counter INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        con.execute("INSERT OR IGNORE INTO edit_lock(id, schema_version) VALUES(1, ?)", (LOCK_SCHEMA_VERSION,))
        con.execute("INSERT OR IGNORE INTO db_activity(id, last_write_counter) VALUES(1, 0)")
        con.commit()


def _read_json_file(path: Path, source: str) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data["source"] = source
            data["path"] = str(path)
            return data
    except Exception:
        return {"source": source, "invalid": True, "path": str(path)}
    return {}


def _read_json_lock(db_path: Path | str) -> Dict[str, Any]:
    current = _read_json_file(lock_file_path(db_path), "json")
    if current:
        return current
    # Migration compatibility: respect active locks written by the first edit-lock patch.
    legacy = _read_json_file(legacy_lock_file_path(db_path), "legacy_json")
    return legacy


def _write_json_lock(db_path: Path | str, record: Dict[str, Any]) -> None:
    path = lock_file_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload["schema"] = LOCK_SCHEMA_VERSION
    payload["db_path"] = str(Path(db_path).expanduser().resolve())
    payload.pop("source", None)
    payload.pop("path", None)
    # Handover requests deliberately live in separate .request.*.json files.
    for key in ("requested_by_token", "requested_by_name", "requested_by_initials", "requested_by_machine", "requested_utc", "message"):
        payload.pop(key, None)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(path))



def _purge_lock_artifacts(db_path: Path | str, include_requests: bool = False) -> None:
    """Best-effort cleanup used for stale/same-user/force takeovers."""
    for lf in (lock_file_path(db_path), legacy_lock_file_path(db_path)):
        try:
            if lf.exists():
                lf.unlink()
        except Exception:
            pass
    if include_requests:
        try:
            folder = lock_dir_path(db_path)
            for req in folder.glob(request_file_glob(db_path)):
                try:
                    req.unlink()
                except Exception:
                    pass
        except Exception:
            pass

def _read_db_lock(db_path: Path | str) -> Dict[str, Any]:
    try:
        ensure_lock_schema(db_path)
        with sqlite3.connect(str(Path(db_path).expanduser().resolve()), timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM edit_lock WHERE id=1").fetchone()
            if not row:
                return {}
            data = dict(row)
            data["source"] = "db"
            return data
    except Exception:
        return {"source": "db", "invalid": True}


def _write_db_lock(db_path: Path | str, record: Dict[str, Any]) -> None:
    ensure_lock_schema(db_path)
    with sqlite3.connect(str(Path(db_path).expanduser().resolve()), timeout=10.0) as con:
        con.execute("PRAGMA busy_timeout = 5000;")
        con.execute(
            """
            UPDATE edit_lock SET
                schema_version=?,
                owner_token=?, owner_name=?, owner_initials=?, machine_name=?, process_id=?,
                acquired_utc=?, heartbeat_utc=?, expires_utc=?,
                requested_by_token=NULL, requested_by_name=NULL, requested_by_initials=NULL,
                requested_by_machine=NULL, requested_utc=NULL, message=NULL
            WHERE id=1
            """,
            (
                LOCK_SCHEMA_VERSION,
                record.get("owner_token") or None,
                record.get("owner_name") or None,
                record.get("owner_initials") or None,
                record.get("machine_name") or None,
                int(record.get("process_id") or 0) or None,
                record.get("acquired_utc") or None,
                record.get("heartbeat_utc") or None,
                record.get("expires_utc") or None,
            ),
        )
        con.commit()


def _record_from_db(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    return {k: row.get(k) for k in (
        "owner_token", "owner_name", "owner_initials", "machine_name", "process_id",
        "acquired_utc", "heartbeat_utc", "expires_utc",
    )} | {"source": row.get("source", "db")}


def _is_record_active(record: Dict[str, Any], now: Optional[_dt.datetime] = None) -> bool:
    if not record or not record.get("owner_token"):
        return False
    now = now or utc_now()
    expires = parse_utc(record.get("expires_utc"))
    if not expires:
        hb = parse_utc(record.get("heartbeat_utc"))
        if not hb:
            return False
        expires = hb + _dt.timedelta(seconds=STALE_TIMEOUT_SECONDS)
    return now <= expires


def _latest_record(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    def key(rec: Dict[str, Any]) -> _dt.datetime:
        return parse_utc(rec.get("heartbeat_utc")) or parse_utc(rec.get("acquired_utc")) or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    return max(records, key=key) if records else {}


def _read_request_file(path: Path) -> Dict[str, Any]:
    data = _read_json_file(path, "request_json")
    if data:
        data.setdefault("request_token", data.get("requester_token") or "")
        data.setdefault("state", "requested")
    return data


def list_handover_requests(db_path: Path | str, include_completed: bool = False) -> List[Dict[str, Any]]:
    ensure_lock_schema(db_path)
    folder = lock_dir_path(db_path)
    requests: List[Dict[str, Any]] = []
    cutoff = utc_now() - _dt.timedelta(days=REQUEST_STALE_DAYS)
    for path in folder.glob(request_file_glob(db_path)):
        rec = _read_request_file(path)
        if not rec or rec.get("invalid"):
            continue
        created = parse_utc(rec.get("requested_utc")) or parse_utc(rec.get("created_utc")) or utc_now()
        if created < cutoff:
            # Best-effort cleanup for old completed/abandoned request files.
            try:
                if rec.get("state") != "requested":
                    path.unlink()
            except Exception:
                pass
            continue
        state = (rec.get("state") or "requested").lower()
        if include_completed or state == "requested":
            requests.append(rec)
    requests.sort(key=lambda r: parse_utc(r.get("requested_utc")) or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc), reverse=True)
    return requests


def _latest_pending_request(db_path: Path | str) -> Dict[str, Any]:
    reqs = list_handover_requests(db_path, include_completed=False)
    return reqs[0] if reqs else {}


def _write_request_response(path: Path, state: str, responder_name: str = "", responder_token: str = "", message: str = "") -> None:
    rec = _read_request_file(path)
    if not rec:
        return
    rec.update({
        "state": state,
        "responded_utc": utc_iso(),
        "responded_by_name": responder_name or default_owner_name(),
        "responded_by_initials": initials_for(responder_name or default_owner_name()),
        "responded_by_machine": socket.gethostname() or "",
        "responded_by_token": responder_token or "",
        "response_message": message or state,
    })
    for key in ("source", "path"):
        rec.pop(key, None)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(path))


def get_handover_request_status(db_path: Path | str, requester_token: str) -> Dict[str, Any]:
    if not requester_token:
        return {}
    path = request_file_path(db_path, requester_token)
    return _read_request_file(path)


def get_lock_status(db_path: Path | str) -> Dict[str, Any]:
    ensure_lock_schema(db_path)
    now = utc_now()
    records = []
    for raw in (_read_json_lock(db_path), _read_db_lock(db_path)):
        if raw and raw.get("owner_token"):
            records.append(_record_from_db(raw))

    active = [r for r in records if _is_record_active(r, now)]
    expired = [r for r in records if r.get("owner_token") and not _is_record_active(r, now)]
    request = _latest_pending_request(db_path)

    status: Dict[str, Any] = {
        "db_path": str(Path(db_path).expanduser().resolve()),
        "lock_dir": str(lock_dir_path(db_path)),
        "lock_file": str(lock_file_path(db_path)),
        "legacy_lock_file": str(legacy_lock_file_path(db_path)),
        "locked": False,
        "is_stale": False,
        "conflict": False,
        "owner_token": "",
        "owner_name": "",
        "owner_initials": "",
        "machine_name": "",
        "process_id": None,
        "acquired_utc": "",
        "heartbeat_utc": "",
        "expires_utc": "",
        "requested_by_token": request.get("requester_token") or request.get("requested_by_token") or "",
        "requested_by_name": request.get("requester_name") or request.get("requested_by_name") or "",
        "requested_by_initials": request.get("requester_initials") or request.get("requested_by_initials") or "",
        "requested_by_machine": request.get("requester_machine") or request.get("requested_by_machine") or "",
        "requested_utc": request.get("requested_utc") or "",
        "message": request.get("message") or "",
        "request_file": request.get("path") or "",
        "sources": [r.get("source") for r in records if r.get("source")],
    }

    if active:
        tokens = {r.get("owner_token") for r in active if r.get("owner_token")}
        chosen = _latest_record(active)
        status.update({
            "locked": True,
            "conflict": len(tokens) > 1,
            "owner_token": chosen.get("owner_token") or "",
            "owner_name": chosen.get("owner_name") or "",
            "owner_initials": chosen.get("owner_initials") or "",
            "machine_name": chosen.get("machine_name") or "",
            "process_id": chosen.get("process_id"),
            "acquired_utc": chosen.get("acquired_utc") or "",
            "heartbeat_utc": chosen.get("heartbeat_utc") or "",
            "expires_utc": chosen.get("expires_utc") or "",
        })
        if len(tokens) > 1:
            status["details"] = "Lock conflict: JSON and DB lock records show different active owners."
    elif expired:
        chosen = _latest_record(expired)
        status.update({
            "locked": False,
            "is_stale": True,
            "owner_token": chosen.get("owner_token") or "",
            "owner_name": chosen.get("owner_name") or "",
            "owner_initials": chosen.get("owner_initials") or "",
            "machine_name": chosen.get("machine_name") or "",
            "process_id": chosen.get("process_id"),
            "acquired_utc": chosen.get("acquired_utc") or "",
            "heartbeat_utc": chosen.get("heartbeat_utc") or "",
            "expires_utc": chosen.get("expires_utc") or "",
        })
    return status


def _make_owner_record(owner_name: str, owner_token: Optional[str] = None) -> Dict[str, Any]:
    now = utc_now()
    ident = owner_identity(owner_name)
    return {
        "schema": LOCK_SCHEMA_VERSION,
        "owner_token": owner_token or str(uuid.uuid4()),
        **ident,
        "acquired_utc": utc_iso(now),
        "heartbeat_utc": utc_iso(now),
        "expires_utc": utc_iso(now + _dt.timedelta(seconds=STALE_TIMEOUT_SECONDS)),
    }


def log_lock_event(db_path: Path | str, event: str, actor_name: str = "", actor_token: str = "", details: str = "") -> None:
    try:
        ensure_lock_schema(db_path)
        with sqlite3.connect(str(Path(db_path).expanduser().resolve()), timeout=10.0) as con:
            con.execute(
                """
                INSERT INTO edit_lock_events(happened_utc, actor_name, actor_token, machine_name, event, details)
                VALUES(?,?,?,?,?,?)
                """,
                (utc_iso(), actor_name or "", actor_token or "", socket.gethostname() or "", event or "", details or ""),
            )
            con.commit()
    except Exception:
        pass


def try_acquire_lock(
    db_path: Path | str,
    owner_name: str = "",
    force: bool = False,
    reason: str = "acquire",
    allow_active_takeover: bool = False,
    clear_requests: bool = False,
) -> Dict[str, Any]:
    status = get_lock_status(db_path)
    locked = bool(status.get("locked"))
    active_locked = locked and not bool(status.get("is_stale"))

    if locked and not force:
        return {"acquired": False, "owner_token": "", "status": status}
    if active_locked and not allow_active_takeover:
        return {"acquired": False, "owner_token": "", "status": status}

    if force or allow_active_takeover or status.get("is_stale"):
        _purge_lock_artifacts(db_path, include_requests=bool(clear_requests))

    record = _make_owner_record(owner_name)
    _write_json_lock(db_path, record)
    _write_db_lock(db_path, record)

    previous = ""
    if status.get("owner_name") or status.get("machine_name"):
        previous = f"Previous owner: {status.get('owner_name') or '-'} on {status.get('machine_name') or '-'}"
    event = reason or "acquire"
    if allow_active_takeover and active_locked:
        event = reason or "force_takeover"
    elif status.get("is_stale"):
        event = "stale_takeover"

    log_lock_event(
        db_path,
        event,
        actor_name=record.get("owner_name") or "",
        actor_token=record.get("owner_token") or "",
        details=previous,
    )
    return {"acquired": True, "owner_token": record["owner_token"], "status": get_lock_status(db_path)}


def force_takeover_lock(db_path: Path | str, owner_name: str = "", reason: str = "force_takeover") -> Dict[str, Any]:
    return try_acquire_lock(
        db_path,
        owner_name=owner_name,
        force=True,
        reason=reason,
        allow_active_takeover=True,
        clear_requests=True,
    )


def refresh_heartbeat(db_path: Path | str, owner_token: str) -> Dict[str, Any]:
    status = get_lock_status(db_path)
    if status.get("conflict"):
        raise LockConflictError(status.get("details") or "Active edit lock conflict detected.")
    if not status.get("locked") or (status.get("owner_token") or "") != (owner_token or ""):
        raise LockConflictError("This session no longer owns the edit lock.")

    now = utc_now()
    record = {
        "owner_token": owner_token,
        "owner_name": status.get("owner_name") or "",
        "owner_initials": status.get("owner_initials") or initials_for(status.get("owner_name") or ""),
        "machine_name": status.get("machine_name") or socket.gethostname(),
        "process_id": status.get("process_id") or os.getpid(),
        "acquired_utc": status.get("acquired_utc") or utc_iso(now),
        "heartbeat_utc": utc_iso(now),
        "expires_utc": utc_iso(now + _dt.timedelta(seconds=STALE_TIMEOUT_SECONDS)),
    }
    _write_json_lock(db_path, record)
    _write_db_lock(db_path, record)
    return get_lock_status(db_path)


def _respond_to_pending_requests(db_path: Path | str, state: str, responder_name: str = "", responder_token: str = "", message: str = "") -> None:
    for req in list_handover_requests(db_path, include_completed=False):
        path_text = req.get("path") or ""
        if not path_text:
            continue
        try:
            _write_request_response(Path(path_text), state, responder_name=responder_name, responder_token=responder_token, message=message)
        except Exception:
            pass


def release_lock(db_path: Path | str, owner_token: str, reason: str = "release") -> bool:
    status = get_lock_status(db_path)
    if owner_token and (status.get("owner_token") or "") != owner_token and status.get("locked"):
        return False

    old_owner = status.get("owner_name") or ""
    if reason in {"handover_release", "manual_release", "application_closed", "release"}:
        _respond_to_pending_requests(
            db_path,
            "released",
            responder_name=old_owner,
            responder_token=owner_token or "",
            message="Editing access was released.",
        )

    clear_record = {
        "owner_token": None,
        "owner_name": None,
        "owner_initials": None,
        "machine_name": None,
        "process_id": None,
        "acquired_utc": None,
        "heartbeat_utc": None,
        "expires_utc": None,
    }
    _write_db_lock(db_path, clear_record)

    for lf in (lock_file_path(db_path), legacy_lock_file_path(db_path)):
        try:
            current = _read_json_file(lf, "json")
            if not current.get("owner_token") or not owner_token or current.get("owner_token") == owner_token:
                if lf.exists():
                    lf.unlink()
        except Exception:
            pass

    log_lock_event(db_path, reason, actor_name=old_owner, actor_token=owner_token or "")
    return True


def request_handover(db_path: Path | str, requester_name: str = "", requester_token: str = "", message: str = "") -> Dict[str, Any]:
    status = get_lock_status(db_path)
    requester_name = (requester_name or "").strip() or default_owner_name()
    requester_token = requester_token or str(uuid.uuid4())
    now = utc_iso()
    req = {
        "schema": LOCK_SCHEMA_VERSION,
        "kind": "edit_lock_handover_request",
        "db_path": str(Path(db_path).expanduser().resolve()),
        "requester_token": requester_token,
        "requester_name": requester_name,
        "requester_initials": initials_for(requester_name),
        "requester_machine": socket.gethostname() or "",
        "requester_process_id": os.getpid(),
        "requested_utc": now,
        "message": message or "Edit access requested.",
        "target_owner_token": status.get("owner_token") or "",
        "target_owner_name": status.get("owner_name") or "",
        "target_owner_machine": status.get("machine_name") or "",
        "state": "requested",
    }

    if status.get("locked"):
        path = request_file_path(db_path, requester_token)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(req, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))
        # Do not write to the DB or the owner heartbeat lock file here. The requester only writes its own request file.
    return {"requester_token": requester_token, "request_file": str(request_file_path(db_path, requester_token)), "status": get_lock_status(db_path)}


def clear_handover_request(db_path: Path | str, owner_token: str = "", event: str = "handover_declined") -> None:
    status = get_lock_status(db_path)
    if owner_token and status.get("locked") and status.get("owner_token") != owner_token:
        return
    responder_name = status.get("owner_name") or default_owner_name()
    state = "declined" if "declin" in (event or "").lower() else "cleared"
    _respond_to_pending_requests(
        db_path,
        state,
        responder_name=responder_name,
        responder_token=owner_token or "",
        message="Editing access request declined." if state == "declined" else "Editing access request cleared.",
    )
    log_lock_event(db_path, event, actor_name=responder_name, actor_token=owner_token or "")


def record_db_write(db_path: Path | str, writer_name: str = "", writer_token: str = "") -> None:
    try:
        ctx = get_lock_context(db_path)
        writer_token = writer_token or ctx.get("owner_token") or ""
        status = get_lock_status(db_path) if writer_token else {}
        writer_name = writer_name or status.get("owner_name") or ""
        with sqlite3.connect(str(Path(db_path).expanduser().resolve()), timeout=10.0) as con:
            con.execute(
                """
                UPDATE db_activity
                SET last_write_utc=?, last_writer_token=?, last_writer_name=?,
                    last_write_counter=COALESCE(last_write_counter, 0) + 1
                WHERE id=1
                """,
                (utc_iso(), writer_token or "", writer_name or ""),
            )
            con.commit()
    except Exception:
        pass


def require_write_access(db_path: Path | str, operation: str = "database write") -> None:
    if _bypass_depth() > 0:
        return
    ctx = get_lock_context(db_path)
    if not ctx:
        return
    if ctx.get("read_only") or not ctx.get("owner_token"):
        raise ReadOnlyLockError(
            f"Read-only mode: '{operation}' was blocked because this session does not own the edit lock."
        )

    now_ts = utc_now().timestamp()
    last = float(ctx.get("last_validated") or 0.0)
    if now_ts - last < 5.0:
        return

    status = get_lock_status(db_path)
    if status.get("conflict"):
        raise LockConflictError(status.get("details") or "Active edit lock conflict detected.")
    if not status.get("locked"):
        raise ReadOnlyLockError("Edit lock has been released or expired. Write blocked.")
    if (status.get("owner_token") or "") != (ctx.get("owner_token") or ""):
        raise ReadOnlyLockError(
            "Edit lock is now owned by another session. This session has been protected from writing."
        )
    ctx["last_validated"] = now_ts
    with _context_lock:
        _contexts[_path_key(db_path)] = ctx


def get_lock_events(db_path: Path | str, limit: int = 80) -> List[Dict[str, Any]]:
    ensure_lock_schema(db_path)
    with sqlite3.connect(str(Path(db_path).expanduser().resolve()), timeout=10.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT happened_utc, actor_name, machine_name, event, details
            FROM edit_lock_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit or 80),),
        ).fetchall()
    return [dict(r) for r in rows]
