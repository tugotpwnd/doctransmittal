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
    override = os.environ.get("DOCTRANS_LIBRARY_ROOT")
    if override:
        p = Path(override)
        if p.exists():
            return p

    org_name = org or os.environ.get("DOCTRANS_ORG", DEFAULT_ORG)
    base = Path.home() / org_name

    # If caller forced a library name
    if library:
        preferred = base / library
        if preferred.exists():
            return preferred

    # Known variants in your environment
    candidates = [
        "Maxwell - Documents",
        "Maxwell Industries - Documents",
        "Maxwell Documents",
        "Documents",
        "Maxwell - 1. Projects",
        "1. Projects",
    ]

    # Simply return the first existing candidate
    for name in candidates:
        root = base / name
        if root.exists():
            return root

    # Last-resort: return the first conventional path (may not exist)
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
