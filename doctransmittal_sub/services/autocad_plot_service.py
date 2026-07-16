from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict


def checkprint_incoming_dir(db_path: Path) -> Path:
    """Return the standard CheckPrint incoming folder used by resubmissions."""
    try:
        from .checkprint_service import _checkprint_incoming_dir
    except Exception:
        from checkprint_service import _checkprint_incoming_dir  # type: ignore
    return _checkprint_incoming_dir(Path(db_path))


def _get_active_autocad() -> tuple[Any, Any]:
    """Return (acad, active_doc) for the running AutoCAD session."""
    try:
        import comtypes.client  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "The AutoCAD COM module 'comtypes' is not available.\n"
            "Install comtypes or rebuild the executable with comtypes included."
        ) from exc

    try:
        acad = comtypes.client.GetActiveObject("AutoCAD.Application")
    except Exception as exc:
        raise RuntimeError(
            "AutoCAD is not currently available through COM.\n"
            "Open AutoCAD and the required drawing/layout, then try again."
        ) from exc

    try:
        doc = acad.ActiveDocument
    except Exception as exc:
        raise RuntimeError("AutoCAD does not appear to have an active drawing open.") from exc

    if doc is None:
        raise RuntimeError("AutoCAD does not have an active drawing open.")

    return acad, doc


def active_autocad_summary() -> Dict[str, str]:
    """Return a lightweight summary of the current AutoCAD document/layout."""
    acad, doc = _get_active_autocad()
    try:
        layout = doc.ActiveLayout
    except Exception:
        layout = None

    def _safe(value: Any) -> str:
        try:
            return str(value or "")
        except Exception:
            return ""

    return {
        "document_name": _safe(getattr(doc, "Name", "")),
        "document_full_name": _safe(getattr(doc, "FullName", "")),
        "layout_name": _safe(getattr(layout, "Name", "")) if layout is not None else "",
        "plot_style": _safe(getattr(layout, "StyleSheet", "")) if layout is not None else "",
    }


def plot_active_layout_to_pdf(
    output_pdf: Path,
    *,
    plot_style: str | None = None,
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    """
    Plot the currently active AutoCAD document/layout to ``output_pdf``.

    This deliberately does not validate the DWG filename, title block, drawing
    number, or revision. Document Register supplies the required CheckPrint
    output filename; the user is responsible for having the correct AutoCAD
    drawing/layout active.

    The AutoCAD command expects an existing LISP command named PLOTCURRENTLAYOUT
    that accepts:
        1. output PDF path
        2. CTB/STB plot style table
    """
    output_pdf = Path(output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    acad, doc = _get_active_autocad()

    try:
        layout = doc.ActiveLayout
    except Exception as exc:
        raise RuntimeError("Could not read the active AutoCAD layout.") from exc

    try:
        doc.Activate()
    except Exception:
        pass

    try:
        acad.ActiveDocument = doc
    except Exception:
        pass

    try:
        existing_backgroundplot = doc.GetVariable("BACKGROUNDPLOT")
    except Exception:
        existing_backgroundplot = None

    try:
        # Background plotting makes SendCommand timing unreliable.
        doc.SetVariable("BACKGROUNDPLOT", 0)
    except Exception:
        pass

    if plot_style is None:
        try:
            plot_style = str(layout.StyleSheet or "")
        except Exception:
            plot_style = ""

    before_mtime = output_pdf.stat().st_mtime if output_pdf.exists() else None
    before_size = output_pdf.stat().st_size if output_pdf.exists() else None

    # Forward slashes reduce quoting/path escaping issues inside AutoCAD.
    out_arg = str(output_pdf).replace("\\", "/")
    ps_arg = str(plot_style or "")

    command = f'PLOTCURRENTLAYOUT\n"{out_arg}"\n"{ps_arg}"\n'

    try:
        doc.SendCommand(command)
    except Exception as exc:
        if existing_backgroundplot is not None:
            try:
                doc.SetVariable("BACKGROUNDPLOT", existing_backgroundplot)
            except Exception:
                pass
        raise RuntimeError(f"Failed to send PLOTCURRENTLAYOUT to AutoCAD:\n{exc}") from exc

    deadline = time.time() + max(5, int(timeout_seconds))
    last_size = None
    stable_count = 0
    result: Dict[str, Any] | None = None

    while time.time() < deadline:
        if output_pdf.exists():
            try:
                st = output_pdf.stat()
                changed = before_mtime is None or st.st_mtime != before_mtime or st.st_size != before_size
                non_empty = st.st_size > 0

                if non_empty and changed:
                    if last_size == st.st_size:
                        stable_count += 1
                    else:
                        stable_count = 0
                    last_size = st.st_size

                    # Require stable size across checks so we do not return while
                    # AutoCAD is still flushing the PDF.
                    if stable_count >= 2:
                        result = {
                            "ok": True,
                            "output_pdf": str(output_pdf),
                            "document_name": str(getattr(doc, "Name", "") or ""),
                            "document_full_name": str(getattr(doc, "FullName", "") or ""),
                            "layout_name": str(getattr(layout, "Name", "") or ""),
                            "plot_style": ps_arg,
                        }
                        break
            except OSError:
                pass
        time.sleep(0.5)

    if existing_backgroundplot is not None:
        try:
            doc.SetVariable("BACKGROUNDPLOT", existing_backgroundplot)
        except Exception:
            pass

    if result is not None:
        return result

    raise RuntimeError(
        "AutoCAD plot command was sent, but the expected PDF was not created "
        f"within {timeout_seconds} seconds.\n\nExpected output:\n{output_pdf}"
    )
