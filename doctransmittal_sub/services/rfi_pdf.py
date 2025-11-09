# rfi_pdf.py
from __future__ import annotations

import os, shutil, re, tempfile
from pathlib import Path
from typing import Dict, Any, Optional

# --- PDF forms (your existing dependency) ---
from fillpdf import fillpdfs

# --- Optional: stamping logos (skips if unavailable) ---
try:
    from PyPDF2 import PdfReader, PdfWriter
    _HAVE_PYPDF2 = True
except Exception:
    _HAVE_PYPDF2 = False

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    _HAVE_RL = True
except Exception:
    _HAVE_RL = False


# ------------------------- field mapping helpers -------------------------
# crude, safe regexes to map common field names in an RFI template
_PATTERNS: Dict[str, str] = {
    "number":        r"(?:\brfi\b.*\b(no|number)\b)|\b(doc|document)\s*no\b|\bnumber\b|\bid\b",
    "discipline":    r"\bdiscipline\b",
    "issued_to":     r"\bissued\s*to\b|\bto\b",
    "issued_to_co":  r"(?:\bto\s*)?company\b|\bissued\s*to\s*company\b|\brecipient\s*company\b",
    "issued_from":   r"\bissued\s*from\b|\bfrom\b|\boriginator\b",
    "issued_date":   r"\bissued\s*date\b|\bdate\s*issued\b|\bdate\b",
    "respond_by":    r"\brespond\s*by\b|\bresponse\s*due\b|\bdue\s*date\b",
    "subject":       r"\bsubject\b|\btitle\b",
    # optional (for later when you pipe the rich text)
    "background":    r"\bbackground\b",
    "request":       r"\b(information\s*requested|request)\b",

    # project header
    "job_no":        r"\b(job|project)\s*(no|number)\b",
    "project_name":  r"\b(project|job)\s*(name|title)\b",
    "client":        r"\b(client|principal|owner)\b",
}

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def _match_key(pdf_field_name: str) -> Optional[str]:
    n = _norm(pdf_field_name)
    for canon, pat in _PATTERNS.items():
        if re.search(pat, n):
            return canon
    return None


# ------------------------- core: fill + logos -------------------------
def build_rfi_field_values(*,
                           rfi: Dict[str, Any],
                           project: Dict[str, Any],
                           background_text: str = "",
                           request_text: str = "") -> Dict[str, str]:
    """Canonical values we try to push into similarly named fields."""
    return {
        "number":       str(rfi.get("number", "")),
        "discipline":   str(rfi.get("discipline", "")),
        "issued_to":    str(rfi.get("issued_to", "")),
        "issued_to_co": str(rfi.get("issued_to_company", "")),
        "issued_from":  str(rfi.get("issued_from", "")),
        "issued_date":  str(rfi.get("issued_date", "")),
        "respond_by":   str(rfi.get("respond_by", "")),
        "subject":      str(rfi.get("subject", "")),
        "background":   background_text or "",
        "request":      request_text or "",

        "job_no":       str(project.get("project_code", "")),
        "project_name": str(project.get("project_name", "")),
        "client":       str(project.get("client_company", "")),
    }


def fill_pdf_fields_from_template(template_pdf: Path,
                                  out_pdf: Path,
                                  values: Dict[str, str]) -> bool:
    """Copy template to out and write fields by fuzzy name matching."""
    template_pdf = Path(template_pdf)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_pdf, out_pdf)

    try:
        fields = fillpdfs.get_form_fields(str(out_pdf), sort=False, page_number=None) or {}
    except Exception as e:
        print(f"[RFI] Could not read form fields: {e}")
        return False

    # Build a value dict keyed by actual PDF field names
    field_dict: Dict[str, str] = {}
    for k in fields.keys():
        canon = _match_key(k)
        if not canon:
            continue
        v = values.get(canon, "")
        if v is None:
            v = ""
        field_dict[k] = str(v)

    if not field_dict:
        print("[RFI] No matching fields found in PDF (template may not be AcroForm).")
        return False

    try:
        # write in-place to the copied output
        fillpdfs.write_fillable_pdf(str(out_pdf), str(out_pdf), field_dict, flatten=False)
        return True
    except Exception as e:
        print(f"[RFI] Failed to write PDF fields: {e}")
        return False


def _stamp_logos(out_pdf: Path,
                 company_logo: Optional[Path],
                 client_logo: Optional[Path]) -> None:
    """Stamp logos on the first page. Silently skip if libs/assets missing."""
    if not (_HAVE_PYPDF2 and _HAVE_RL):
        return
    if not (company_logo or client_logo):
        return

    out_pdf = Path(out_pdf)
    try:
        reader = PdfReader(str(out_pdf))
        first = reader.pages[0]
        w = float(first.mediabox.width)
        h = float(first.mediabox.height)

        # make overlay using exact page size
        tmp_overlay = Path(tempfile.mkdtemp()) / "overlay.pdf"
        c = canvas.Canvas(str(tmp_overlay), pagesize=(w, h))

        # simple coords: ~A4 portrait top band, tune later
        # Keep logos within 120–160 px width; auto-scale height
        x_pad = 28
        y_top = h - 28
        max_w = 160
        def _draw_logo(img_path: Path, x_left: float, align_right: bool = False):
            try:
                img = ImageReader(str(img_path))
            except Exception:
                return
            iw, ih = img.getSize()
            scale = min(max_w / iw, 1.0)
            dw, dh = iw * scale, ih * scale
            x = x_left - (dw if align_right else 0.0)
            y = y_top - dh
            c.drawImage(img, x, y, width=dw, height=dh, mask='auto')

        if company_logo and Path(company_logo).is_file():
            _draw_logo(Path(company_logo), x_pad, align_right=False)
        if client_logo and Path(client_logo).is_file():
            _draw_logo(Path(client_logo), w - x_pad, align_right=True)

        c.save()

        # merge overlay onto first page
        overlay_reader = PdfReader(str(tmp_overlay))
        writer = PdfWriter()
        first.merge_page(overlay_reader.pages[0])
        writer.add_page(first)
        for i in range(1, len(reader.pages)):
            writer.add_page(reader.pages[i])

        with open(out_pdf, "wb") as f:
            writer.write(f)

    except Exception as e:
        print(f"[RFI] Logo stamping skipped: {e}")


def generate_rfi_pdf(*,
                     template_pdf: Path,
                     out_pdf: Path,
                     rfi_row: Dict[str, Any],
                     project: Dict[str, Any],
                     background_text: str = "",
                     request_text: str = "",
                     company_logo: Optional[Path] = None,
                     client_logo: Optional[Path] = None) -> bool:
    """Convenience: fill form then stamp logos."""
    values = build_rfi_field_values(rfi=rfi_row,
                                    project=project,
                                    background_text=background_text,
                                    request_text=request_text)
    ok = fill_pdf_fields_from_template(template_pdf, out_pdf, values)
    if ok:
        _stamp_logos(out_pdf, company_logo, client_logo)
    return ok
