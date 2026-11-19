"""PDF ingestion: find the official PDF for an RBI document and turn it into clean body lines.

The parser (src/ingest/parse.py) uses a document's PDF when one has been saved, and its web page otherwise.
PDFs are saved by a person in a browser (rbidocs.rbi.org.in blocks scripts) into data/raw/pdf, under the file name RBI gives them.

Text comes from poppler's pdftotext, which kept words, hyphenated terms and table cells intact where pypdf split
words ("d irections") and dropped a risk weight cell; pypdf is the fallback when poppler is not installed.
Cleaning removes what the PDF has and the web page does not: page numbers, the table of contents, Hindi
letterhead lines and the "Withdrawn" watermark.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from src import config

LINK_RE = re.compile(r"https?://rbidocs\.rbi\.org\.in/rdocs/[^\"'\s>]+?\.pdf", re.I)
# Bank Directions whose current version is published only as a PDF; their web page is the previous version.
CURRENT_PDF = {"CB-CF": "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/154MD.PDF",
               "CB-CAP": "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/151MD.PDF"}
PDF_DIRS = ("raw/pdf",)   # under data/

PAGE_NO = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of\s*\d{1,4})?\s*$", re.I)
TOC_LINE = re.compile(r"\.{6,}\s*\d{1,4}\s*$")
WATERMARK = re.compile(r"^\s*withdrawn\s*$", re.I)
LETTERHEAD = re.compile(r"^(_{5,}|-{5,}|Caution: RBI never sends mails.*|to anyone\. Please do not respond in any manner to such offers\.?)$", re.I)
DEVANAGARI = re.compile(r"[ऀ-ॿ�]")
VERSION_RE = re.compile(r"\(\s*updated\s+as\s+on\s+([A-Za-z]+\s+\d{1,2}\s*,\s*\d{4})\s*\)", re.I)


def is_pdf(path: Path) -> bool:
    """A real PDF, not a bot-check page saved under a .pdf name."""
    try:
        with open(path, "rb") as fh:
            return path.stat().st_size > 1000 and fh.read(5).startswith(b"%PDF")
    except OSError:
        return False


def downloaded() -> dict[str, str]:
    """Lower-case file name -> path relative to the project, for every valid PDF saved. 'X (1).pdf' counts as X.pdf."""
    found: dict[str, str] = {}
    for sub in PDF_DIRS:
        folder = config.DATA / sub
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() != ".pdf" or not is_pdf(f):
                continue
            name = re.sub(r" \(\d+\)(?=\.pdf$)", "", f.name, flags=re.I).lower()
            found.setdefault(name, f"data/{sub}/{f.name}")
    return found


def main_pdf(page_html: str) -> Optional[str]:
    """The document's own PDF link on its RBI web page (annexes and site-wide PDFs are left out)."""
    links = list(dict.fromkeys(LINK_RE.findall(page_html)))
    main = [link for link in links if "/notification/pdfs/" in link.lower()]
    return main[0] if main else None


def pdf_for(doc_key: str, page_html: str, have: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    """(url, saved path) of the PDF to use for a document, or (url, None) when it is not saved."""
    url = CURRENT_PDF.get(doc_key) or main_pdf(page_html)
    if not url:
        return None, None
    return url, have.get(url.rsplit("/", 1)[-1].lower())


def _is_hindi(line: str) -> bool:
    letters = [c for c in line if not c.isspace()]
    return bool(letters) and len(DEVANAGARI.findall(line)) / len(letters) > 0.3


def clean_pages(pages: list[str]) -> list[str]:
    """Body lines from the text of each page, in order."""
    out: list[str] = []
    for text in pages:
        lines = [ln.rstrip() for ln in (text or "").split("\n")]
        # page numbers sit in the first or last few lines of a page; a lone number elsewhere may be a table cell
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        edges = set(filled[:2] + filled[-2:])
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if i in edges and PAGE_NO.match(s):
                continue
            if TOC_LINE.search(s) or WATERMARK.match(s) or LETTERHEAD.match(s) or _is_hindi(s):
                continue
            out.append(re.sub(r"[ \t]+", " ", s))
    return out


def _pages_pdftotext(path: str) -> list[str]:
    import subprocess

    out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, text=True, timeout=300, check=True).stdout
    return out.split("\f")


def _pages_pypdf(path: str) -> list[str]:
    from pypdf import PdfReader

    pages = []
    for page in PdfReader(path).pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:   # a damaged page is skipped, not fatal
            pages.append("")
    return pages


def join_hyphenated(lines: list[str]) -> list[str]:
    """'pre-' at a line end followed by 'payment' becomes 'pre-payment'."""
    out: list[str] = []
    for ln in lines:
        if out and re.search(r"[A-Za-z]-$", out[-1]) and ln[:1].islower():
            out[-1] += ln
        else:
            out.append(ln)
    return out


def extract(path: str | Path) -> tuple[list[str], dict]:
    """Clean body lines of a PDF, and facts about it (pages, extractor, version line if the PDF states one)."""
    import shutil

    full = str(config.ROOT / path) if not Path(path).is_absolute() else str(path)
    extractor = "pdftotext" if shutil.which("pdftotext") else "pypdf"
    pages = _pages_pdftotext(full) if extractor == "pdftotext" else _pages_pypdf(full)
    lines = join_hyphenated(clean_pages(pages))
    head = " ".join(lines[:40])
    m = VERSION_RE.search(head)
    return lines, {"pages": len([p for p in pages if p.strip()]), "extractor": extractor, "version": m.group(1) if m else None}
