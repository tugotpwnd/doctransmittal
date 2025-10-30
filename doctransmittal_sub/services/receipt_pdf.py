# receipt_pdf.py — branded transmittal receipt
# Modernized styling: diagonal brand slices, white content card, logos, refined tables
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any
import sys

from reportlab.lib.pagesizes import A4
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

# ---- Brand tokens ----------------------------------------------------------
BRAND_BLUE  = colors.HexColor("#0D4FA2")
BRAND_GREEN = colors.HexColor("#009640")
GREY_TEXT   = colors.HexColor("#6B7C93")

# Use built-ins, or register corporate fonts here if you have TTFs
FONT   = "Times-Roman"
FONT_B = "Times-Bold"

USE_TRI_SLICES = True
SHOW_TOP_RIGHT_TITLE = False  # keep removed

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


# ---- Background & Card -----------------------------------------------------

def _draw_brand_slices(c, doc):
    w, h = A4
    c.saveState()
    p = c.beginPath()
    p.moveTo(w*0.46, h); p.lineTo(w, h); p.lineTo(w, h*0.70); p.close()
    c.setFillColor(BRAND_BLUE); c.drawPath(p, fill=1, stroke=0)
    p = c.beginPath()
    p.moveTo(0, 0); p.lineTo(0, h*0.30); p.lineTo(w*0.36, 0); p.close()
    c.setFillColor(BRAND_GREEN); c.drawPath(p, fill=1, stroke=0)
    c.restoreState()


def _draw_content_card(c, doc, radius=4*mm):
    """White rounded rectangle; slightly shorter for tighter fit."""
    x = doc.leftMargin - 6*mm
    y = doc.bottomMargin - 4*mm  # raised bottom
    w = doc.width + 12*mm
    h = doc.height + 6*mm  # reduced from +12mm
    c.saveState()
    c.setFillColor(colors.HexColor("#EEF2F7"))
    c.roundRect(x+1.4*mm, y-1.4*mm, w, h, radius, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.roundRect(x, y, w, h, radius, stroke=0, fill=1)
    c.setStrokeColor(colors.HexColor("#D8E0EA"))
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, radius, stroke=1, fill=0)
    c.restoreState()


# ---- Header / Footer -------------------------------------------------------

def _draw_header_footer(canvas, doc, header: Dict[str, Any]):
    w, h = A4
    canvas.saveState()

    if USE_TRI_SLICES:
        _draw_brand_slices(canvas, doc)
        _draw_content_card(canvas, doc)
        header_tagline_y = h - 18*mm
    else:
        blue_y = h - 4*mm
        canvas.setFillColor(BRAND_BLUE); canvas.rect(0, blue_y, w, 8*mm, stroke=0, fill=1)
        white_h = 18*mm
        canvas.setFillColor(colors.white); canvas.rect(0, blue_y - white_h, w, white_h, stroke=0, fill=1)
        canvas.setFillColor(BRAND_GREEN); canvas.rect(0, blue_y - white_h - 6*mm, w, 3*mm, stroke=0, fill=1)
        header_tagline_y = blue_y - 12*mm

    # --- Header logo (top-left) ---
    header_logo_path = header.get("header_logo_path") or "doctransmittal_sub/resources/logo.png"
    hdr_logo = _resolve_asset(header_logo_path)
    header_logo_max_w = 45.5*mm  # 1.75x
    header_logo_max_h = 21*mm

    logo_used_w = 0.0
    x_left = 12*mm
    if hdr_logo:
        if hdr_logo.suffix.lower() == ".svg":
            logo_used_w, _ = _draw_svg(canvas, hdr_logo, x_left, header_tagline_y - 4*mm,
                                       header_logo_max_w, header_logo_max_h)
        else:
            logo_used_w, _ = _draw_logo(canvas, hdr_logo, x_left, header_tagline_y - 4*mm,
                                        header_logo_max_w, header_logo_max_h)

    # Left side tagline and TRN
    x_text = x_left + (logo_used_w + (4*mm if logo_used_w else 0))
    canvas.setFillColor(GREY_TEXT)
    canvas.setFont(FONT, 12)
    canvas.drawString(x_text, header_tagline_y, "DOCUMENT TRANSMITTAL")
    trn_no = (header.get("number") or "").strip()
    if trn_no:
        canvas.setFillColor(colors.black)
        canvas.setFont(FONT, 10)
        canvas.drawString(x_text, header_tagline_y - 6*mm, f"Transmittal: {trn_no}")

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
    logo_y = 2*mm  # moved further down

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
    """Rev | Document No. | File Type | Description
       Rev column reduced to 1/3 width, Description wraps to new lines and expands row height.
    """
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

    # Adjusted column widths: Rev narrower, Description wider
    rev_w     = 8     # mm
    doc_no_w  = 50    # mm
    file_w    = 30    # mm
    desc_w    = 92    # mm  (expanded to absorb space from Rev)
    tbl = Table(rows, colWidths=[rev_w*mm, doc_no_w*mm, file_w*mm, desc_w*mm], repeatRows=1)

    tbl.setStyle(TableStyle([
        ("FONT", (0,0), (-1,-1), FONT, 9),
        ("FONT", (0,0), (-1,0), FONT_B, 9),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#008D3C")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#B7C3D0")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),        # let cells grow vertically
        ("ALIGN", (0,1), (0,-1), "CENTER"),       # Rev column centered
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
