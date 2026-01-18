# pdf_layout.py
# ------------------------------------------------------------------
# Shared PDF layout primitives
# Extracted from simple_report.py
#
# PURPOSE:
#   - Single source of truth for PDF branding, layout, fonts, colours
#   - Used by simple_report.py, receipt_pdf.py, invoices, etc.
#
# DESIGN:
#   - Canvas-safe helpers (header/footer)
#   - Platypus-safe styles (Paragraph/Table)
#   - No document-specific logic
# ------------------------------------------------------------------

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, Table, TableStyle, Spacer
# pdf_layout.py
from pathlib import Path
import sys
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_FONTS_READY = False

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def _resource_path(rel: str) -> str:
    """
    Resolve resource paths for dev + PyInstaller.
    Local to pdf_layout to avoid entry-point coupling.
    """
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "doctransmittal_sub"
    else:
        base = Path(__file__).resolve().parents[1]  # doctransmittal_sub/

    return str((base / rel).resolve())


# ------------------------------------------------------------------
# Font registration (same as simple_report)
# ------------------------------------------------------------------

def _ensure_fonts():
    """
    Idempotent font registration.
    Safe to call multiple times.
    """
    global _FONTS_READY
    if _FONTS_READY:
        return

    # Register individual fonts
    pdfmetrics.registerFont(
        TTFont("Arial", _resource_path("resources/fonts/arial.ttf"))
    )
    pdfmetrics.registerFont(
        TTFont("Arial-Bold", _resource_path("resources/fonts/arialbd.ttf"))
    )
    pdfmetrics.registerFont(
        TTFont("Arial-Italic", _resource_path("resources/fonts/ariali.ttf"))
    )
    pdfmetrics.registerFont(
        TTFont("Arial-BoldItalic", _resource_path("resources/fonts/arialbi.ttf"))
    )

    # Register family (THIS fixes your error)
    pdfmetrics.registerFontFamily(
        "Arial",
        normal="Arial",
        bold="Arial-Bold",
        italic="Arial-Italic",
        boldItalic="Arial-BoldItalic",
    )

    _FONTS_READY = True

FONT     = "Arial"
FONT_B   = "Arial-Bold"
FONT_I   = "Arial-Italic"
FONT_BI  = "Arial-BoldItalic"

# ------------------------------------------------------------------
# Brand colours (locked to Simple Report)
# ------------------------------------------------------------------

COL_PRIMARY = colors.HexColor("#215096")   # blue
COL_ACCENT  = colors.HexColor("#007F4D")   # green
COL_TEXT    = colors.black
COL_MUTED   = colors.HexColor("#8A8A8A")
COL_LINE    = colors.HexColor("#D0D6DF")

# ------------------------------------------------------------------
# Paragraph styles
# ------------------------------------------------------------------

_styles = getSampleStyleSheet()

TITLE = ParagraphStyle(
    name="Title",
    fontName=FONT_B,
    fontSize=18,
    leading=22,
    spaceAfter=8,
    textColor=COL_PRIMARY,
    alignment=TA_LEFT,
)

H2 = ParagraphStyle(
    name="H2",
    parent=_styles["Heading2"],
    fontName=FONT_B,
    fontSize=14,
    leading=18,
    spaceBefore=6,
    spaceAfter=6,
    textColor=COL_PRIMARY,
    alignment=TA_LEFT,
)

BODY = ParagraphStyle(
    name="Body",
    parent=_styles["BodyText"],
    fontName=FONT,
    fontSize=10,
    leading=14,
    spaceBefore=4,
    spaceAfter=4,
    textColor=COL_TEXT,
)

BODY_SMALL = ParagraphStyle(
    name="BodySmall",
    parent=_styles["BodyText"],
    fontName=FONT,
    fontSize=8.5,
    leading=11,
    spaceBefore=2,
    spaceAfter=2,
    textColor=COL_MUTED,
)

LABEL = ParagraphStyle(
    name="Label",
    fontName=FONT_B,
    fontSize=9.5,
    leading=12,
    textColor=COL_PRIMARY,
)

VALUE = ParagraphStyle(
    name="Value",
    fontName=FONT,
    fontSize=9.5,
    leading=12,
    textColor=COL_TEXT,
)

VALUE_RIGHT = ParagraphStyle(
    name="ValueRight",
    parent=VALUE,
    alignment=TA_RIGHT,
)

TABLE_HEADER = ParagraphStyle(
    "TABLE_HEADER",
    parent=LABEL,
    fontName=FONT_B,          # bold
    fontSize=9,
    textColor=colors.white,   # force white
    spaceBefore=0,
    spaceAfter=0,
)
# ------------------------------------------------------------------
# Header / footer (canvas-level)
# ------------------------------------------------------------------

def draw_header(
    canvas,
    *,
    project: str,
    doc_id: Optional[str] = None,
):
    """
    Matches Simple Report header.
    """
    w, h = A4
    y = h - 18 * mm

    canvas.saveState()

    # Left: Project
    canvas.setFont(FONT_B, 10)
    canvas.setFillColor(COL_PRIMARY)
    canvas.drawString(12 * mm, y, "Project:")

    lw = canvas.stringWidth("Project:", FONT_B, 10)

    canvas.setFont(FONT, 10)
    canvas.setFillColor(COL_ACCENT)
    canvas.drawString(12 * mm + lw + 2, y, project or "")

    # Right: Doc ID (optional)
    if doc_id:
        label = "Doc ID:"
        canvas.setFont(FONT_B, 10)
        lw = canvas.stringWidth(label, FONT_B, 10)
        vw = canvas.stringWidth(doc_id, FONT, 10)
        x = w - 12 * mm - (lw + vw + 2)

        canvas.setFillColor(COL_PRIMARY)
        canvas.drawString(x, y, label)

        canvas.setFont(FONT, 10)
        canvas.setFillColor(COL_ACCENT)
        canvas.drawString(x + lw + 2, y, doc_id)

    canvas.restoreState()


def draw_footer(
    canvas,
    *,
    left_text: str,
    right_text: Optional[str] = None,
    mini_logo: Optional[Path] = None,
):
    """
    Subtle footer consistent with Simple Report, with optional centred mini logo.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    w, _ = A4
    y = 12 * mm

    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(COL_MUTED)

    # ---- Left text ----
    canvas.drawString(12 * mm, y, left_text)

    # ---- Right text ----
    if right_text:
        rw = canvas.stringWidth(right_text, FONT, 8)
        canvas.drawString(w - 12 * mm - rw, y, right_text)

    # ---- Centre mini logo (optional) ----
    if mini_logo and Path(mini_logo).exists():
        try:
            max_h = 7 * mm   # intentionally small
            max_w = 30 * mm

            if mini_logo.suffix.lower() == ".svg":
                from svglib.svglib import svg2rlg
                from reportlab.graphics import renderPDF

                drawing = svg2rlg(str(mini_logo))
                if drawing and drawing.width and drawing.height:
                    scale = min(max_w / drawing.width, max_h / drawing.height)
                    drawing.scale(scale, scale)

                    # Critical: update reported size
                    drawing.width *= scale
                    drawing.height *= scale

                    x = (w - drawing.width) / 2
                    renderPDF.draw(drawing, canvas, x, y - 2)

            else:
                from reportlab.platypus import Image as RLImage

                img = RLImage(str(mini_logo))
                scale = min(max_w / img.imageWidth, max_h / img.imageHeight)
                img.drawWidth = img.imageWidth * scale
                img.drawHeight = img.imageHeight * scale

                x = (w - img.drawWidth) / 2
                canvas.drawImage(
                    str(mini_logo),
                    x,
                    y - 2,
                    width=img.drawWidth,
                    height=img.drawHeight,
                    preserveAspectRatio=True,
                    mask="auto",
                )

        except Exception:
            pass  # footer branding is optional by design

    canvas.restoreState()

# ------------------------------------------------------------------
# Key / Value block (used heavily by receipts)
# ------------------------------------------------------------------

def kv_block(rows: Iterable[Tuple[str, str]], col_widths=(50 * mm, None)):
    """
    Label/value grid with strict alignment.
    """
    data = [
        [
            Paragraph(label, LABEL),
            Paragraph(value, VALUE),
        ]
        for label, value in rows
    ]

    t = Table(
        data,
        colWidths=list(col_widths),
        hAlign="LEFT",
    )

    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return t

# ------------------------------------------------------------------
# Standard table (used by reports + receipts)
# ------------------------------------------------------------------

def standard_table(
    headers: List[str],
    rows: List[List[str]],
    *,
    header_fill=COL_PRIMARY,
    grid_color=COL_LINE,
):
    data = [[Paragraph(h, LABEL) for h in headers]]
    data += [[Paragraph(str(c), BODY) for c in r] for r in rows]

    t = Table(data, repeatRows=1)

    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_fill),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), FONT_B),
                ("GRID", (0, 0), (-1, -1), 0.5, grid_color),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t

from typing import Sequence, Any, List, Optional
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib import colors

# assumes these already exist in pdf_layout
# FONT, FONT_B, BODY, LABEL, COL_PRIMARY, COL_LINE, _safe_str

def table_with_headers_and_widths(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    col_widths: Sequence[float],
    header_fill=COL_PRIMARY,
    grid_color=COL_LINE,
) -> Table:
    """
    Standard table with a styled header row and explicit column widths.

    - Header row is always rendered
    - Header repeats on page breaks
    - Styling is consistent across all PDFs
    """

    data: List[List[Any]] = []

    # Header row
    data.append(
        [Paragraph(_safe_str(h), TABLE_HEADER) for h in headers]
    )

    # Body rows
    for r in rows:
        row_cells: List[Any] = []
        for c in r:
            val = _safe_str(c)
            row_cells.append(
                Paragraph(val if val else "&nbsp;", BODY)
            )
        data.append(row_cells)

    table = Table(
        data,
        colWidths=list(col_widths),
        repeatRows=1,  # repeat header on page break
    )

    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_fill),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), FONT_B),

                ("GRID", (0, 0), (-1, -1), 0.5, grid_color),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),

                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    return table


def _safe_str(x: Any) -> str:
    return ("" if x is None else str(x)).strip()
