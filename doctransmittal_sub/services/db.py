# services/db.py  — MERGED file (keeps your existing API + adds transmittal snapshots & edit/soft-delete)
from __future__ import annotations
import json
import sqlite3, time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# -------------------------------- connection --------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=30.0)
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    con.execute("PRAGMA busy_timeout = 5000;")
    return con

def _retry_write(fn, retries: int = 5, base_delay: float = 0.15):
    for i in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ("locked" in msg or "busy" in msg) and i < retries - 1:
                time.sleep(base_delay * (i + 1))
                continue
            raise

def _ensure_column(con: sqlite3.Connection, table: str, col: str, coltype: str):
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype};")

def _ensure_index(con: sqlite3.Connection, name: str, ddl: str):
    row = con.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
    if not row:
        con.execute(ddl)

# -------------------------------- schema --------------------------------

DDL = [
    # --- Project / Register layer (existing) ---
    '''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY,
        project_code TEXT NOT NULL UNIQUE,
        project_name TEXT NOT NULL,
        root_path   TEXT,
        client_company   TEXT,
        client_reference TEXT,
        client_contact   TEXT,
        end_user         TEXT
    );''',

    '''CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        doc_id TEXT NOT NULL,
        doc_type TEXT,
        file_type TEXT,
        description TEXT,
        status TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(project_id, doc_id)
    );''',

    '''CREATE TABLE IF NOT EXISTS revisions (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        rev TEXT NOT NULL,
        created_on TEXT DEFAULT (date('now')),
        notes TEXT,
        UNIQUE(document_id, rev)
    );''',

    '''CREATE TABLE IF NOT EXISTS project_areas (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        code TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        UNIQUE(project_id, code)
    );''',

    '''CREATE TABLE IF NOT EXISTS project_lists (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,                           -- 'doc_types' | 'file_types' | 'statuses'
        value TEXT NOT NULL,
        UNIQUE(project_id, kind, value)
    );''',

    # Presets (existing)
    '''CREATE TABLE IF NOT EXISTS presets (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        UNIQUE(project_id, name)
    );''',
    '''CREATE TABLE IF NOT EXISTS preset_items (
        preset_id INTEGER NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
        doc_id TEXT NOT NULL,
        UNIQUE(preset_id, doc_id)
    );''',

    # --- Transmittals (existing base) ---
    '''CREATE TABLE IF NOT EXISTS transmittals (
        id INTEGER PRIMARY KEY,
        project_code TEXT NOT NULL,
        number TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        client TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_on TEXT NOT NULL
    );''',

    '''CREATE TABLE IF NOT EXISTS transmittal_items (
        id INTEGER PRIMARY KEY,
        transmittal_id INTEGER NOT NULL REFERENCES transmittals(id) ON DELETE CASCADE,
        doc_id TEXT NOT NULL,
        doc_type TEXT,
        revision TEXT NOT NULL,
        file_path TEXT
    );''',

]

def init_db(db_path: Path) -> None:
    con = _connect(db_path); cur = con.cursor()
    for ddl in DDL:
        cur.execute(ddl)

    # projects: add client metadata (safe if already present)
    _ensure_column(con, "projects", "client_reference", "TEXT")
    _ensure_column(con, "projects", "client_contact", "TEXT")
    _ensure_column(con, "projects", "end_user", "TEXT")
    _ensure_column(con, "projects", "client_company", "TEXT")


    # Your existing extra columns
    _ensure_column(con, "documents", "sp_url", "TEXT")
    _ensure_column(con, "documents", "local_hint", "TEXT")

    # --- New, non-destructive migrations for history/edit/soft-delete ---
    # transmittals: track soft-delete + updates
    _ensure_column(con, "transmittals", "updated_on", "TEXT")
    _ensure_column(con, "transmittals", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "transmittals", "deleted_on", "TEXT")
    _ensure_column(con, "transmittals", "deleted_reason", "TEXT")

    # transmittal_items: store snapshot fields
    _ensure_column(con, "transmittal_items", "file_type", "TEXT")
    _ensure_column(con, "transmittal_items", "description", "TEXT")
    _ensure_column(con, "transmittal_items", "status", "TEXT")
    _ensure_column(con, "transmittal_items", "row_snapshot", "TEXT")  # JSON

    # helpful indexes
    _ensure_index(con, "idx_t_created", "CREATE INDEX idx_t_created ON transmittals(created_on);")
    _ensure_index(con, "idx_t_deleted", "CREATE INDEX idx_t_deleted ON transmittals(is_deleted);")
    _ensure_index(con, "idx_ti_doc", "CREATE INDEX idx_ti_doc ON transmittal_items(doc_id);")
    _ensure_index(con, "ux_ti_trans_doc",
                  "CREATE UNIQUE INDEX ux_ti_trans_doc ON transmittal_items(transmittal_id, doc_id);")



    con.commit(); con.close()

# ------------------------------ Project API (existing) ------------------------------

def upsert_project(db_path: Path,
                   project_code: str,
                   project_name: str,
                   root_path: Optional[str],
                   *,
                   client_company: Optional[str] = None,
                   client_reference: Optional[str] = None,
                   client_contact: Optional[str] = None,
                   end_user: Optional[str] = None) -> int:
    """
    Ensure exactly ONE row exists in 'projects' for this DB (id=1).
    Backwards compatible with older calls that only pass the first 3 params.
    """
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("""
            INSERT INTO projects(
                id, project_code, project_name, root_path,
                client_company, client_reference, client_contact, end_user
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_code     = excluded.project_code,
                project_name     = excluded.project_name,
                root_path        = excluded.root_path,
                client_company   = excluded.client_company,
                client_reference = excluded.client_reference,
                client_contact   = excluded.client_contact,
                end_user         = excluded.end_user
        """, (
            (project_code or "").strip(),
            (project_name or "").strip(),
            (root_path or "").strip(),
            (client_company or "").strip(),
            (client_reference or "").strip(),
            (client_contact or "").strip(),
            (end_user or "").strip(),
        ))
        con.commit(); con.close()
        return 1
    return _retry_write(_do)



def get_project(db_path: Path) -> Optional[Dict[str, Any]]:
    con = _connect(db_path)
    row = con.execute("""
        SELECT id, project_code, project_name, root_path,
               COALESCE(client_company,''), COALESCE(client_reference,''),
               COALESCE(client_contact,''), COALESCE(end_user,'')
          FROM projects
         LIMIT 1
    """).fetchone()
    con.close()
    if not row:
        return None
    return {
        "id": row[0],
        "project_code": row[1],
        "project_name": row[2],
        "root_path": row[3],
        "client_company": row[4],
        "client_reference": row[5],
        "client_contact": row[6],
        "end_user": row[7],
    }


# ------------------------------ Documents / Revisions (existing) ------------------------------

def upsert_document(db_path: Path, project_id: int, doc: Dict[str, Any]) -> int:
    """
    doc: {doc_id, doc_type, file_type, description, status, is_active}
    """
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("""INSERT INTO documents(project_id,doc_id,doc_type,file_type,description,status,is_active)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(project_id,doc_id) DO UPDATE SET
                         doc_type=excluded.doc_type,
                         file_type=excluded.file_type,
                         description=excluded.description,
                         status=excluded.status,
                         is_active=excluded.is_active""",
                    (project_id, doc["doc_id"].strip(), doc.get("doc_type",""), doc.get("file_type",""),
                     doc.get("description",""), doc.get("status",""), int(doc.get("is_active",1))))
        row = cur.execute("SELECT id FROM documents WHERE project_id=? AND doc_id=?",
                          (project_id, doc["doc_id"].strip())).fetchone()
        con.commit(); con.close()
        return int(row[0])
    return _retry_write(_do)

def add_revision(db_path: Path, document_id: int, rev: str, notes: str = "") -> int:
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO revisions(document_id,rev,notes) VALUES(?,?,?)",
                    (document_id, rev.strip(), notes))
        row = cur.execute("SELECT id FROM revisions WHERE document_id=? AND rev=?", (document_id, rev.strip())).fetchone()
        con.commit(); con.close()
        return int(row[0])
    return _retry_write(_do)

def add_revision_by_docid(db_path: Path, project_id: int, doc_id: str, rev: str, notes: str = "") -> int:
    """
    Insert revision for a document, addressing by (project_id, doc_id) instead of numeric document_id.
    Returns 1 if inserted (or already present), 0 if doc_id not found.
    """
    def _do():
        con = _connect(db_path); cur = con.cursor()
        row = cur.execute("SELECT id FROM documents WHERE project_id=? AND doc_id=?",
                          (project_id, doc_id.strip())).fetchone()
        if not row:
            con.close(); return 0
        document_id = int(row[0])
        cur.execute("INSERT OR IGNORE INTO revisions(document_id,rev,notes) VALUES(?,?,?)",
                    (document_id, rev.strip(), notes))
        con.commit(); con.close()
        return 1
    return _retry_write(_do)

def list_documents_with_latest(db_path: Path, project_id: int, state: str = "active") -> List[Dict[str, Any]]:
    con = _connect(db_path)
    where = "d.project_id=?"
    if state == "active":
        where += " AND d.is_active=1"
    elif state == "deleted":
        where += " AND d.is_active=0"
    rows = con.execute(f"""
        SELECT d.doc_id, d.doc_type, d.file_type, d.description, d.status,
               (SELECT r.rev FROM revisions r WHERE r.document_id=d.id ORDER BY r.id DESC LIMIT 1) AS latest_rev
        FROM documents d
        WHERE {where}
        ORDER BY d.doc_id COLLATE NOCASE
    """, (project_id,)).fetchall()
    con.close()
    cols = ["doc_id","doc_type","file_type","description","status","latest_rev"]
    return [dict(zip(cols, r)) for r in rows]

def list_statuses_for_project(db_path: Path, project_id: int) -> List[str]:
    con = _connect(db_path)
    rows = con.execute("SELECT DISTINCT COALESCE(status,'') FROM documents WHERE project_id=? ORDER BY 1",
                       (project_id,)).fetchall()
    con.close()
    return [r[0] for r in rows if (r and r[0])]

# ------------------------------ Snapshot helper (new) ------------------------------

def _snapshot_for_doc(cur: sqlite3.Cursor, project_id: int, doc_id: str) -> Dict[str, Any]:
    r = cur.execute("""
        SELECT doc_type, file_type, description, status,
               (SELECT rev FROM revisions WHERE document_id=documents.id ORDER BY id DESC LIMIT 1) AS latest_rev
          FROM documents
         WHERE project_id=? AND doc_id=?""", (project_id, doc_id.strip())).fetchone()
    if not r:
        return {}
    return {
        "doc_type": r[0] or "",
        "file_type": r[1] or "",
        "description": r[2] or "",
        "status": r[3] or "",
        "latest_rev": r[4] or ""
    }

# ------------------------------ Transmittals (ENHANCED) ------------------------------

def insert_transmittal(db_path: Path, header: Dict[str, Any], items: List[Dict[str, Any]]) -> int:
    """
    Existing behavior preserved. Enhancements:
    - Captures snapshot fields (file_type, status, description, plus row_snapshot JSON).
    - Accepts items with optional explicit fields; falls back to live register snapshot.
    """
    init_db(db_path)  # ensure new columns/indexes exist
    def _do():
        con = _connect(db_path); cur = con.cursor()
        # insert header (same as before)
        cur.execute(
            "INSERT INTO transmittals(project_code,number,title,client,created_by,created_on) VALUES(?,?,?,?,?,?)",
            (header["project_code"], header["number"], header["title"], header["client"],
             header["created_by"], header["created_on"])
        )
        tid = cur.lastrowid

        # discover project_id for snapshot
        prow = cur.execute("SELECT id FROM projects WHERE project_code=? LIMIT 1",
                           (header["project_code"],)).fetchone()
        project_id = int(prow[0]) if prow else None

        rows = []
        for it in (items or []):
            doc_id = it["doc_id"].strip()
            snap = dict(it.get("row_snapshot") or {})
            if (not snap) and project_id:
                snap = _snapshot_for_doc(cur, project_id, doc_id)
            rows.append((
                tid,
                doc_id,
                it.get("doc_type", snap.get("doc_type","")),
                it.get("revision") or snap.get("latest_rev","") or "",
                it.get("file_path","") or "",
                it.get("file_type", snap.get("file_type","")),
                it.get("description", snap.get("description","")),
                it.get("status", snap.get("status","")),
                json.dumps(snap or {}, ensure_ascii=False)
            ))

        # insert items (now including snapshot columns)
        cur.executemany("""
            INSERT INTO transmittal_items(transmittal_id,doc_id,doc_type,revision,file_path,
                                          file_type,description,status,row_snapshot)
            VALUES (?,?,?,?,?,?,?,?,?)""", rows)

        con.commit(); con.close()
        return tid
    return _retry_write(_do)

def list_transmittals(db_path: Path, include_deleted: bool = False) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    where = "" if include_deleted else "WHERE is_deleted=0"
    rows = con.execute(
        f"SELECT id,project_code,number,title,client,created_by,created_on,updated_on,is_deleted "
        f"FROM transmittals {where} ORDER BY created_on DESC, id DESC"
    ).fetchall()
    con.close()
    cols = ["id","project_code","number","title","client","created_by","created_on","updated_on","is_deleted"]
    return [dict(zip(cols, r)) for r in rows]

def get_transmittal_items(db_path: Path, transmittal_id: int) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute("""
        SELECT id,doc_id,doc_type,revision,file_path,file_type,description,status,row_snapshot
          FROM transmittal_items
         WHERE transmittal_id=?
         ORDER BY doc_id COLLATE NOCASE, id
    """, (transmittal_id,)).fetchall()
    con.close()
    cols = ["id","doc_id","doc_type","revision","file_path","file_type","description","status","row_snapshot"]
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(zip(cols, r))
        # keep your old keys intact + add new ones
        try:
            d["row_snapshot"] = json.loads(d.get("row_snapshot") or "{}")
        except Exception:
            d["row_snapshot"] = {}
        out.append(d)
    return out

def find_transmittal_id_by_number(db_path: Path, number: str) -> Optional[int]:
    con = _connect(db_path)
    row = con.execute("SELECT id FROM transmittals WHERE number=?", (number.strip(),)).fetchone()
    con.close()
    return int(row[0]) if row else None

def delete_transmittal_by_id(db_path: Path, transmittal_id: int) -> None:
    # Hard delete (purge) — existing behavior preserved
    def _do():
        con = _connect(db_path)
        try:
            con.execute("DELETE FROM transmittals WHERE id=?", (transmittal_id,))
            con.commit()
        finally:
            con.close()
    _retry_write(_do)

# -------- NEW: edit/soft delete helpers (non-breaking additions) --------

def update_transmittal_header(db_path: Path, transmittal_id: int, *,
                              title: Optional[str] = None,
                              client: Optional[str] = None) -> bool:
    sets = []
    vals: List[Any] = []
    if title is not None: sets.append("title=?"); vals.append(title)
    if client is not None: sets.append("client=?"); vals.append(client)
    if not sets: return False
    sets.append("updated_on=datetime('now')")
    sql = f"UPDATE transmittals SET {', '.join(sets)} WHERE id=?"
    vals.append(transmittal_id)
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute(sql, tuple(vals))
        changed = cur.rowcount > 0
        con.commit(); con.close(); return changed
    return _retry_write(_do)

def add_items_to_transmittal(db_path: Path, transmittal_id: int, items: List[Dict[str, Any]]) -> int:
    if not items: return 0
    def _do():
        con = _connect(db_path); cur = con.cursor()
        # get project_code -> project_id for snapshots
        row = cur.execute("SELECT project_code FROM transmittals WHERE id=?", (transmittal_id,)).fetchone()
        project_id = None
        if row:
            prow = cur.execute("SELECT id FROM projects WHERE project_code=? LIMIT 1", (row[0],)).fetchone()
            project_id = int(prow[0]) if prow else None

        inserted = 0
        for it in items:
            doc_id = it["doc_id"].strip()
            snap = dict(it.get("row_snapshot") or {})
            if (not snap) and project_id:
                snap = _snapshot_for_doc(cur, project_id, doc_id)

            cur.execute("""
                INSERT OR IGNORE INTO transmittal_items
                (transmittal_id,doc_id,doc_type,revision,file_path,file_type,description,status,row_snapshot)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (transmittal_id,
                 doc_id,
                 it.get("doc_type", snap.get("doc_type","")),
                 it.get("revision") or snap.get("latest_rev","") or "",
                 it.get("file_path","") or "",
                 it.get("file_type", snap.get("file_type","")),
                 it.get("description", snap.get("description","")),
                 it.get("status", snap.get("status","")),
                 json.dumps(snap or {}, ensure_ascii=False))
            )
            if cur.rowcount > 0:
                inserted += 1

        cur.execute("UPDATE transmittals SET updated_on=datetime('now') WHERE id=?", (transmittal_id,))
        con.commit(); con.close()
        return inserted
    return _retry_write(_do)

def remove_items_from_transmittal(db_path: Path, transmittal_id: int, doc_ids: List[str]) -> int:
    if not doc_ids: return 0
    doc_ids = [d.strip() for d in doc_ids if d and d.strip()]
    if not doc_ids: return 0
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.executemany("DELETE FROM transmittal_items WHERE transmittal_id=? AND doc_id=?",
                        [(transmittal_id, d) for d in doc_ids])
        removed = cur.rowcount
        cur.execute("UPDATE transmittals SET updated_on=datetime('now') WHERE id=?", (transmittal_id,))
        con.commit(); con.close()
        return removed
    return _retry_write(_do)

def soft_delete_transmittal(db_path: Path, transmittal_id: int, reason: str = "") -> bool:
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("""UPDATE transmittals
                          SET is_deleted=1,
                              deleted_on=datetime('now'),
                              deleted_reason=?
                        WHERE id=? AND is_deleted=0""", (reason or "", transmittal_id))
        changed = cur.rowcount > 0
        con.commit(); con.close(); return changed
    return _retry_write(_do)

def list_transmittals_for_doc(db_path: Path, project_id: int, doc_id: str,
                              include_deleted: bool = False) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute(f"""
        SELECT t.id, t.number, t.title, t.client, t.created_on, t.is_deleted,
               ti.revision, ti.status, ti.file_type, ti.description
          FROM transmittal_items ti
          JOIN transmittals t ON t.id = ti.transmittal_id
         WHERE ti.doc_id=? {'AND t.is_deleted=0' if not include_deleted else ''}
         ORDER BY t.created_on DESC, t.id DESC
    """, (doc_id.strip(),)).fetchall()
    con.close()
    cols = ["transmittal_id","number","title","client","created_on","is_deleted",
            "revision","status","file_type","description"]
    return [dict(zip(cols, r)) for r in rows]

def get_doc_submission_history(db_path: Path, project_id: int, doc_id: str) -> List[Dict[str, Any]]:
    # Convenience alias (stable shape)
    return list_transmittals_for_doc(db_path, project_id, doc_id, include_deleted=True)

# ------------------------------ Update helpers (existing) ------------------------------

def update_document_fields(db_path: Path, project_id: int, doc_id: str, fields: Dict[str, Any]) -> None:
    allowed = {"doc_type", "file_type", "description", "status", "is_active"}
    kv = {k: v for k, v in fields.items() if k in allowed}
    if not kv: return
    sets = ", ".join(f"{k}=?" for k in kv.keys())
    params = list(kv.values()) + [project_id, doc_id.strip()]
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute(f"UPDATE documents SET {sets} WHERE project_id=? AND doc_id=?", params)
        con.commit(); con.close()
    _retry_write(_do)

def bulk_update_documents_fields(db_path: Path, project_id: int, doc_ids: List[str], fields: Dict[str, Any]) -> int:
    allowed = {"doc_type", "file_type", "description", "status", "is_active"}
    kv = {k: v for k, v in fields.items() if k in allowed}
    if not kv or not doc_ids: return 0
    sets = ", ".join(f"{k}=?" for k in kv.keys())
    def _do():
        con = _connect(db_path); cur = con.cursor()
        for doc_id in doc_ids:
            cur.execute(f"UPDATE documents SET {sets} WHERE project_id=? AND doc_id=?",
                        list(kv.values()) + [project_id, doc_id.strip()])
        con.commit(); n = cur.rowcount; con.close()
        return n
    return _retry_write(_do)

def set_document_sp_link(db_path: Path, project_id: int, doc_id: str, sp_url: str, local_hint: str = "") -> None:
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("""UPDATE documents
                          SET sp_url=?, local_hint=?
                        WHERE project_id=? AND doc_id=?""",
                    (sp_url.strip(), (local_hint or "").strip(), project_id, doc_id.strip()))
        con.commit(); con.close()
    _do()

def get_document_sp_link(db_path: Path, project_id: int, doc_id: str) -> dict:
    con = _connect(db_path)
    row = con.execute("""SELECT sp_url, local_hint
                           FROM documents
                          WHERE project_id=? AND doc_id=?""",
                      (project_id, doc_id.strip())).fetchone()
    con.close()
    return {"sp_url": row[0] if row else "", "local_hint": row[1] if row else ""}

def list_documents_basic(db_path: Path, project_id: int):
    con = _connect(db_path)
    cur = con.cursor()
    rows = cur.execute("""
        SELECT doc_id, doc_type, file_type, description, status, COALESCE(sp_url,''), COALESCE(local_hint,'')
          FROM documents
         WHERE project_id=?
         ORDER BY doc_id
    """, (project_id,)).fetchall()
    con.close()
    return [{
        "doc_id": r[0], "doc_type": r[1], "file_type": r[2],
        "description": r[3], "status": r[4], "sp_url": r[5], "local_hint": r[6]
    } for r in rows]

def get_document_pk(db_path: Path, project_id: int, doc_id: str) -> Optional[int]:
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT id FROM documents WHERE project_id=? AND doc_id=?",
            (project_id, doc_id.strip())
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        con.close()

def add_revisions_for_docs(db_path: Path, project_id: int, rev_map: Dict[str, str]) -> int:
    """
    rev_map = {doc_id: 'A', ...}. Inserts (if new) each revision for each doc.
    Returns count of rows touched.
    """
    def _do():
        con = _connect(db_path); cur = con.cursor()
        touched = 0
        for doc_id, rev in (rev_map or {}).items():
            row = cur.execute(
                "SELECT id FROM documents WHERE project_id=? AND doc_id=?",
                (project_id, doc_id.strip())
            ).fetchone()
            if not row:
                continue
            cur.execute(
                "INSERT OR IGNORE INTO revisions(document_id,rev) VALUES(?,?)",
                (int(row[0]), rev.strip())
            )
            touched += 1
        con.commit(); con.close()
        return touched
    return _retry_write(_do)

# ------------------------------ Areas API (existing) ------------------------------

def list_areas(db_path: str, project_id: int):
    con = sqlite3.connect(db_path); cur = con.cursor()
    cur.execute("SELECT code, description FROM project_areas WHERE project_id=? ORDER BY code;", (project_id,))
    rows = cur.fetchall()
    con.close()
    return rows

def upsert_area(db_path: str, project_id: int, code: str, description: str):
    code = (code or "").strip().upper()
    description = (description or "").strip()
    if not code: return
    con = sqlite3.connect(db_path); cur = con.cursor()
    cur.execute("""
    INSERT INTO project_areas(project_id, code, description)
    VALUES (?, ?, ?)
    ON CONFLICT(project_id, code) DO UPDATE SET description=excluded.description;
    """, (project_id, code, description))
    con.commit(); con.close()

def delete_area(db_path: str, project_id: int, code: str):
    con = sqlite3.connect(db_path); cur = con.cursor()
    cur.execute("DELETE FROM project_areas WHERE project_id=? AND code=?;", (project_id, code))
    con.commit(); con.close()

# ------------------------------ Bulk update helpers (existing) ------------------------------

def bulk_update_docs(db_path: str, project_id: int, updates: dict):
    """
    updates: { "DOCID": {"revision": "B", "description": "New text"} , ... }
    Only supplied fields are updated. Skips unknown doc_ids.
    """
    if not updates:
        return {"matched": 0, "updated_rev": 0, "updated_desc": 0}

    # Build set of existing doc_ids up-front
    con = _connect(Path(db_path)); cur = con.cursor()
    cur.execute("SELECT doc_id FROM documents WHERE project_id=?", (project_id,))
    existing = {r[0].strip().upper() for r in cur.fetchall()}
    con.close()

    matched = upd_rev = upd_desc = 0
    for doc_id, fields in (updates or {}).items():
        did = (doc_id or "").strip().upper()
        if not did or did not in existing:
            continue
        matched += 1

        # Add revision into the revisions table (schema-consistent)
        if "revision" in fields and fields["revision"] not in (None, ""):
            upd_rev += add_revision_by_docid(Path(db_path), project_id, did, str(fields["revision"]).strip())

        # Update description on documents (allowed column)
        if "description" in fields and fields["description"] not in (None, ""):
            update_document_fields(Path(db_path), project_id, did, {"description": str(fields["description"]).strip()})
            upd_desc += 1

    return {"matched": matched, "updated_rev": upd_rev, "updated_desc": upd_desc}

def get_row_options(db_path: Path, project_id: int) -> Dict[str, list]:
    con = _connect(db_path); cur = con.cursor()
    rows = cur.execute(
        "SELECT kind, value FROM project_lists WHERE project_id=? ORDER BY kind, value",
        (project_id,)
    ).fetchall()
    con.close()
    out = {"doc_types": [], "file_types": [], "statuses": []}
    for kind, val in rows:
        if kind in out and val:
            out[kind].append(val)
    # keep unique + sorted
    for k in out:
        out[k] = sorted(set(out[k]))
    return out

def set_row_options(db_path: Path, project_id: int, options: Dict[str, list]) -> None:
    options = options or {}
    def _do():
        con = _connect(db_path); cur = con.cursor()
        # Simplest is replace-all for this project
        cur.execute("DELETE FROM project_lists WHERE project_id=?", (project_id,))
        for kind in ("doc_types", "file_types", "statuses"):
            vals = sorted(set(x for x in (options.get(kind) or []) if x))
            for v in vals:
                cur.execute("INSERT INTO project_lists(project_id, kind, value) VALUES (?,?,?)",
                            (project_id, kind, v))
        con.commit(); con.close()
        return True
    return _retry_write(_do)

def list_presets(db_path: Path, project_id: int) -> list[str]:
    con = _connect(db_path); cur = con.cursor()
    rows = cur.execute("SELECT name FROM presets WHERE project_id=? ORDER BY name", (project_id,)).fetchall()
    con.close()
    return [r[0] for r in rows]

def get_preset_doc_ids(db_path: Path, project_id: int, name: str) -> list[str]:
    con = _connect(db_path); cur = con.cursor()
    row = cur.execute("SELECT id FROM presets WHERE project_id=? AND name=?", (project_id, name)).fetchone()
    if not row:
        con.close(); return []
    preset_id = int(row[0])
    items = cur.execute("SELECT doc_id FROM preset_items WHERE preset_id=? ORDER BY doc_id", (preset_id,)).fetchall()
    con.close()
    return [i[0] for i in items]

def save_preset(db_path: Path, project_id: int, name: str, doc_ids: list[str]) -> bool:
    name = (name or "").strip()
    ids  = sorted(set(x for x in (doc_ids or []) if x))
    if not name:
        return False
    def _do():
        con = _connect(db_path); cur = con.cursor()
        # upsert preset header
        cur.execute("""
            INSERT INTO presets(project_id, name)
            VALUES (?, ?)
            ON CONFLICT(project_id, name) DO NOTHING
        """, (project_id, name))
        row = cur.execute("SELECT id FROM presets WHERE project_id=? AND name=?", (project_id, name)).fetchone()
        if not row:
            con.close(); return False
        preset_id = int(row[0])
        # replace items
        cur.execute("DELETE FROM preset_items WHERE preset_id=?", (preset_id,))
        for did in ids:
            cur.execute("INSERT OR IGNORE INTO preset_items(preset_id, doc_id) VALUES (?,?)", (preset_id, did))
        con.commit(); con.close(); return True
    return _retry_write(_do)

def delete_preset(db_path: Path, project_id: int, name: str) -> bool:
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("DELETE FROM presets WHERE project_id=? AND name=?", (project_id, name))
        changed = cur.rowcount > 0
        con.commit(); con.close()
        return changed
    return _retry_write(_do)

def rename_preset(db_path: Path, project_id: int, old_name: str, new_name: str) -> bool:
    if not old_name or not new_name or old_name == new_name:
        return False
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("UPDATE presets SET name=? WHERE project_id=? AND name=?", (new_name, project_id, old_name))
        changed = cur.rowcount > 0
        con.commit(); con.close()
        return changed
    return _retry_write(_do)
