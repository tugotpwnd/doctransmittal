# receipt_pdf.py
# ------------------------------------------------------------------
# Refactored to enforce Simple Report styling via shared pdf_layout.py
#
# Major changes:
#   - Remove bespoke header/footer, logos, and local brand tokens
#   - Use pdf_layout primitives for typography + colours + structure
#   - Keep export_* public API signatures stable
# ------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .db import get_project

# --- Shared layout system (Simple Report design tokens + helpers) ----------
try:
    # If receipt_pdf.py sits alongside other services
    from .pdf_layout import (
        BODY,
        BODY_SMALL,
        FONT,
        FONT_B,
        LABEL,
        TITLE,
        COL_LINE,
        COL_MUTED,
        COL_PRIMARY,
        draw_footer,
        draw_header,
        kv_block,
        _ensure_fonts, _resource_path,
        table_with_headers_and_widths,
)
except Exception:
    # Fallback if file moved under ui/ or different package wiring
    from ..services.pdf_layout import (  # type: ignore
        BODY,
        BODY_SMALL,
        FONT,
        FONT_B,
        LABEL,
        TITLE,
        COL_LINE,
        COL_MUTED,
        COL_PRIMARY,
        draw_footer,
        draw_header,
        kv_block,
    )


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def _safe_str(x: Any) -> str:
    return ("" if x is None else str(x)).strip()


def _to_dt(s: str) -> datetime:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return datetime.min


def _footer_left_text(header: Dict[str, Any]) -> str:
    # If you want this configurable later, put it in settings and pass in header.
    return _safe_str(header.get("footer_left")) or "Maxwell Industries Pty Ltd • ABN 95 654 787 210"


def _build_doc(
    out_pdf: Path,
    *,
    pagesize=A4,
    left_margin: float = 12 * mm,
    right_margin: float = 12 * mm,
    top_margin: float = 28 * mm,
    bottom_margin: float = 18 * mm,
    on_page=None,
) -> Tuple[BaseDocTemplate, Frame]:
    doc = BaseDocTemplate(
        str(out_pdf),
        pagesize=pagesize,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])
    return doc, frame


def _two_col_block(left: Table, right: Table, *, gap: float = 8 * mm) -> Table:
    """
    Place two blocks side-by-side, consistent padding-free layout.
    """
    side_w = (A4[0] - 24 * mm - gap) / 2.0  # page width - margins - gap
    t = Table([[left, right]], colWidths=[side_w, side_w])
    t.setStyle(
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
    return t

# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def export_transmittal_pdf(out_pdf: Path, header: Dict[str, Any], items: List[Dict[str, Any]]) -> Path:
    """
    Export a Document Transmittal PDF (A4 portrait) using shared pdf_layout styling.
    """
    _ensure_fonts()  # ✅ THIS IS THE CORRECT PLACE

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    db_path = Path(header["db_path"])
    proj = get_project(db_path)
    if not proj:
        raise RuntimeError("Project metadata missing")

    project_title = proj["project_name"]
    project_number = proj["project_code"]
    client_company = proj["client_company"]
    client_reference = proj["client_reference"]
    end_user = proj["end_user"]
    doc_id = header["number"]
    transmittal_title = header["title"]

    def _on_page(canvas, doc):
        draw_header(canvas, project=project_title, doc_id=doc_id)
        draw_footer(
            canvas,
            left_text="Maxwell Industries Pty Ltd • ABN 95 654 787 210",
            right_text=f"Page {doc.page}",
            mini_logo=Path(_resource_path("resources/_MI_Logo_Small_SVG.svg")),
        )

    from reportlab.lib.units import mm
    doc, _ = _build_doc(out_pdf, pagesize=A4, top_margin=28 * mm, bottom_margin=18 * mm, on_page=_on_page)


    from_name = _safe_str(header.get("from") or header.get("created_by") or header.get("user"))
    date_str = _safe_str(header.get("created_on") or header.get("date"))

    from reportlab.platypus import Table, TableStyle, Image as RLImage
    from reportlab.lib.units import mm

    # --- Title + logo row (tight, baseline-stable) ---
    title_para = Paragraph("Document Transmittal", TITLE)

    logo_elem = None
    logo_max_h = 18 * mm
    logo_col_w = 60 * mm  # tweak if you want more/less room

    try:
        logo_path = _resource_path("resources/_MI_Logo_SVG.svg")  # or .png fallback

        if logo_path.lower().endswith(".svg"):
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(logo_path)
            if drawing and drawing.width and drawing.height:
                scale = min((logo_col_w / drawing.width), (logo_max_h / drawing.height))
                drawing.scale(scale, scale)

                # CRITICAL: update the reported size so Table doesn't allocate giant height
                drawing.width = drawing.width * scale
                drawing.height = drawing.height * scale

                logo_elem = drawing

        else:
            # Prefer Platypus Image flowable (not ImageReader) for correct sizing
            img = RLImage(logo_path)
            scale = min((logo_col_w / img.imageWidth), (logo_max_h / img.imageHeight))
            img.drawWidth = img.imageWidth * scale
            img.drawHeight = img.imageHeight * scale
            logo_elem = img

    except Exception:
        logo_elem = None

    # Build a 2-col row: title left, logo right
    # If logo missing, second cell is blank but row height remains stable
    row = [title_para, (logo_elem or "")]
    title_row = Table(
        [row],
        colWidths=[doc.width - logo_col_w, logo_col_w],
        rowHeights=[max(logo_max_h, 18 * mm)],  # keeps the row from ballooning
        hAlign="LEFT",
    )

    title_row.setStyle(
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

    flow: List[Any] = []
    flow.append(title_row)

    if transmittal_title:
        flow.append(Spacer(1, 2))
        flow.append(
            Paragraph(
                _safe_str(transmittal_title),
                BODY,  # reuse BODY, keep hierarchy subtle
            )
        )

    flow.append(Spacer(1, 8))

    # --- Transmittal Information block (two columns) ---
    left_block = kv_block(
        [
            ("To", client_reference or "—"),
            ("Project No.", project_number or "—"),
            ("Project Title", project_title or "—"),
            ("Client", client_company or "—"),
        ],
        col_widths=(40 * mm, None),
    )
    right_block = kv_block(
        [
            ("From", from_name or "—"),
            ("Date", date_str or "—"),
            ("End User", end_user or "—"),
        ],
        col_widths=(40 * mm, None),
    )

    flow.append(Paragraph("<b>Transmittal Information</b>", BODY))
    flow.append(Spacer(1, 2))
    flow.append(_two_col_block(left_block, right_block, gap=8 * mm))
    flow.append(Spacer(1, 10))

    # --- Attachments table (use same visual language as Simple Report) ---
    flow.append(Paragraph("<b>Transmittal Attachments</b>", BODY))
    flow.append(Spacer(1, 2))

    # Table data
    headers = ["Rev", "Document No.", "File Type", "Description"]
    rows: List[List[Any]] = []
    for it in items or []:
        rev = _safe_str(it.get("revision"))
        docno = _safe_str(it.get("doc_id"))
        ftyp = _safe_str(it.get("file_type"))
        desc = _safe_str(it.get("description"))
        rows.append([rev or "—", docno or "—", ftyp or "—", desc or "—"])

    # Widths tuned to A4 with 12mm margins: content width ~186mm
    col_widths = [12 * mm, 43 * mm, 25 * mm, (doc.width - (10 + 45 + 25) * mm)]

    flow.append(
        table_with_headers_and_widths(
            headers,
            rows,
            col_widths=col_widths,
        )
    )

    doc.build(flow)
    return out_pdf


def export_progress_report_pdf(
    out_pdf: Path,
    header: Dict[str, Any],
    docs: List[Dict[str, Any]] | None = None,
    *,
    db_path: Path | None = None,
    project_id: int | None = None,
) -> Path:
    """
    Progress Tracker (A4 portrait).
    Enforces pdf_layout styling and DB-backed project metadata.
    """
    _ensure_fonts()

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # ---- Project metadata (single source of truth) ----
    if not db_path:
        raise RuntimeError("db_path is required for progress report")

    proj = get_project(Path(db_path))
    if not proj:
        raise RuntimeError("Project metadata missing")

    project_title = proj["project_name"]
    doc_id = _safe_str(header.get("created_on")) or "Progress"

    # ---- Load documents if not supplied ----
    if docs is None and project_id is not None:
        try:
            from ..services.db import list_documents_with_latest  # type: ignore
            docs = list_documents_with_latest(Path(db_path), int(project_id), state="active")
        except Exception:
            docs = []
    docs = docs or []

    def _on_page(canvas, doc):
        draw_header(canvas, project=project_title, doc_id=doc_id)
        draw_footer(
            canvas,
            left_text="Maxwell Industries Pty Ltd • ABN 95 654 787 210",
            right_text=f"Page {doc.page}",
            mini_logo=Path(_resource_path("resources/_MI_Logo_Small_SVG.svg")),
        )

    doc, _ = _build_doc(
        out_pdf,
        pagesize=A4,
        top_margin=28 * mm,
        bottom_margin=18 * mm,
        on_page=_on_page,
    )

    flow: List[Any] = []
    # --- Title + logo row (match Transmittal styling) ---
    title_para = Paragraph("Progress Tracker", TITLE)

    logo_elem = None
    logo_max_h = 18 * mm
    logo_col_w = 60 * mm

    try:
        logo_path = _resource_path("resources/_MI_Logo_SVG.svg")

        if logo_path.lower().endswith(".svg"):
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(logo_path)
            if drawing and drawing.width and drawing.height:
                scale = min(
                    logo_col_w / drawing.width,
                    logo_max_h / drawing.height,
                )
                drawing.scale(scale, scale)
                drawing.width *= scale
                drawing.height *= scale
                logo_elem = drawing
        else:
            from reportlab.platypus import Image as RLImage

            img = RLImage(logo_path)
            scale = min(
                logo_col_w / img.imageWidth,
                logo_max_h / img.imageHeight,
            )
            img.drawWidth = img.imageWidth * scale
            img.drawHeight = img.imageHeight * scale
            logo_elem = img

    except Exception:
        logo_elem = None

    title_row = Table(
        [[title_para, logo_elem or ""]],
        colWidths=[doc.width - logo_col_w, logo_col_w],
        rowHeights=[logo_max_h],
        hAlign="LEFT",
    )

    title_row.setStyle(
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

    flow.append(title_row)
    flow.append(Spacer(1, 8))

    # ---- Summary table ----
    from collections import Counter
    counts = Counter((_safe_str(r.get("status")) or "—").upper() for r in docs)

    flow.append(Paragraph("<b>Overall Progress</b>", BODY))
    flow.append(Spacer(1, 4))

    summary_rows = [[k, str(v)] for k, v in counts.items()] or [["—", "0"]]

    flow.append(
        table_with_headers_and_widths(
            headers=["Status", "Count"],
            rows=summary_rows,
            col_widths=[120 * mm, doc.width - 120 * mm],
        )
    )

    flow.append(Spacer(1, 10))

    # ---- Full document table ----
    flow.append(Paragraph("<b>All Documents</b>", BODY))
    flow.append(Spacer(1, 2))
    rows: List[List[Any]] = []
    for r in docs:
        rows.append(
            [
                _safe_str(r.get("doc_id")) or "—",
                _safe_str(r.get("description")) or "—",
                _safe_str(r.get("status")) or "—",
                _safe_str(r.get("latest_rev")) or "—",
            ]
        )

    col_w = [
        46 * mm,  # Document No.
        105 * mm,  # Description
        30 * mm,  # Status
        15 * mm,  # Rev (explicit, visible)
    ]

    flow.append(
        table_with_headers_and_widths(
            headers=["Document No.", "Description", "Status", "Rev"],
            rows=rows,
            col_widths=col_w,
        )
    )

    doc.build(flow)
    return out_pdf

def export_register_report_pdf(
    out_pdf: Path,
    header: Dict[str, Any],
    *,
    db_path: Path,
    project_id: int,
) -> Path:
    """
    Document Register (A4 landscape).
    Enforces pdf_layout styling and DB-backed project metadata.
    """
    _ensure_fonts()

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # ---- Project metadata (single source of truth) ----
    proj = get_project(Path(db_path))
    if not proj:
        raise RuntimeError("Project metadata missing")

    project_title = proj["project_name"]
    doc_id = _safe_str(header.get("report_code")) or "Register"

    # ---- Load register rows ----
    try:
        from ..services.db import (
            list_documents_with_latest,
            get_doc_submission_history,
        )  # type: ignore
        rows = list_documents_with_latest(
            Path(db_path),
            int(project_id),
            state="active",
        ) or []
    except Exception:
        rows = []
        get_doc_submission_history = None  # type: ignore

    def _last_two_submissions(doc_id_in: str) -> list[dict[str, Any]]:
        if not get_doc_submission_history:
            return []
        try:
            hist = get_doc_submission_history(
                Path(db_path),
                int(project_id),
                doc_id_in,
            ) or []
        except Exception:
            hist = []
        return sorted(
            hist,
            key=lambda r: _to_dt(_safe_str(r.get("created_on"))),
            reverse=True,
        )[:2]

    # ---- Header / footer ----
    def _on_page(canvas, doc):
        draw_header(canvas, project=project_title, doc_id=doc_id)
        draw_footer(
            canvas,
            left_text="Maxwell Industries Pty Ltd • ABN 95 654 787 210",
            right_text=f"Page {doc.page}",
            mini_logo=Path(
                _resource_path("resources/_MI_Logo_Small_SVG.svg")
            ),
        )

    doc, _ = _build_doc(
        out_pdf,
        pagesize=landscape(A4),
        top_margin=28 * mm,
        bottom_margin=18 * mm,
        on_page=_on_page,
    )

    # ---- Flow ----
    flow: List[Any] = []
    flow.append(Paragraph("Document Register", TITLE))
    flow.append(Spacer(1, 8))

    # ---- Table rows ----
    body_rows: List[List[Any]] = []
    for r in rows:
        did = _safe_str(r.get("doc_id")) or "—"
        dtype = _safe_str(r.get("doc_type")) or "—"
        ftyp = _safe_str(r.get("file_type")) or "—"
        desc = _safe_str(r.get("description")) or "—"

        last_two = _last_two_submissions(did)
        latest = last_two[0] if len(last_two) > 0 else None
        prev = last_two[1] if len(last_two) > 1 else None

        body_rows.append(
            [
                did,
                dtype,
                ftyp,
                desc,
                _safe_str(latest.get("revision")) if latest else "—",
                _safe_str(latest.get("created_on")) if latest else "—",
                _safe_str(prev.get("revision")) if prev else "—",
                _safe_str(prev.get("created_on")) if prev else "—",
            ]
        )

    headers = [
        "Document No.",
        "Type",
        "File Type",
        "Description",
        "Latest Sub.",
        "Date",
        "Prev Sub.",
        "Date",
    ]

    # ---- Column widths (ALL IN POINTS – no unit mixups) ----
    col_w = [
        55 * mm,
        22 * mm,
        22 * mm,
        125 * mm,
        18 * mm,
        25 * mm,
        18 * mm,
        doc.width - (55 + 22 + 22 + 125 + 18 + 25 + 18) * mm,
    ]

    flow.append(
        table_with_headers_and_widths(
            headers=headers,
            rows=body_rows,
            col_widths=col_w,
        )
    )

    doc.build(flow)
    return out_pdf
