"""PDF annotation scanning used to prevent marked-up drawings entering transmittals."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable


DEFAULT_IGNORED_AUTHORS = "AutoCAD SHX Text"


@dataclass(frozen=True)
class MarkupFinding:
    file: Path
    status: str
    page: str = ""
    subtype: str = ""
    author: str = ""
    contents: str = ""
    ignored_count: int = 0
    error: str = ""


def split_filters(raw: str) -> list[str]:
    """Parse comma, semicolon, or newline-separated case-insensitive wildcards."""
    raw = (raw or "").replace(";", "\n").replace(",", "\n")
    return [item.strip().casefold() for item in raw.splitlines() if item.strip()]


def _matches_filter(value: str, patterns: list[str]) -> bool:
    candidate = (value or "").casefold()
    return any(fnmatch.fnmatchcase(candidate, pattern) for pattern in patterns)


def scan_pdf(path: Path, author_patterns: list[str], subtype_patterns: list[str]) -> list[MarkupFinding]:
    """Return all non-excluded PDF annotations, or one PASS/CHECK ERROR finding."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("The PDF markup scan requires the PyMuPDF package.") from exc

    findings: list[MarkupFinding] = []
    ignored = 0
    try:
        with fitz.open(path) as document:
            if document.needs_pass:
                try:
                    document.authenticate("")
                except Exception:
                    pass

            for page_number, page in enumerate(document, start=1):
                for annotation in page.annots() or []:
                    try:
                        subtype = annotation.type[1] or "Unknown"
                        info = annotation.info or {}
                        author = (info.get("title") or "").strip()
                        contents = (info.get("content") or "").strip()
                        if _matches_filter(author, author_patterns) or _matches_filter(subtype, subtype_patterns):
                            ignored += 1
                            continue
                        findings.append(MarkupFinding(
                            file=path,
                            status="MARKUP FOUND",
                            page=str(page_number),
                            subtype=subtype,
                            author=author or "(blank)",
                            contents=contents.replace("\r", " ").replace("\n", " ")[:500],
                        ))
                    except Exception as exc:
                        findings.append(MarkupFinding(
                            file=path,
                            status="CHECK ERROR",
                            page=str(page_number),
                            error=f"Could not read annotation: {exc}",
                        ))
    except Exception as exc:
        return [MarkupFinding(file=path, status="CHECK ERROR", error=str(exc))]

    if not findings:
        return [MarkupFinding(file=path, status="PASS", ignored_count=ignored)]
    return [replace(finding, ignored_count=ignored) for finding in findings]


def scan_pdfs(paths: Iterable[Path], ignored_authors: str, ignored_subtypes: str) -> list[MarkupFinding]:
    """Scan the supplied mapped PDF paths, preserving their order."""
    authors = split_filters(ignored_authors)
    subtypes = split_filters(ignored_subtypes)
    findings: list[MarkupFinding] = []
    for path in paths:
        pdf_path = Path(path)
        if not pdf_path.exists():
            findings.append(MarkupFinding(pdf_path, "CHECK ERROR", error="Mapped PDF file was not found."))
        else:
            findings.extend(scan_pdf(pdf_path, authors, subtypes))
    return findings
