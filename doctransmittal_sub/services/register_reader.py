from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import pandas as pd, re
from ..models.document import DocumentRow

REV_TOKEN_RE = re.compile(r"(?i)(?:rev\\s*)?([A-Za-z]+\\d*|\\d+[A-Za-z]*|[A-Za-z]|\\d+)$")

def col_to_idx(col_letter: str) -> int:
    col_letter = col_letter.strip().upper()
    n = 0
    for ch in col_letter: n = n * 26 + (ord(ch) - ord('A') + 1)
    return n - 1

def rightmost_nonempty(values) -> Optional[str]:
    for v in reversed(values):
        if pd.notna(v) and str(v).strip() != "":
            return str(v).strip()
    return None

def parse_latest_token(s: Optional[str]) -> str:
    if not s: return ""
    m = REV_TOKEN_RE.search(str(s).strip())
    return m.group(1).upper() if m else str(s).strip()

def read_register(path: Path, sheet_name: str = "MI Documents",
                  doc_id_col: str = "B", doc_type_col: str = "C",
                  file_type_col: str = "D", description_col: str = "E", status_col: str = "F",
                  start_row: int = 10, rev_start_col: str = "G", rev_end_col: str = "BZ") -> List[DocumentRow]:
    df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", dtype=str).fillna("")
    start_idx = max(0, start_row - 2)
    rev_start_idx, rev_end_idx = col_to_idx(rev_start_col), col_to_idx(rev_end_col)
    rows: List[DocumentRow] = []
    for i in range(start_idx, len(df)):
        def get(col): idx = col_to_idx(col); return str(df.iloc[i, idx]) if idx < df.shape[1] else ""
        doc_id = get(doc_id_col).strip()
        if not doc_id: continue
        doc_type, file_type, descr, status = get(doc_type_col).strip(), get(file_type_col).strip(), get(description_col).strip(), get(status_col).strip()
        rev_slice = df.iloc[i, rev_start_idx : min(df.shape[1], rev_end_idx + 1)]
        latest_raw = rightmost_nonempty(list(rev_slice.values))
        token = parse_latest_token(latest_raw)
        rows.append(DocumentRow(doc_id, doc_type, file_type, descr, status, latest_raw or "", token or "", i + 1))
    return rows