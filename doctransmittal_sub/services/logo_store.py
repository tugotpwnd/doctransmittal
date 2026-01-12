from __future__ import annotations
import shutil, os
from pathlib import Path
from typing import List, Iterable

# All logos live next to the DB in a folder named "DM-Logos"
LOGOS_DIRNAME = "DM-Logos"
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".bmp", ".tif", ".tiff", ".gif"}

def logos_dir_for_db(db_path: Path) -> Path:
    db_path = Path(db_path)
    return db_path.parent / LOGOS_DIRNAME

def list_logos(db_path: Path) -> List[Path]:
    root = logos_dir_for_db(db_path)
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTS], key=lambda p: p.name.lower())

def _unique_name(dirpath: Path, name: str) -> Path:
    base = Path(name).stem
    ext  = Path(name).suffix
    candidate = dirpath / f"{base}{ext}"
    n = 1
    while candidate.exists():
        candidate = dirpath / f"{base} ({n}){ext}"
        n += 1
    return candidate

def add_logos(db_path: Path, sources: Iterable[Path]) -> List[Path]:
    """Copy selected images into DM-Logos; returns list of copied target paths."""
    root = logos_dir_for_db(db_path)
    root.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []
    for src in sources:
        src = Path(src)
        if not src.exists() or src.suffix.lower() not in ALLOWED_EXTS:
            continue
        dst = _unique_name(root, src.name)
        shutil.copy2(str(src), str(dst))
        copied.append(dst)
    return copied

def remove_logos(db_path: Path, filenames: Iterable[str]) -> int:
    """Remove files (by filename) from DM-Logos; returns count removed."""
    root = logos_dir_for_db(db_path)
    count = 0
    for name in filenames:
        p = root / name
        try:
            if p.exists() and p.is_file():
                p.unlink()
                count += 1
        except Exception:
            pass
    return count
