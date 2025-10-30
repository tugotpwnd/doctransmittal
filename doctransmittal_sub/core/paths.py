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
def company_library_root(org: str = "Maxwell Industries Pty Ltd",
                         library: str = "Maxwell - Documents") -> Path:
    """
    Try to find the local sync root for the org+library on this machine.
    We check a few common layouts used by OneDrive/SharePoint.
    """
    home = Path.home()
    candidates = [
        home / org / library,                          # C:\Users\you\Maxwell Industries Pty Ltd\Maxwell - Documents
        home / f"OneDrive - {org}" / library,         # C:\Users\you\OneDrive - Maxwell Industries Pty Ltd\Maxwell - Documents
        home / f"SharePoint" / library,               # fallback-ish
    ]
    # Also check OneDriveCommercial env var if present
    odv = os.environ.get("OneDriveCommercial", "")
    if odv:
        candidates.append(Path(odv) / library)

    for c in candidates:
        if c.exists():
            return c

    # last resort: return the first candidate (even if not exists) so caller can still join rel paths
    return candidates[0]


def resolve_company_library_path(rel: str,
                                 org: str = "Maxwell Industries Pty Ltd",
                                 library: str = "Maxwell - Documents") -> Path:
    """
    Convert a stored relative path (non-user-specific) into this user's absolute path.
    """
    root = company_library_root(org, library)
    return (root / Path(rel)).resolve()
