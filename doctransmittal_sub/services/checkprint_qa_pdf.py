# checkprint_qa_pdf.py
# ------------------------------------------------------------------
# CheckPrint Quality Assurance PDF export.
# Uses the same ReportLab layout primitives as the Document Register
# print output so the report remains visually consistent.
# ------------------------------------------------------------------

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

try:
    from .db import get_project, _connect
    from .pdf_layout import (
        BODY,
        BODY_SMALL,
        TITLE,
        COL_PRIMARY,
        draw_footer,
        draw_header,
        kv_block,
        _ensure_fonts,
        _resource_path,
        table_with_headers_and_widths,
    )
    from .receipt_pdf import _build_doc, _safe_str
except Exception:  # pragma: no cover - fallback for alternate packaging/import contexts
    from ..services.db import get_project, _connect  # type: ignore
    from ..services.pdf_layout import (  # type: ignore
        BODY,
        BODY_SMALL,
        TITLE,
        COL_PRIMARY,
        draw_footer,
        draw_header,
        kv_block,
        _ensure_fonts,
        _resource_path,
        table_with_headers_and_widths,
    )
    from ..services.receipt_pdf import _build_doc, _safe_str  # type: ignore


_FINAL_STATUS_LABELS = {
    "pending": "Ongoing",
    "in_progress": "Ongoing",
    "submitted": "Ongoing",
    "awaiting_review": "Ongoing",
    "completed": "Completed",
    "cancelled": "Cancelled",
}


def _initials(name: Optional[str]) -> str:
    """Return compact initials suitable for audit-table display."""
    txt = (name or "").strip()
    if not txt:
        return "-"
    if "@" in txt and " " not in txt:
        txt = txt.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
    parts = [p for p in txt.replace(",", " ").split() if p]
    if not parts:
        return "-"
    if len(parts) == 1:
        token = parts[0]
        return token[:2].upper() if len(token) > 1 else token.upper()
    return "".join(p[0].upper() for p in parts[:3])


def _fmt_date(value: Any) -> str:
    raw = _safe_str(value)
    if not raw:
        return "-"
    # SQLite datetime('now') generally returns YYYY-MM-DD HH:MM:SS.
    # Keep the report compact by showing the date only where possible.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return raw[:10] if len(raw) >= 10 else raw


def _status_label(status: Any) -> str:
    return _FINAL_STATUS_LABELS.get(_safe_str(status).lower(), _safe_str(status).replace("_", " ").title() or "Ongoing")


def _load_batch_and_items(db_path: Path, batch_id: int) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    con = _connect(db_path)
    try:
        b_row = con.execute(
            """
            SELECT id, project_id, code, title, client, created_by, created_on,
                   status, submitted_on, reviewer, reviewer_notes
              FROM checkprint_batches
             WHERE id=?
            """,
            (int(batch_id),),
        ).fetchone()
        if not b_row:
            raise RuntimeError("CheckPrint batch not found")

        b_cols = [
            "id",
            "project_id",
            "code",
            "title",
            "client",
            "created_by",
            "created_on",
            "status",
            "submitted_on",
            "reviewer",
            "reviewer_notes",
        ]
        batch = dict(zip(b_cols, b_row))

        rows = con.execute(
            """
            SELECT
                ci.id,
                ci.doc_id,
                ci.revision,
                ci.cp_version,
                ci.status,
                ci.reviewer_status,
                ci.submitter,
                ci.reviewer,
                ci.approver,
                ci.last_submitted_on,
                ci.last_reviewed_on,
                ci.last_approved_on,
                COALESCE(d.doc_type, '') AS doc_type,
                COALESCE(d.file_type, '') AS file_type,
                COALESCE(d.description, '') AS description
              FROM checkprint_items ci
              JOIN checkprint_batches cb ON cb.id = ci.batch_id
         LEFT JOIN documents d
                ON d.project_id = cb.project_id
               AND d.doc_id = ci.doc_id
             WHERE ci.batch_id=?
               AND COALESCE(ci.state, 'active')='active'
             ORDER BY ci.doc_id COLLATE NOCASE, ci.cp_version, ci.id
            """,
            (int(batch_id),),
        ).fetchall()
        i_cols = [
            "id",
            "doc_id",
            "revision",
            "cp_version",
            "status",
            "reviewer_status",
            "submitter",
            "reviewer",
            "approver",
            "last_submitted_on",
            "last_reviewed_on",
            "last_approved_on",
            "doc_type",
            "file_type",
            "description",
        ]
        items = [dict(zip(i_cols, r)) for r in rows]
        return batch, items
    finally:
        con.close()


def _title_logo_row(doc, title_text: str):
    title_para = Paragraph(title_text, TITLE)
    logo_elem = None
    logo_max_h = 18 * mm
    logo_col_w = 60 * mm

    try:
        logo_path = _resource_path("resources/_MI_Logo_SVG.svg")
        if str(logo_path).lower().endswith(".svg"):
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(str(logo_path))
            if drawing and drawing.width and drawing.height:
                scale = min(logo_col_w / drawing.width, logo_max_h / drawing.height)
                drawing.scale(scale, scale)
                drawing.width *= scale
                drawing.height *= scale
                logo_elem = drawing
        else:
            from reportlab.platypus import Image as RLImage

            img = RLImage(str(logo_path))
            scale = min(logo_col_w / img.imageWidth, logo_max_h / img.imageHeight)
            img.drawWidth = img.imageWidth * scale
            img.drawHeight = img.imageHeight * scale
            logo_elem = img
    except Exception:
        logo_elem = None

    row = Table(
        [[title_para, logo_elem or ""]],
        colWidths=[doc.width - logo_col_w, logo_col_w],
        rowHeights=[logo_max_h],
        hAlign="LEFT",
    )
    row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return row


def export_checkprint_qa_pdf(
    out_pdf: Path,
    *,
    db_path: Path,
    batch_id: int,
    generated_by: str = "",
) -> Path:
    """
    Export a CheckPrint Quality Assurance Document PDF for one batch.

    Columns intentionally match the requested QA overview:
      Document No. / Type / File Type / Description / Submitter / Reviewer /
      Approver / CheckPrint Iterations / Approval Date
    """
    _ensure_fonts()

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    db_path = Path(db_path)
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata missing")

    batch, items = _load_batch_and_items(db_path, int(batch_id))
    project_title = _safe_str(proj.get("project_name"))
    project_code = _safe_str(proj.get("project_code"))
    batch_code = _safe_str(batch.get("code")) or f"CP-{batch_id}"
    batch_title = _safe_str(batch.get("title"))
    state_label = _status_label(batch.get("status"))

    def _on_page(canvas, doc):
        draw_header(canvas, project=project_title, doc_id=batch_code)
        draw_footer(
            canvas,
            left_text="Maxwell Industries Pty Ltd • ABN 95 654 787 210",
            right_text=f"Page {doc.page}",
            mini_logo=Path(_resource_path("resources/_MI_Logo_Small_SVG.svg")),
        )

    doc, _ = _build_doc(
        out_pdf,
        pagesize=landscape(A3),
        top_margin=30 * mm,
        bottom_margin=20 * mm,
        on_page=_on_page,
    )

    flow: List[Any] = []
    flow.append(_title_logo_row(doc, "CheckPrint Quality Assurance Document"))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(f"<b>{state_label}</b>", BODY))
    if batch_title:
        flow.append(Paragraph(_safe_str(batch_title), BODY_SMALL))
    flow.append(Spacer(1, 6))

    meta_left = kv_block(
        [
            ("CheckPrint", batch_code or "-"),
            ("Project No.", project_code or "-"),
            ("Project Title", project_title or "-"),
        ],
        col_widths=(32 * mm, None),
    )
    meta_right = kv_block(
        [
            ("Generated By", _safe_str(generated_by) or "-"),
            ("Generated On", datetime.now().strftime("%Y-%m-%d")),
            ("Created On", _fmt_date(batch.get("created_on"))),
        ],
        col_widths=(32 * mm, None),
    )
    meta_gap = 8 * mm
    meta_w = (doc.width - meta_gap) / 2.0
    meta = Table([[meta_left, meta_right]], colWidths=[meta_w, meta_w])
    meta.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    flow.append(meta)
    flow.append(Spacer(1, 10))

    body_rows: List[List[Any]] = []
    for it in items:
        final_status = _safe_str(it.get("status")).lower()
        approval_date = _fmt_date(it.get("last_approved_on")) if final_status in {"approved", "accepted"} else "-"
        body_rows.append(
            [
                _safe_str(it.get("doc_id")) or "-",
                _safe_str(it.get("doc_type")) or "-",
                _safe_str(it.get("file_type")) or "-",
                _safe_str(it.get("description")) or "-",
                _initials(it.get("submitter")),
                _initials(it.get("reviewer")),
                _initials(it.get("approver")),
                str(int(it.get("cp_version") or 1)),
                approval_date,
            ]
        )

    headers = [
        "Document No.",
        "Type",
        "File Type",
        "Description",
        "Sub.",
        "Rev.",
        "App.",
        "CP Iter.",
        "Approval Date",
    ]

    fixed_mm = 55 + 34 + 24 + 14 + 14 + 14 + 18 + 28
    col_w = [
        55 * mm,
        34 * mm,
        24 * mm,
        doc.width - fixed_mm * mm,
        14 * mm,
        14 * mm,
        14 * mm,
        18 * mm,
        28 * mm,
    ]

    flow.append(
        table_with_headers_and_widths(
            headers=headers,
            rows=body_rows or [["-", "-", "-", "No CheckPrint items found.", "-", "-", "-", "-", "-"]],
            col_widths=col_w,
        )
    )

    doc.build(flow)
    return out_pdf
