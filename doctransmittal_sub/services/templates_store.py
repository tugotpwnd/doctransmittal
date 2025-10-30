from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional
import json

from doctransmittal_sub.core.paths import app_data_dir, resolve_company_library_path

# ---------------------------------------------------------------------------
# JSON location
# ---------------------------------------------------------------------------
def templates_dir() -> Path:
    return app_data_dir() / "templates"

def templates_json_path() -> Path:
    return templates_dir() / "templates.json"

# ---------------------------------------------------------------------------
# Categories (routing)
# ---------------------------------------------------------------------------
CATEGORIES = [
    ("document", "Report/Document/Register"),
    ("schedule", "Schedule"),
    ("drawing",  "Drawing"),
]
CATEGORY_KEYS = {k for k, _ in CATEGORIES}
CATEGORY_LABELS = {k: v for k, v in CATEGORIES}
CATEGORY_FROM_LABEL = {v: k for k, v in CATEGORIES}

# ---------------------------------------------------------------------------
# Document kind (processing)
# ---------------------------------------------------------------------------
KINDS = [
    ("excel", "Excel"),
    ("word",  "Word"),
]
KIND_KEYS = {k for k, _ in KINDS}
KIND_LABELS = {k: v for k, v in KINDS}
KIND_FROM_LABEL = {v: k for k, v in KINDS}

DEFAULT_CATEGORY = "document"
DEFAULT_KIND = "excel"
DEFAULT_ORG = "Maxwell Industries Pty Ltd"
DEFAULT_LIBRARY = "Maxwell - Documents"

def _norm_category(cat: Optional[str]) -> str:
    cat = (cat or "").strip().lower()
    return cat if cat in CATEGORY_KEYS else DEFAULT_CATEGORY

def _norm_kind(kind: Optional[str]) -> str:
    k = (kind or "").strip().lower()
    return k if k in KIND_KEYS else DEFAULT_KIND

# ---------------------------------------------------------------------------
# JSON schema:
# {
#   "org": "Maxwell Industries Pty Ltd",
#   "library": "Maxwell - Documents",
#   "templates": [
#     {
#       "doc_id": "MI-DT-EN-004",
#       "description": "Design Report (Word)",
#       "revision": "1.0",
#       "category": "document",   # routing
#       "kind": "word",           # processing ("excel"|"word")
#       "relpath": "0. MIMS/4. Document Templates/6 Engineering/MI-...docx"
#     }
#   ]
# }
# ---------------------------------------------------------------------------

def load_templates(path: Optional[Path] = None) -> List[Dict]:
    p = Path(path) if path else templates_json_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    org = str(data.get("org") or DEFAULT_ORG)
    lib = str(data.get("library") or DEFAULT_LIBRARY)
    items = list(data.get("templates") or [])
    out: List[Dict] = []
    for t in items:
        doc_id = str(t.get("doc_id", "")).strip()
        description = str(t.get("description", "")).strip()
        revision = str(t.get("revision", "")).strip()
        category = _norm_category(t.get("category"))
        kind     = _norm_kind(t.get("kind"))
        relpath = str(t.get("relpath", "")).strip().replace("\\", "/")
        abs_path = str(resolve_company_library_path(relpath, org=org, library=lib))
        out.append({
            "doc_id": doc_id,
            "description": description,
            "revision": revision,
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, CATEGORY_LABELS[DEFAULT_CATEGORY]),
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, KIND_LABELS[DEFAULT_KIND]),
            "relpath": relpath,
            "abs_path": abs_path,
        })
    return out


def save_templates(items: List[Dict],
                   *,
                   org: str = DEFAULT_ORG,
                   library: str = DEFAULT_LIBRARY,
                   path: Optional[Path] = None) -> None:
    """
    Persist list of dicts with minimal keys.
    """
    p = Path(path) if path else templates_json_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {
        "org": org,
        "library": library,
        "templates": [
            {
                "doc_id": (it.get("doc_id") or "").strip(),
                "description": (it.get("description") or "").strip(),
                "revision": (it.get("revision") or "").strip(),
                "category": _norm_category(it.get("category") or it.get("category_label")),
                "kind": _norm_kind(it.get("kind") or it.get("kind_label")),
                "relpath": (it.get("relpath") or "").strip().replace("\\", "/"),
            }
            for it in (items or [])
            if any((it.get("doc_id"), it.get("description"), it.get("relpath")))
        ]
    }
    p.write_text(json.dumps(serialisable, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_abs_path(item: Dict,
                     org: str = DEFAULT_ORG,
                     library: str = DEFAULT_LIBRARY) -> Path:
    rel = str(item.get("relpath", "")).strip()
    return resolve_company_library_path(rel, org=org, library=library)
