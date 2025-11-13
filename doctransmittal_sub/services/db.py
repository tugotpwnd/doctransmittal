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
        comments TEXT DEFAULT '',
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

    # --- CheckPrint (new) ---
    '''CREATE TABLE IF NOT EXISTS checkprint_batches (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        code TEXT NOT NULL UNIQUE,           -- e.g. CP-TRN-001
        title TEXT NOT NULL,
        client TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_on TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress / completed / cancelled
        submitted_on TEXT,
        reviewer TEXT,
        reviewer_notes TEXT
    );''',

    '''CREATE TABLE IF NOT EXISTS checkprint_items (
        id INTEGER PRIMARY KEY,
        batch_id INTEGER NOT NULL REFERENCES checkprint_batches(id) ON DELETE CASCADE,
        doc_id TEXT NOT NULL,
        revision TEXT,
        base_name TEXT NOT NULL,            -- original basename without _CP_N
        cp_version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'pending',  -- pending / accepted / rejected
        submitter TEXT NOT NULL,
        reviewer TEXT,
        last_submitted_on TEXT,
        last_reviewed_on TEXT,
        last_reviewer_note TEXT,
        source_path TEXT NOT NULL,          -- actual source file (_CP_N) on disk
        cp_path TEXT NOT NULL               -- path to the copy in CheckPrint folder
    );''',

    '''CREATE TABLE IF NOT EXISTS checkprint_events (
        id INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES checkprint_items(id) ON DELETE CASCADE,
        happened_on TEXT NOT NULL,
        actor TEXT,
        event TEXT NOT NULL,          -- submitted / resubmitted / accepted / rejected / status_changed
        from_status TEXT,
        to_status TEXT,
        note TEXT
    );''',

    # --- RFIs (new) ---
    '''CREATE TABLE IF NOT EXISTS rfis (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        number TEXT NOT NULL,                        -- DOCUMENT NO.
        discipline TEXT,
        issued_to TEXT,
        issued_to_company TEXT,
        issued_from TEXT,
        issued_date TEXT,                            -- ISO text (YYYY-MM-DD or full datetime)
        respond_by TEXT,
        subject TEXT,
        response_from TEXT,
        response_company TEXT,
        response_date TEXT,
        response_status TEXT,
        comments TEXT,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        created_on TEXT NOT NULL DEFAULT (datetime('now')),
        updated_on TEXT
    );''',

]

def init_db(db_path: Path) -> None:
    con = _connect(db_path); cur = con.cursor()
    for ddl in DDL:
        cur.execute(ddl)

    # projects...
    _ensure_column(con, "projects", "client_reference", "TEXT")
    _ensure_column(con, "projects", "client_contact", "TEXT")
    _ensure_column(con, "projects", "end_user", "TEXT")
    _ensure_column(con, "projects", "client_company", "TEXT")

    # documents (existing extras)
    _ensure_column(con, "documents", "sp_url", "TEXT")
    _ensure_column(con, "documents", "local_hint", "TEXT")

    # documents (NEW)
    _ensure_column(con, "documents", "comments", "TEXT DEFAULT ''")   # <— ADD THIS
    _ensure_column(con, "documents", "updated_on", "TEXT")  # <— NEW: needed by rename_document_id()

    # transmittals...
    _ensure_column(con, "transmittals", "updated_on", "TEXT")
    _ensure_column(con, "transmittals", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "transmittals", "deleted_on", "TEXT")
    _ensure_column(con, "transmittals", "deleted_reason", "TEXT")

    # transmittal_items...
    _ensure_column(con, "transmittal_items", "file_type", "TEXT")
    _ensure_column(con, "transmittal_items", "description", "TEXT")
    _ensure_column(con, "transmittal_items", "status", "TEXT")
    _ensure_column(con, "transmittal_items", "row_snapshot", "TEXT")

    _ensure_index(con, "idx_t_created", "CREATE INDEX idx_t_created ON transmittals(created_on);")
    _ensure_index(con, "idx_t_deleted", "CREATE INDEX idx_t_deleted ON transmittals(is_deleted);")
    _ensure_index(con, "idx_ti_doc", "CREATE INDEX idx_ti_doc ON transmittal_items(doc_id);")
    _ensure_index(con, "ux_ti_trans_doc",
                  "CREATE UNIQUE INDEX ux_ti_trans_doc ON transmittal_items(transmittal_id, doc_id);")

    # --- inside init_db(...) after existing _ensure_column calls for rfis ---
    # rfis – add rich-text & plain-text fields for Background / Information Requested
    _ensure_column(con, "rfis", "background_html", "TEXT")
    _ensure_column(con, "rfis", "request_html", "TEXT")
    _ensure_column(con, "rfis", "background_text", "TEXT")
    _ensure_column(con, "rfis", "request_text", "TEXT")

    # rfis…
    _ensure_index(con, "ux_rfis_unique", "CREATE UNIQUE INDEX IF NOT EXISTS ux_rfis_unique ON rfis(project_id, number);")
    _ensure_index(con, "idx_rfis_project", "CREATE INDEX IF NOT EXISTS idx_rfis_project ON rfis(project_id);")

    _ensure_rfi_content_columns(db_path)

    con.commit(); con.close()

def list_rfis(db_path: Path, project_id: int) -> List[Dict[str, Any]]:
    """
    Return all RFIs for a given project, including content fields.
    """
    con = _connect(db_path)
    try:
        # Ensure backward compatibility (adds content columns if missing)
        cur = con.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info(rfis)")}
        for col in ("background_html","request_html","background_text","request_text"):
            if col not in cols:
                cur.execute(f"ALTER TABLE rfis ADD COLUMN {col} TEXT DEFAULT ''")
        con.commit()

        rows = cur.execute("""
            SELECT
                number, discipline, issued_to, issued_to_company, issued_from,
                issued_date, respond_by, subject,
                response_from, response_company, response_date, response_status,
                COALESCE(comments,''),
                COALESCE(background_html,''), COALESCE(request_html,''),
                COALESCE(background_text,''), COALESCE(request_text,'')
            FROM rfis
            WHERE project_id=? AND is_deleted=0
            ORDER BY number COLLATE NOCASE
        """, (int(project_id),)).fetchall()

        cols = [
            "number","discipline","issued_to","issued_to_company","issued_from",
            "issued_date","respond_by","subject",
            "response_from","response_company","response_date","response_status",
            "comments",
            "background_html","request_html","background_text","request_text"
        ]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def create_rfi(db_path: Path, project_id: int, rfi: Dict[str, Any]) -> bool:
    """
    Create a new RFI entry.
    Ensures all current content fields are supported.
    Returns True if inserted, False if duplicate or failed.
    """
    number = (rfi.get("number") or "").strip()
    if not number:
        return False

    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        try:
            # --- Ensure DB schema has content fields ---
            cols = {r[1] for r in cur.execute("PRAGMA table_info(rfis)")}
            for col in ("background_html","request_html","background_text","request_text"):
                if col not in cols:
                    cur.execute(f"ALTER TABLE rfis ADD COLUMN {col} TEXT DEFAULT ''")
            con.commit()

            cur.execute("""
                INSERT INTO rfis (
                    project_id, number,
                    discipline, issued_to, issued_to_company, issued_from,
                    issued_date, respond_by, subject,
                    response_from, response_company, response_date,
                    response_status, comments,
                    background_html, request_html, background_text, request_text
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                int(project_id), number,
                rfi.get("discipline",""), rfi.get("issued_to",""), rfi.get("issued_to_company",""),
                rfi.get("issued_from",""), rfi.get("issued_date",""), rfi.get("respond_by",""),
                rfi.get("subject",""), rfi.get("response_from",""), rfi.get("response_company",""),
                rfi.get("response_date",""), rfi.get("response_status",""), rfi.get("comments",""),
                rfi.get("background_html",""), rfi.get("request_html",""),
                rfi.get("background_text",""), rfi.get("request_text","")
            ))
            con.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"[create_rfi] Error: {e}")
            return False
        finally:
            con.close()

    return _retry_write(_do)


def update_rfi_fields(db_path: Path, project_id: int, number: str, fields: Dict[str, Any]) -> bool:
    number = (number or "").strip()
    if not (number and fields):
        return False
    allowed = {"discipline","issued_to","issued_to_company","issued_from",
               "issued_date","respond_by","subject",
               "response_from","response_company","response_date","response_status","comments",
               "background_html","request_html","background_text","request_text"}
    kv = {k: fields[k] for k in fields.keys() & allowed}
    if not kv:
        return False
    sets = ", ".join(f"{k}=?" for k in kv.keys())
    vals = list(kv.values()) + [int(project_id), number]
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute(f"UPDATE rfis SET {sets}, updated_on=datetime('now') WHERE project_id=? AND number=?", vals)
        changed = cur.rowcount > 0
        con.commit(); con.close()
        return changed
    return _retry_write(_do)

# --- add near your DB init code ---
def _ensure_rfi_content_columns(db_path):
    con = _connect(db_path)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(rfis)")}
        needed = {"background_html","background_text","request_html","request_text"}
        missing = needed - cols
        for c in sorted(missing):
            con.execute(f"ALTER TABLE rfis ADD COLUMN {c} TEXT DEFAULT ''")
        con.commit()
    finally:
        con.close()


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
    Insert revision for a document, addressing by (project_id, doc_id).
    Returns 1 if a row was written.
    """
    def _do():
        con = _connect(db_path); cur = con.cursor()
        row = cur.execute(
            "SELECT id FROM documents WHERE project_id=? AND doc_id=?",
            (project_id, doc_id.strip())
        ).fetchone()
        if not row:
            con.close(); return 0

        document_id = int(row[0])

        # If the same rev existed before, REPLACE it so it becomes the newest row (new id).
        cur.execute(
            "INSERT OR REPLACE INTO revisions(document_id,rev,notes,created_on) VALUES(?,?,?,date('now'))",
            (document_id, rev.strip(), notes)
        )

        # debug:
        try:
            print(f"[db] add_revision_by_docid doc_id={doc_id} rev='{rev.strip()}' -> rowcount={cur.rowcount}")
        except Exception:
            pass

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
        SELECT d.doc_id, d.doc_type, d.file_type, d.description,
               COALESCE(d.comments, '') AS comments,               -- NEW
               d.status,
               (SELECT r.rev FROM revisions r WHERE r.document_id=d.id ORDER BY r.id DESC LIMIT 1) AS latest_rev
        FROM documents d
        WHERE {where}
        ORDER BY d.doc_id COLLATE NOCASE
    """, (project_id,)).fetchall()
    con.close()
    cols = ["doc_id","doc_type","file_type","description","comments","status","latest_rev"]  # NEW order
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
                              client: Optional[str] = None,
                              created_on: Optional[str] = None,
                              created_by: Optional[str] = None) -> bool:
    sets = []
    vals: List[Any] = []
    if title is not None: sets.append("title=?"); vals.append(title)
    if client is not None: sets.append("client=?"); vals.append(client)
    if created_on is not None: sets.append("created_on=?"); vals.append(created_on)
    if created_by is not None: sets.append("created_by=?"); vals.append(created_by)
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
    allowed = {"doc_type", "file_type", "description", "comments", "status", "is_active"}  # added comments
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
    allowed = {"doc_type", "file_type", "description", "comments", "status", "is_active"}  # added comments
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
        SELECT doc_id, doc_type, file_type, description, COALESCE(comments,''), status,
               COALESCE(sp_url,''), COALESCE(local_hint,'')
          FROM documents
         WHERE project_id=?
         ORDER BY doc_id
    """, (project_id,)).fetchall()
    con.close()
    return [{
        "doc_id": r[0], "doc_type": r[1], "file_type": r[2],
        "description": r[3], "comments": r[4], "status": r[5],
        "sp_url": r[6], "local_hint": r[7]
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

# --- DB snapshot on open -----------------------------------------------------
def create_db_backup(db_path: Path, *, history_dir_name: str = "DB History", keep: int = 50) -> Path:
    """
    Make a point-in-time backup beside the DB, in '<DB folder>/DB History/'.
    Uses SQLite 'VACUUM INTO' so it's safe with WAL/journal modes.
    Returns the path of the backup (or Path() if skipped).
    """
    try:
        p = Path(db_path)
        if not p.exists() or not p.is_file():
            return Path()  # nothing to do for brand-new paths

        hist = p.parent / history_dir_name
        hist.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        base = p.stem  # e.g. 3025xxxx-00-REG-002
        out = hist / f"{base}_{ts}.db"

        # Prefer read-only; if that fails (old SQLite), fallback to normal mode
        try:
            con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5.0)
        except Exception:
            con = sqlite3.connect(str(p), timeout=5.0)

        try:
            con.execute("PRAGMA busy_timeout=5000;")
            # VACUUM INTO creates a compact, consistent copy atomically
            con.execute("VACUUM INTO ?", (str(out),))
        finally:
            con.close()

        # Simple rotation: keep newest N matching files
        files = sorted(hist.glob(f"{base}_*.db"))
        if keep and len(files) > keep:
            for old in files[:-keep]:
                try:
                    old.unlink()
                except Exception:
                    pass

        return out
    except Exception:
        # Never fail the app just because a backup failed
        return Path()


def rename_document_id(db_path: Path, project_id: int, old_id: str, new_id: str) -> bool:
    """
    Rename a document's ID within a project.
    - Case-insensitive uniqueness enforced: (project_id, doc_id COLLATE NOCASE) must be unique.
    - Cascades to any table that has BOTH columns: project_id, doc_id.
    Returns True on success; raises ValueError on any validation failure or conflict.
    """
    def _connect(p: Path) -> sqlite3.Connection:
        return sqlite3.connect(str(p))

    old = (old_id or "").strip()
    new = (new_id or "").strip()
    if not new:
        raise ValueError("Document ID cannot be empty.")

    # Optional: normalize your house style (UPPER, collapse spaces, etc.)
    new = " ".join(new.split()).upper()

    con = _connect(db_path)
    try:
        # 1) ensure a case-insensitive unique index on documents
        con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_pid_docid_nocase
            ON documents(project_id, doc_id COLLATE NOCASE)
        """)

        # 2) reject if target already exists (case-insensitive)
        hit = con.execute(
            "SELECT 1 FROM documents WHERE project_id=? AND UPPER(doc_id)=UPPER(?)",
            (int(project_id), new)
        ).fetchone()
        if hit:
            raise ValueError(f"Document ID '{new}' already exists in this project.")

        # 3) update main table
        cur = con.execute(
            "UPDATE documents SET doc_id=?, updated_on=datetime('now') "
            "WHERE project_id=? AND UPPER(doc_id)=UPPER(?)",
            (new, int(project_id), old.upper())
        )
        if cur.rowcount == 0:
            raise ValueError(f"Original document '{old}' not found.")

        # 4) cascade to any table that stores (project_id, doc_id)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})").fetchall()]
            if "project_id" in cols and "doc_id" in cols:
                con.execute(
                    f"UPDATE {t} SET doc_id=? WHERE project_id=? AND UPPER(doc_id)=UPPER(?)",
                    (new, int(project_id), old.upper())
                )

        con.commit()
        return True
    finally:
        con.close()


def list_rfis(db_path: Path, project_id: int) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    try:
        # Back-compat: add missing content columns if this DB is older
        cur = con.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info(rfis)")}
        for col in ("background_html","request_html","background_text","request_text"):
            if col not in cols:
                cur.execute(f"ALTER TABLE rfis ADD COLUMN {col} TEXT DEFAULT ''")
        con.commit()

        rows = cur.execute("""
            SELECT
                number, discipline, issued_to, issued_to_company, issued_from,
                issued_date, respond_by, subject,
                response_from, response_company, response_date, response_status,
                COALESCE(comments,''),
                COALESCE(background_html,''), COALESCE(request_html,''),
                COALESCE(background_text,''), COALESCE(request_text,'')
            FROM rfis
            WHERE project_id=? AND is_deleted=0
            ORDER BY number COLLATE NOCASE
        """, (int(project_id),)).fetchall()

        cols = [
            "number","discipline","issued_to","issued_to_company","issued_from",
            "issued_date","respond_by","subject",
            "response_from","response_company","response_date","response_status",
            "comments",
            "background_html","request_html","background_text","request_text"
        ]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()

def create_rfi(db_path: Path, project_id: int, rfi: Dict[str, Any]) -> bool:
    """
    Create a new RFI, including rich-text/plain-text content fields.
    Returns True on insert; False if duplicate exists.
    """
    number = (rfi.get("number") or "").strip()
    if not number:
        return False

    def _do():
        con = _connect(db_path)
        cur = con.cursor()
        try:
            # Back-compat: add missing columns for older DBs
            cols = {r[1] for r in cur.execute("PRAGMA table_info(rfis)")}
            for col in ("background_html","request_html","background_text","request_text"):
                if col not in cols:
                    cur.execute(f"ALTER TABLE rfis ADD COLUMN {col} TEXT DEFAULT ''")
            con.commit()

            cur.execute("""
                INSERT INTO rfis(
                    project_id, number,
                    discipline, issued_to, issued_to_company, issued_from,
                    issued_date, respond_by, subject,
                    response_from, response_company, response_date, response_status, comments,
                    background_html, request_html, background_text, request_text
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                int(project_id), number,
                rfi.get("discipline",""), rfi.get("issued_to",""), rfi.get("issued_to_company",""),
                rfi.get("issued_from",""), rfi.get("issued_date",""), rfi.get("respond_by",""),
                rfi.get("subject",""), rfi.get("response_from",""), rfi.get("response_company",""),
                rfi.get("response_date",""), rfi.get("response_status",""), rfi.get("comments",""),
                rfi.get("background_html",""), rfi.get("request_html",""),
                rfi.get("background_text",""), rfi.get("request_text","")
            ))
            con.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"[create_rfi] Error: {e}")
            return False
        finally:
            con.close()

    return _retry_write(_do)

def update_rfi_fields(db_path: Path, project_id: int, number: str, fields: Dict[str, Any]) -> bool:
    number = (number or "").strip()
    if not (number and fields):
        return False
    allowed = {
        "discipline","issued_to","issued_to_company","issued_from",
        "issued_date","respond_by","subject",
        "response_from","response_company","response_date","response_status","comments",
        "background_html","request_html","background_text","request_text"
    }
    kv = {k: fields[k] for k in fields.keys() & allowed}
    if not kv:
        return False
    sets = ", ".join(f"{k}=?" for k in kv.keys())
    vals = list(kv.values()) + [int(project_id), number]

    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute(f"UPDATE rfis SET {sets}, updated_on=datetime('now') WHERE project_id=? AND number=?", vals)
        changed = cur.rowcount > 0
        con.commit(); con.close()
        return changed

    return _retry_write(_do)


# ====================== CheckPrint helpers ======================

def create_checkprint_batch(
    db_path: Path,
    *,
    project_id: int,
    code: str,
    title: str,
    client: str,
    created_by: str,
    created_on: str,
    items: List[Dict[str, Any]],
) -> int:
    """
    Create a CheckPrint batch + items.
    items = [{
        doc_id, revision, base_name, cp_version,
        status, submitter, source_path, cp_path,
        last_submitted_on
    }, ...]
    """
    init_db(db_path)

    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("""
            INSERT INTO checkprint_batches(
                project_id, code, title, client,
                created_by, created_on, status
            ) VALUES (?,?,?,?,?,?, 'in_progress')
        """, (project_id, code, title, client, created_by, created_on))
        batch_id = cur.lastrowid

        rows = []
        for it in items:
            rows.append((
                batch_id,
                it["doc_id"],
                it.get("revision") or "",
                it["base_name"],
                int(it.get("cp_version", 1)),
                it.get("status", "pending"),
                it.get("submitter", created_by),
                it.get("reviewer") or "",
                it.get("last_submitted_on", created_on),
                it.get("last_reviewed_on") or "",
                it.get("last_reviewer_note") or "",
                it["source_path"],
                it["cp_path"],
            ))
        cur.executemany("""
            INSERT INTO checkprint_items(
                batch_id, doc_id, revision, base_name, cp_version,
                status, submitter, reviewer, last_submitted_on,
                last_reviewed_on, last_reviewer_note,
                source_path, cp_path
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)

        con.commit(); con.close()
        return batch_id

    return _retry_write(_do)


def list_checkprint_batches(db_path: Path, project_id: int) -> List[Dict[str, Any]]:
    init_db(db_path)
    con = _connect(db_path)
    rows = con.execute("""
        SELECT id, code, title, client, created_by, created_on,
               status, submitted_on, reviewer, reviewer_notes
          FROM checkprint_batches
         WHERE project_id=?
         ORDER BY created_on DESC, id DESC
    """, (int(project_id),)).fetchall()
    con.close()
    cols = ["id","code","title","client","created_by","created_on",
            "status","submitted_on","reviewer","reviewer_notes"]
    return [dict(zip(cols, r)) for r in rows]


def get_checkprint_items(db_path: Path, batch_id: int) -> List[Dict[str, Any]]:
    init_db(db_path)
    con = _connect(db_path)
    rows = con.execute("""
        SELECT id, batch_id, doc_id, revision, base_name, cp_version,
               status, submitter, reviewer, last_submitted_on,
               last_reviewed_on, last_reviewer_note,
               source_path, cp_path
          FROM checkprint_items
         WHERE batch_id=?
         ORDER BY doc_id, cp_version
    """, (int(batch_id),)).fetchall()
    con.close()
    cols = [
        "id","batch_id","doc_id","revision","base_name","cp_version",
        "status","submitter","reviewer","last_submitted_on",
        "last_reviewed_on","last_reviewer_note",
        "source_path","cp_path"
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_latest_checkprint_versions(
    db_path: Path,
    project_id: int,
    doc_ids: List[str],
) -> Dict[str, int]:
    """
    Returns {doc_id: max_cp_version} across all batches for this project.
    """
    if not doc_ids:
        return {}
    init_db(db_path)
    con = _connect(db_path)
    placeholders = ",".join("?" for _ in doc_ids)
    rows = con.execute(f"""
        SELECT ci.doc_id, MAX(ci.cp_version)
          FROM checkprint_items ci
          JOIN checkprint_batches cb ON cb.id = ci.batch_id
         WHERE cb.project_id=? AND ci.doc_id IN ({placeholders})
         GROUP BY ci.doc_id
    """, (int(project_id), *doc_ids)).fetchall()
    con.close()
    return {str(r[0]): int(r[1]) for r in rows}


def update_checkprint_item_status(
    db_path: Path,
    item_id: int,
    *,
    status: Optional[str] = None,
    reviewer: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """
    Update status / reviewer / note for a single item.
    """
    fields: Dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
        fields["last_reviewed_on"] = "datetime('now')"  # special
    if reviewer is not None:
        fields["reviewer"] = reviewer
    if note is not None:
        fields["last_reviewer_note"] = note

    if not fields:
        return

    sets_sql = []
    vals: List[Any] = []
    for k,v in fields.items():
        if v == "datetime('now')":
            sets_sql.append(f"{k}=datetime('now')")
        else:
            sets_sql.append(f"{k}=?"); vals.append(v)
    vals.append(int(item_id))

    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute(f"""
            UPDATE checkprint_items
               SET {", ".join(sets_sql)}
             WHERE id=?
        """, vals)
        con.commit(); con.close()

    _retry_write(_do)


def append_checkprint_event(
    db_path: Path,
    *,
    item_id: int,
    actor: str,
    event: str,
    from_status: Optional[str],
    to_status: Optional[str],
    note: str = "",
) -> None:
    def _do():
        con = _connect(db_path); cur = con.cursor()
        cur.execute("""
            INSERT INTO checkprint_events(
                item_id, happened_on, actor, event, from_status, to_status, note
            ) VALUES (?, datetime('now'), ?, ?, ?, ?, ?)
        """, (int(item_id), actor, event, from_status, to_status, note))
        con.commit(); con.close()
    _retry_write(_do)
