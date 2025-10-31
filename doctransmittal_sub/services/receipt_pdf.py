# receipt_pdf.py — branded transmittal receipt
# Clean header: subtext under Maxwell logo, optional client logos top-right,
# centered title and TRN number.
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Iterable
import sys

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

# ---- Optional SVG support (crisper logos if you have SVG assets) ----------
try:
    from svglib.svglib import svg2rlg  # type: ignore
    from reportlab.graphics import renderPDF  # type: ignore
    HAS_SVGLIB = True
except Exception:
    HAS_SVGLIB = False

# ---- Try to use the same logo store as Project Settings -------------------
# Use the same logo access as template_apply
def _dbg(msg: str) -> None:
    try:
        print(f"[receipt_pdf] {msg}", flush=True)
    except Exception:
        pass

# Prefer same import style as template_apply.py
try:
    from .logo_store import list_logos  # <-- services sibling import
    _dbg("logo_store import ok (.logo_store)")
except Exception:
    try:
        from ..services.logo_store import list_logos  # fallback
        _dbg("logo_store import ok (..services.logo_store)")
    except Exception as e:
        _dbg(f"logo_store import FAILED: {e}")
        list_logos = lambda *_: []


# ---- Brand tokens ----------------------------------------------------------
BRAND_BLUE  = colors.HexColor("#0D4FA2")
BRAND_GREEN = colors.HexColor("#009640")
GREY_TEXT   = colors.HexColor("#6B7C93")

FONT    = "Helvetica"
FONT_B  = "Helvetica-Bold"
FONT_I  = "Helvetica-Oblique"


# Turn off diagonal slices; keep simple header band/card
USE_TRI_SLICES = False
SHOW_TOP_RIGHT_TITLE = False  # keep removed

# Default subtext under the Maxwell logo
DEFAULT_BRAND_SUBTEXT = [
    "Maxwell Industries Pty Ltd ABN 95 654 787 210",
    "A subsidiary of Maxwell Corporation Pty Ltd",
]

# ---- Helpers ---------------------------------------------------------------

def _resolve_asset(rel_or_abs: str | None) -> Path | None:
    if not rel_or_abs:
        return None
    p = Path(rel_or_abs)
    if p.exists():
        return p
    here = Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents)[:5]:
        cand = parent / rel_or_abs
        if cand.exists():
            return cand
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cand = Path(base) / rel_or_abs
        if cand.exists():
            return cand
    return None


def _compute_raster_fit(img_path: Path, max_w_pt: float, max_h_pt: float) -> tuple[float, float, ImageReader]:
    reader = ImageReader(str(img_path))
    iw, ih = reader.getSize()
    sx = max_w_pt / float(iw)
    sy = max_h_pt / float(ih)
    s = min(sx, sy)
    return iw * s, ih * s, reader


def _draw_logo(canvas, img_path: Path, x_pt: float, y_pt: float, max_w_pt: float, max_h_pt: float) -> tuple[float, float]:
    try:
        w_used, h_used, reader = _compute_raster_fit(img_path, max_w_pt, max_h_pt)
        if w_used <= 0 or h_used <= 0:
            return (0.0, 0.0)
        canvas.drawImage(reader, x_pt, y_pt, width=w_used, height=h_used, preserveAspectRatio=True, mask="auto")
        return (w_used, h_used)
    except Exception:
        return (0.0, 0.0)


def _draw_svg(canvas, svg_path: Path, x_pt: float, y_pt: float, max_w_pt: float, max_h_pt: float) -> tuple[float, float]:
    if not HAS_SVGLIB:
        return (0.0, 0.0)
    try:
        drawing = svg2rlg(str(svg_path))
        if not drawing or drawing.width == 0 or drawing.height == 0:
            return (0.0, 0.0)
        s = min(max_w_pt / drawing.width, max_h_pt / drawing.height)
        drawing.scale(s, s)
        renderPDF.draw(drawing, canvas, x_pt, y_pt)
        return (drawing.width * s, drawing.height * s)
    except Exception:
        return (0.0, 0.0)


def _draw_content_card(c, doc, radius=4*mm):
    x = doc.leftMargin - 6*mm
    y = doc.bottomMargin - 4*mm
    w = doc.width + 12*mm
    h = doc.height + 6*mm
    c.saveState()
    c.setFillColor(colors.HexColor("#EEF2F7"))
    c.roundRect(x+1.4*mm, y-1.4*mm, w, h, radius, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.roundRect(x, y, w, h, radius, stroke=0, fill=1)
    c.setStrokeColor(colors.HexColor("#D8E0EA"))
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, radius, stroke=1, fill=0)
    c.restoreState()


def _draw_header_backdrop(c, doc):
    w, h = doc.pagesize
    c.saveState()
    # top blue band
    blue_h = 8*mm
    c.setFillColor(BRAND_BLUE)
    c.rect(0, h - blue_h, w, blue_h, stroke=0, fill=1)
    c.restoreState()
    _draw_content_card(c, doc)


# ---- Client logos ----------------------------------------------------------

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".bmp", ".tif", ".tiff", ".gif"}

def _find_dm_logos_near(path: Path) -> List[Path]:
    """Fallback: walk up a few levels looking for a 'DM-Logos' folder."""
    for cur in [path] + list(path.parents)[:5]:
        dm = cur / "DM-Logos"
        if dm.exists() and dm.is_dir():
            files = sorted([p for p in dm.iterdir() if p.suffix.lower() in ALLOWED_EXTS], key=lambda p: p.name.lower())
            if files:
                _dbg(f"found DM-Logos at: {dm} (n={len(files)})")
                return files
    return []

def _gather_client_logo_paths(header: Dict[str, Any]) -> List[Path]:
    """
    1) header['client_logo_paths'] (explicit)
    2) list_logos(header['register_path'] or header['db_path'])
    3) Fallback: search near out_pdf for 'DM-Logos'
    """
    # 1) explicit list
    raw = header.get("client_logo_paths") or []
    out: List[Path] = []
    if isinstance(raw, (list, tuple)):
        for p in raw:
            p = Path(str(p))
            if p.exists():
                out.append(p)
    if out:
        _dbg(f"using explicit client_logo_paths (n={len(out)})")
        return out

    # 2) from DB path
    reg_path = header.get("register_path") or header.get("db_path")
    if reg_path:
        try:
            out = list_logos(Path(reg_path))
            _dbg(f"list_logos('{reg_path}') -> {len(out)} files")
            if out:
                return out
        except Exception as e:
            _dbg(f"list_logos error: {e}")

    # 3) fallback: try close to the PDF output location
    out_pdf_hint = header.get("_pdf_out_path")
    if out_pdf_hint:
        guessed = _find_dm_logos_near(Path(out_pdf_hint).parent)
        if guessed:
            return guessed

    _dbg("no client logos found.")
    return []




def _draw_logo_row_right(canvas, files: Iterable[Path], *,
                         y_bottom: float,
                         right_margin_pt: float,
                         gap_pt: float = 1*mm,       # tighter gap
                         max_h_pt: float = 18*mm,
                         max_w_pt: float = 34*mm):
    """
    Draws a horizontal row of logos, right-aligned.
    Shifted slightly down and right for better alignment with the header.
    """
    y_bottom -= 10*mm   # move down from blue header
    right_margin_pt -= -10*mm  # move a bit further right
    x = right_margin_pt

    for p in list(files)[:4][::-1]:
        try:
            if p.suffix.lower() == ".svg":
                w_used, h_used = _draw_svg(canvas, p, x - max_w_pt, y_bottom, max_w_pt, max_h_pt)
            else:
                w_used, h_used = _draw_logo(canvas, p, x - max_w_pt, y_bottom, max_w_pt, max_h_pt)
            used = min(max_w_pt, w_used) if w_used else 0.0
            if used > 0:
                x -= (used + gap_pt)
        except Exception:
            continue



# ---- Header / Footer -------------------------------------------------------

def _draw_header_footer(canvas, doc, header: Dict[str, Any]):
    w, h = doc.pagesize
    canvas.saveState()

    # Simple header (no triangles)
    _draw_header_backdrop(canvas, doc)
    header_tagline_y = h - 18*mm

    # --- Header logo (top-left) ---
    header_logo_path = header.get("header_logo_path") or "doctransmittal_sub/resources/logo.png"
    hdr_logo = _resolve_asset(header_logo_path)
    header_logo_max_w = 45.5*mm
    header_logo_max_h = 21*mm

    x_left = 12*mm
    logo_bottom = header_tagline_y - 4*mm
    logo_used_w = 0.0
    logo_used_h = 0.0
    if hdr_logo:
        if hdr_logo.suffix.lower() == ".svg":
            logo_used_w, logo_used_h = _draw_svg(canvas, hdr_logo, x_left, logo_bottom,
                                                 header_logo_max_w, header_logo_max_h)
        else:
            logo_used_w, logo_used_h = _draw_logo(canvas, hdr_logo, x_left, logo_bottom,
                                                  header_logo_max_w, header_logo_max_h)

    # --- Subtext beneath the Maxwell logo
    sub_lines = header.get("brand_subtext_lines") or DEFAULT_BRAND_SUBTEXT
    if sub_lines:
        canvas.setFillColor(GREY_TEXT)
        canvas.setFont(FONT_I, 9)
        y_sub = logo_bottom - 3.2*mm
        for i, line in enumerate(sub_lines[:2]):
            canvas.drawString(x_left, y_sub - i*3.8*mm, str(line))

    # --- Centered header title and TRN number
    header_title = (header.get("header_title") or "DOCUMENT TRANSMITTAL").strip()
    canvas.setFillColor(GREY_TEXT)
    canvas.setFont(FONT, 12)
    canvas.drawCentredString(w/2, header_tagline_y, header_title)

    trn_no = (header.get("number") or "").strip()
    if trn_no:
        canvas.setFillColor(colors.black)
        canvas.setFont(FONT, 10)
        canvas.drawCentredString(w/2, header_tagline_y - 6*mm, f"Transmittal: {trn_no}")

    # --- Client logos (top-right)
    client_paths = _gather_client_logo_paths(header)
    if client_paths:
        right_safe_margin = 12*mm
        y_bottom = h - 18 * mm
        _draw_logo_row_right(canvas, client_paths,
                             y_bottom=y_bottom,
                             right_margin_pt=w - right_safe_margin,
                             max_h_pt=17 * mm,  # slightly taller scale
                             max_w_pt=33 * mm)
    # Footer rule
    canvas.setStrokeColor(colors.HexColor("#B5B5B5"))
    canvas.setLineWidth(0.5)
    canvas.line(12*mm, 20*mm, w - 12*mm, 20*mm)

    # --- Footer logo (RHS) ---
    footer_logo_path = header.get("footer_logo_path") or "doctransmittal_sub/resources/logo_small.png"
    ftr_logo = _resolve_asset(footer_logo_path)
    footer_logo_max_w = 27*mm
    footer_logo_max_h = 12*mm
    right_safe_margin = 24*mm
    logo_y = 2*mm

    if ftr_logo:
        try:
            used_w, used_h, reader = _compute_raster_fit(ftr_logo, footer_logo_max_w, footer_logo_max_h)
            x_r = (w - right_safe_margin) - used_w
            canvas.drawImage(reader, x_r, logo_y, width=used_w, height=used_h, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    # Page number
    canvas.setFillColor(colors.grey)
    canvas.setFont(FONT, 9)
    canvas.drawRightString(w - 12*mm, 10*mm, f"Page {doc.page}")
    canvas.restoreState()


# ---- Body widgets ----------------------------------------------------------

def _kv_table(rows: list[list[str]], col0_width_mm: float, col1_width_mm: float) -> Table:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]; body.fontName = FONT; body.fontSize = 10; body.leading = 12
    wrapped = [[k, Paragraph(v if v else "&nbsp;", body)] for k, v in rows]
    t = Table(wrapped, colWidths=[col0_width_mm*mm, col1_width_mm*mm])
    t.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 10),
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F7FAFD")),
        ("TEXTCOLOR", (0,0), (0,-1), GREY_TEXT),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, colors.whitesmoke),
    ]))
    return t


def _attachments_table(items: List[Dict[str, Any]]) -> Table:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.fontName = FONT
    body.fontSize = 9
    body.leading = 11

    rows = [["Rev", "Document No.", "File Type", "Description"]]
    for it in items or []:
        rev  = (it.get("revision") or "").strip()
        doc  = (it.get("doc_id") or "").strip()
        ftyp = (it.get("file_type") or "").strip()
        desc_text = (it.get("description") or "").strip()
        desc_para = Paragraph(desc_text if desc_text else "&nbsp;", body)
        rows.append([rev, doc, ftyp, desc_para])

    # Adjusted column widths
    rev_w     = 8
    doc_no_w  = 50
    file_w    = 30
    desc_w    = 92
    tbl = Table(rows, colWidths=[rev_w*mm, doc_no_w*mm, file_w*mm, desc_w*mm], repeatRows=1)

    tbl.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 9),
        ("FONT", (0,0), (-1,0), FONT_B, 9),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#008D3C")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#B7C3D0")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("ALIGN", (0,1), (0,-1), "CENTER"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#FAFCFF"), colors.white]),
    ]))
    return tbl


# ---- Public API ------------------------------------------------------------

def export_transmittal_pdf(out_pdf: Path, header: Dict[str, Any], items: List[Dict[str, Any]]) -> Path:
    out_pdf = Path(out_pdf)
    header.setdefault("_pdf_out_path", str(out_pdf))
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    H1 = styles["Title"]; H1.fontName = FONT_B; H1.fontSize = 18; H1.leading = 21
    H2 = styles["Heading2"]; H2.fontName = FONT_B; H2.fontSize = 12; H2.leading = 14

    doc = BaseDocTemplate(
        str(out_pdf), pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=40*mm, bottomMargin=20*mm
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([
        PageTemplate(id="with-hf", frames=[frame], onPage=lambda c, d: _draw_header_footer(c, d, header))
    ])

    flow = []
    flow.append(Paragraph("Document Transmittal", H1))
    flow.append(Spacer(1, 6))

    # Transmittal Info
    to_name = (header.get("to") or header.get("client_contact") or header.get("client") or "").strip()
    from_name = (header.get("from") or header.get("created_by") or header.get("user") or "").strip()
    proj_no = (header.get("project_code") or header.get("project_no") or "").strip()
    proj_title = (header.get("title") or header.get("project_title") or "").strip()
    client = (header.get("client") or "").strip()
    end_user = (header.get("end_user") or "").strip()
    date_str = (header.get("created_on") or header.get("date") or "").strip()
    purpose = (header.get("purpose") or header.get("status") or "").strip()

    side_w = (doc.width / 2) - 8*mm
    key_w = max(22*mm, side_w * 0.33)
    val_w = side_w - key_w

    left_tbl = _kv_table([
        ["To:", to_name],
        ["Project No.:", proj_no],
        ["Project Title:", proj_title],
        ["Client:", client],
    ], col0_width_mm=key_w/mm, col1_width_mm=val_w/mm)

    right_tbl = _kv_table([
        ["From:", from_name],
        ["Date:", date_str],
        ["End User:", end_user],
        ["Purpose:", purpose],
    ], col0_width_mm=key_w/mm, col1_width_mm=val_w/mm)

    info_tbl = Table([[left_tbl, right_tbl]], colWidths=[side_w, side_w])
    info_tbl.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
    ]))

    flow.append(KeepTogether([Paragraph("Transmittal Information", H2), Spacer(1, 2), info_tbl]))
    flow.append(Spacer(1, 10))

    # Attachments
    flow.append(Paragraph("Transmittal Attachments", H2))
    flow.append(Spacer(1, 2))
    flow.append(_attachments_table(items))

    doc.build(flow)
    return out_pdf

# --- add near bottom (below export_transmittal_pdf) -------------------------
def export_progress_report_pdf(
    out_pdf: Path,
    header: Dict[str, Any],
    docs: List[Dict[str, Any]] | None = None,
    *,
    db_path: Path | None = None,
    project_id: int | None = None
) -> Path:
    """
    Builds a 'Progress Tracker' PDF using same header/footer & client logos.
    Body = Big pie chart of Status distribution + table of all documents.
    If docs is None and db_path/project_id are provided, it will query the DB.
    """
    out_pdf = Path(out_pdf)
    header = dict(header or {})
    header["header_title"] = header.get("header_title") or "PROGRESS TRACKER"
    header.setdefault("_pdf_out_path", str(out_pdf))
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # Optional: fetch docs if not supplied
    if docs is None and db_path and project_id is not None:
        try:
            # Lazy import to avoid hard deps for callers that pass docs explicitly
            from ..services.db import list_documents_with_latest  # type: ignore
            docs = list_documents_with_latest(Path(db_path), int(project_id), state="active")
        except Exception:
            docs = []
    docs = docs or []

    # ----------------- Styles & doc shell -----------------
    styles = getSampleStyleSheet()
    H1 = styles["Title"];    H1.fontName = FONT_B; H1.fontSize = 18; H1.leading = 21
    H2 = styles["Heading2"]; H2.fontName = FONT_B; H2.fontSize = 12; H2.leading = 14
    P  = styles["BodyText"]; P.fontName  = FONT;   P.fontSize   = 10; P.leading   = 12

    doc = BaseDocTemplate(
        str(out_pdf), pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=40*mm, bottomMargin=20*mm
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([
        PageTemplate(id="with-hf", frames=[frame], onPage=lambda c, d: _draw_header_footer(c, d, header))
    ])

    flow: List = []

    # ----------------- Title -----------------
    flow.append(Paragraph("Progress Tracker", H1))
    flow.append(Spacer(1, 8))

    # ----------------- Pie chart -----------------
    # Build status counts
    from collections import Counter
    counts = Counter((str((r.get("status") or "")).strip() or "—").upper() for r in docs)
    labels = list(counts.keys())
    values = [counts[k] for k in labels]

    # Drawing with Pie + Legend
    try:
        # ----------------- Pie chart (with legend & percentages) -----------------
        from collections import Counter
        from reportlab.graphics.shapes import Drawing, Rect
        from reportlab.graphics.charts.piecharts import Pie

        counts = Counter((str((r.get("status") or "")).strip() or "—").upper() for r in docs)
        # Sort by size desc for a cleaner legend
        labels = [k for k, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]
        values = [counts[k] for k in labels]
        total = float(sum(values)) or 1.0
        pcts = [round(v * 100.0 / total) for v in values]  # whole-number % for readability

        flow.append(Paragraph("Overall Progress", H2))
        flow.append(Spacer(1, 4))

        # Pie only drawing
        pie_d = Drawing(110 * mm, 95 * mm)  # generous bbox for safety
        pie = Pie()
        pie.width = 90 * mm
        pie.height = 90 * mm
        pie.x = 0
        pie.y = 0
        pie.data = values or [1]
        pie.labels = []  # legend carries labels
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 0.6

        # Fixed palette for consistent look (cycles if > len)
        palette = [
            colors.HexColor("#0E7C86"),  # teal
            colors.HexColor("#00BCD4"),  # cyan
            colors.HexColor("#5B3CC4"),  # indigo
            colors.HexColor("#E05A87"),  # rose
            colors.HexColor("#F39C12"),  # orange
            colors.HexColor("#7F8C8D"),  # grey
            colors.HexColor("#1ABC9C"),  # green
            colors.HexColor("#3498DB"),  # blue
        ]
        for i in range(len(values or [1])):
            pie.slices[i].fillColor = palette[i % len(palette)]
        pie_d.add(pie)

        # Legend as a table (swatch + text) so it always renders where we place it
        legend_rows = []
        legend_text_style = getSampleStyleSheet()["BodyText"]
        legend_text_style.fontName = FONT
        legend_text_style.fontSize = 10
        legend_text_style.leading = 12

        for i, lbl in enumerate(labels or ["—"]):
            sw = Drawing(10, 10)
            sw.add(Rect(0, 0, 8, 8,
                        fillColor=pie.slices[i].fillColor,
                        strokeColor=pie.slices[i].fillColor))
            legend_rows.append([
                sw,
                Paragraph(f"{lbl} — {values[i]} ({pcts[i]}%)", legend_text_style)
            ])

        legend_tbl = Table(
            legend_rows or [["", Paragraph("No data", legend_text_style)]],
            colWidths=[8 * mm, 55 * mm]
        )
        legend_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))

        # Place pie (left) + legend (right) side-by-side
        side_by_side = Table(
            [[pie_d, legend_tbl]],
            colWidths=[110 * mm, 76 * mm]  # total ~186mm content width
        )
        side_by_side.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

        flow.append(side_by_side)
        flow.append(Spacer(1, 10))

    except Exception:
        # Fallback: simple counts list
        flow.append(Paragraph("Overall Progress", H2))
        flow.append(Spacer(1, 4))
        for k in labels:
            flow.append(Paragraph(f"{k}: {counts[k]}", P))
        flow.append(Spacer(1, 10))

    # ----------------- Full document table -----------------
    flow.append(Paragraph("All Documents", H2))
    flow.append(Spacer(1, 2))

    # Build rows: Doc No., Type, File, Description, Status, Rev
    body_style = getSampleStyleSheet()["BodyText"]
    body_style.fontName = FONT
    body_style.fontSize = 9
    body_style.leading  = 11

    rows = [["Document No.", "Type", "File", "Description", "Status", "Rev"]]
    for r in docs:
        doc_no = (r.get("doc_id") or "").strip()
        typ    = (r.get("doc_type") or "").strip()
        ftyp   = (r.get("file_type") or "").strip()
        desc   = Paragraph((r.get("description") or "&nbsp;").strip() or "&nbsp;", body_style)
        stat   = (r.get("status") or "").strip()
        rev    = (r.get("latest_rev") or "").strip()
        rows.append([doc_no, typ, ftyp, desc, stat, rev])

    # Keep widths within ~186mm content width (A4 – margins)
    col_w = [46, 18, 18, 72, 20, 12]  # mm => sums to 186mm
    tbl = Table(rows, colWidths=[w*mm for w in col_w], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 9),
        ("FONT", (0,0), (-1,0), FONT_B, 9),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#008D3C")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#B7C3D0")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("ALIGN", (0,1), (0,-1), "LEFT"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#FAFCFF"), colors.white]),
    ]))
    flow.append(tbl)

    doc.build(flow)
    return out_pdf

from datetime import datetime
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, BaseDocTemplate, PageTemplate, Frame
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import mm

def export_register_report_pdf(
    out_pdf: Path,
    header: Dict[str, Any],
    *,
    db_path: Path,
    project_id: int
) -> Path:
    """
    Document Register (A4 landscape) with ONLY submitted revisions:
      Doc ID | Type | File Type | Description | Latest Sub. | Date | Prev Sub. | Date
    - If no submissions exist: show "—" in both submission cols.
    """
    out_pdf = Path(out_pdf)
    header = dict(header or {})
    header.setdefault("header_title", "DOCUMENT REGISTER")
    header.setdefault("_pdf_out_path", str(out_pdf))
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # --- Load register rows (active only) ---
    try:
        try:
            from ..services.db import list_documents_with_latest, get_doc_submission_history
        except Exception:
            from ..services.db import list_documents_with_latest, get_doc_submission_history
        rows = list_documents_with_latest(Path(db_path), int(project_id), state="active") or []
    except Exception:
        rows = []

    # normalise date strings so we can sort desc
    def _to_dt(s: str):
        s = (s or "").strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
        return datetime.min

    # get last two ACTUAL submissions for a doc_id
    def _last_two_submissions(doc_id: str):
        try:
            hist = get_doc_submission_history(Path(db_path), int(project_id), doc_id) or []
        except Exception:
            hist = []
        hist_sorted = sorted(hist, key=lambda r: _to_dt(r.get("created_on") or ""), reverse=True)
        return hist_sorted[:2]

    # ---- doc shell (LANDSCAPE) ----
    styles = getSampleStyleSheet()
    H1 = styles["Title"]; H1.fontName = "Helvetica-Bold"; H1.fontSize = 18; H1.leading = 21

    doc = BaseDocTemplate(
        str(out_pdf), pagesize=landscape(A4),
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=40*mm, bottomMargin=20*mm
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([
        PageTemplate(id="with-hf", frames=[frame], onPage=lambda c, d: _draw_header_footer(c, d, header))
    ])

    flow: list = []
    flow.append(Paragraph("Document Register", H1))
    flow.append(Spacer(1, 8))

    # ---- header row (now 8 cols) ----
    HSTYLE = styles["BodyText"]
    HSTYLE.fontName = "Helvetica-Bold"
    HSTYLE.fontSize = 9
    HSTYLE.alignment = 1
    HSTYLE.leading = 10

    hdr_labels = [
        "Document<br/>No.",
        "Type",
        "File<br/>Type",
        "Description",
        "Latest<br/>Sub.",
        "Date",
        "Prev<br/>Sub.",
        "Date",
    ]
    hdr = [Paragraph(lbl, HSTYLE) for lbl in hdr_labels]

    # widen overall table and shift width to doc no + description
    col_w_mm = [50, 18, 18, 120, 16, 19, 16, 19]  # widened Doc No. & Description

    body_rows = []
    for r in rows:
        did   = (r.get("doc_id") or "").strip()
        dtype = (r.get("doc_type") or "").strip()
        ftyp  = (r.get("file_type") or "").strip()
        desc  = (r.get("description") or "").strip()

        last_two = _last_two_submissions(did)
        latest = last_two[0] if len(last_two) >= 1 else None
        prev   = last_two[1] if len(last_two) >= 2 else None

        latest_sub_rev  = (latest.get("revision") or "—") if latest else "—"
        latest_sub_date = (latest.get("created_on") or "—") if latest else "—"
        prev_sub_rev    = (prev.get("revision") or "—") if prev else "—"
        prev_sub_date   = (prev.get("created_on") or "—") if prev else "—"

        P = styles["BodyText"]
        P.fontName = "Helvetica"
        P.fontSize = 9
        P.leading = 11
        desc_para = Paragraph(desc if desc else "&nbsp;", P)

        body_rows.append([
            did,
            dtype,
            ftyp,
            desc_para,
            latest_sub_rev,
            latest_sub_date,
            prev_sub_rev,
            prev_sub_date,
        ])

    tbl = Table([hdr] + body_rows, colWidths=[w*mm for w in col_w_mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        # --- HEADER ---
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#009640")),  # green header
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,0), 10),
        ("ALIGN",      (0,0), (-1,0), "CENTER"),

        # --- BODY ---
        ("GRID",       (0,0), (-1,-1), 0.25, colors.HexColor("#B7C3D0")),
        ("VALIGN",     (0,1), (-1,-1), "TOP"),
        ("FONTSIZE",   (0,1), (-1,-1), 9),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#FAFCFF"), colors.white]),

        ("LEFTPADDING",  (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),

        # align description left, submissions centered
        ("ALIGN", (3,1), (3,-1), "LEFT"),
        ("ALIGN", (4,1), (4,-1), "CENTER"),
        ("ALIGN", (6,1), (6,-1), "CENTER"),
    ]))


    flow.append(tbl)
    doc.build(flow)
    return out_pdf

