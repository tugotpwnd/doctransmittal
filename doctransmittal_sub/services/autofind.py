from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Iterable
import re

def suggest_mapping(doc_ids: List[str], search_roots: List[Path], prefer_revision_suffix: bool = True,
                    extensions: List[str] | None = None) -> Dict[str, List[Tuple[Path, float, Optional[str]]]]:
    if extensions: extensions = [e.lower() for e in extensions]
    out: Dict[str, List[Tuple[Path, float, Optional[str]]]] = {d: [] for d in doc_ids}
    trailing_re = re.compile(r"(?i)[_\\-\\s]([A-Za-z0-9]+)$")
    def consider(doc: str, p: Path):
        stem = p.stem; conf, rev = 0.0, None
        if stem == doc: conf = 0.9
        elif stem.startswith(doc): conf = 0.8
        elif doc in stem: conf = 0.6
        m = trailing_re.search(stem)
        if m:
            rev = m.group(1).upper()
            if stem.startswith(doc) and (prefer_revision_suffix or rev):
                conf = max(conf, 1.0)
        out.setdefault(doc, []).append((p, conf, rev))
    for root in search_roots:
        if not root.exists(): continue
        for p in root.rglob("*"):
            if not p.is_file(): continue
            if extensions and p.suffix.lower() not in extensions: continue
            name = p.name
            for doc in doc_ids:
                if doc in name: consider(doc, p)
    for k, lst in out.items(): lst.sort(key=lambda t: (-t[1], t[0].name.lower()))
    return out


def find_docid_rev_matches(
    doc_revs: Iterable[Tuple[str, str]],
    search_roots: List[Path],
    extensions: List[str] | None = None
) -> Dict[str, Path]:
    """
    Return {doc_id: Path} for files whose STEM == f"{DocID}_{Revision}" (case-insensitive).
    If multiple matches exist, the first encountered (sorted by name) is used.
    """
    if extensions:
        extensions = [e.lower() for e in extensions]
    # Build wanted stems and a reverse map
    wanted: Dict[str, str] = {}  # STEM -> doc_id
    for doc, rev in (doc_revs or []):
        if not doc or not rev:
            continue
        stem = f"{doc}_{rev}".upper()
        wanted[stem] = doc

    found: Dict[str, Path] = {}
    for root in search_roots or []:
        if not root or not Path(root).exists():
            continue
        # predictable order for determinism
        files = sorted(root.rglob("*"), key=lambda p: p.name.lower())
        for p in files:
            if not p.is_file():
                continue
            if extensions and p.suffix.lower() not in extensions:
                continue
            stem = p.stem.upper()
            if stem in wanted and wanted[stem] not in found:
                found[wanted[stem]] = p
    return found