# doctransmittal_sub/core/paths.py
from __future__ import annotations
from pathlib import Path
import platform, os

APP_NAME = "DocumentTransmittal"

def app_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / APP_NAME
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME

def logs_dir() -> Path:
    p = app_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p

def project_state_dir(project_root: Path) -> Path:
    # NEW: if caller already passed the hidden state dir, don't nest again
    if project_root.name.lower() == ".docutrans":
        p = project_root
    else:
        p = project_root / ".docutrans"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- SharePoint / OneDrive helpers ------------------------------------------
# doctransmittal_sub/core/paths.py
from pathlib import Path
import os
from typing import Optional

DEFAULT_ORG = "Maxwell Industries Pty Ltd"

def company_library_root(org: Optional[str] = None, library: Optional[str] = None) -> Path:
    """
    Returns the local OneDrive 'company library' root, e.g.
    C:\\Users\\<you>\\Maxwell Industries Pty Ltd\\<Library-Name>\\

    Backward-compatible signature: (org=None, library=None)
    - If 'library' is given, prefer that folder.
    - Otherwise, auto-detect by known names and by the presence of '0. MIMS'.
    - Env overrides:
        DOCTRANS_LIBRARY_ROOT -> absolute path to use directly
        DOCTRANS_ORG          -> override the org folder name
    """
    # 0) Hard override
    override = os.environ.get("DOCTRANS_LIBRARY_ROOT")
    if override:
        p = Path(override)
        if p.exists():
            return p

    org_name = org or os.environ.get("DOCTRANS_ORG", DEFAULT_ORG)
    base = Path.home() / org_name

    # If caller supplied a library folder name, try it first
    if library:
        preferred = base / library
        if (preferred / "0. MIMS").exists() or preferred.exists():
            return preferred

    # Known variants we’ve seen in the wild
    candidates = [
        "Maxwell - Documents",
        "Maxwell Industries - Documents",
        "Maxwell Documents",
        "Documents",
    ]

    # Prefer candidates that have the anchor folder
    for name in candidates:
        root = base / name
        if (root / "0. MIMS").exists():
            return root

    # Otherwise, first existing candidate
    for name in candidates:
        root = base / name
        if root.exists():
            return root

    # Wildcard scan for any "*Documents*" that has '0. MIMS'
    try:
        for d in base.iterdir():
            if d.is_dir() and "documents" in d.name.lower():
                if (d / "0. MIMS").exists():
                    return d
    except Exception:
        pass

    # Last-resort: return the first conventional path (may not exist yet)
    return base / candidates[0]


def resolve_company_library_path(relpath: Path | str,
                                 org: Optional[str] = None,
                                 library: Optional[str] = None) -> Path:
    """
    Join a relative path (typically starting at '0. MIMS/...') to the detected
    company library root.
    """
    root = company_library_root(org=org, library=library)
    return (root / Path(relpath)).resolve()
