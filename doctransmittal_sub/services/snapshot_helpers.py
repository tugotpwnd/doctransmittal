from __future__ import annotations
from pathlib import Path
from typing import Dict, List


def build_snapshot_items(
    *,
    items: List[dict],
    mapping: Dict[str, str],
) -> List[dict]:
    """
    Build a snapshot compatible with:
      - start_checkprint_batch
      - add_documents_to_checkprint
      - edit_transmittal_replace_items

    items: list of register-like dicts (must contain doc_id)
    mapping: { doc_id -> absolute file path }
    """
    snap: List[dict] = []

    for it in items:
        doc_id = (it.get("doc_id") or "").strip()
        if not doc_id:
            continue

        file_path = mapping.get(doc_id, "")

        snap.append({
            "doc_id": doc_id,
            "description": it.get("description", ""),
            "type": it.get("type", ""),
            "file_type": it.get("file_type", ""),
            "revision": it.get("revision", ""),
            "file_path": str(Path(file_path).resolve()) if file_path else "",
        })

    return snap
