from __future__ import annotations
from dataclasses import dataclass
@dataclass
class DocumentRow:
    doc_id: str
    doc_type: str
    file_type: str
    description: str
    status: str
    latest_rev_raw: str
    latest_rev_token: str
    row_index: int