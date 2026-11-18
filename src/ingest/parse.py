"""Parse downloaded RBI pages into paragraph chunks with paragraph number, lender type and date metadata.

Usage: python -m src.ingest.parse
Writes data/parsed/chunks.jsonl and data/parsed/corpus_manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser

from src import config

# Documents cited by rules/rules.yaml keep the short keys used there.
RULE_SOURCE_KEYS = {
    "rbi_md_12939": "HFC-MD", "rbi_md_12942": "NBFC-RBC", "rbi_md_12943": "NBFC-KYC", "rbi_md_12954": "NBFC-CRM",
    "rbi_md_13399": "CB-SA-2027", "rbi_md_13140": "CB-RBC", "rbi_md_13141": "CB-KYC", "rbi_md_13153": "CB-CRM",
    "prev_13156_271": "CB-CF", "prev_13159_270": "CB-CAP",
}
MAX_WORDS = 220
# "58. No housing finance company ..." starts paragraph 58. PDFs also print "352.In case ...", with no space.
PARA_RE = re.compile(r"^\s*(?:\d+\[)?(\d{1,3})([A-Z]{0,2})\.(?:\s+\S|(?=[A-Z(“\"]))")
MIN_CHARS = 300   # a web page with less body text than this only links its PDF
ARCHIVE_MENU = re.compile(r"(19\d\d|20\d\d|Archives|All Months|All Years|January|February|March|April|May|June|July|"
                          r"August|September|October|November|December)")
DATE_RE = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}$")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "tr", "div", "li", "h1", "h2", "h3", "h4", "table"):
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in ("p", "tr", "table", "div"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.out).replace("\xa0", " ")
    text = re.sub(r"[ \t\r]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def lender_type_of(title: str) -> str:
    t = title.lower()
    if "housing finance" in t:
        return "hfc"
    if "non-banking financial" in t or "nbfc" in t:
        return "nbfc"
    if "commercial banks" in t:
        return "bank"
    return "other"


def body_lines(text: str) -> tuple[str, str, list[str]]:
    """Return (title, date, body lines) with the site navigation and footer removed."""
    lines = [ln.strip() for ln in text.split("\n")]
    title = ""
    start = 0
    for i, ln in enumerate(lines[:140]):
        if ln.startswith("|") and len(ln) > 30 and not title and i > 60:
            title = ln.lstrip("| ").strip()
            start = i + 1
    end = len(lines)
    for i in range(len(lines) - 1, max(len(lines) - 80, 0), -1):
        if lines[i] == "Top" or lines[i].startswith("Back to previous page"):
            end = i
    body = [ln for ln in lines[start:end] if ln and ln != "|"]
    while body and ARCHIVE_MENU.fullmatch(body[-1]):   # the site's archive menu of months and years
        body.pop()
    dated = next((ln for ln in body[:12] if DATE_RE.match(ln)), "")
    return title, dated, body


def split_paragraphs(body: list[str]) -> list[tuple[str, str]]:
    """Group lines under top-level numbered paragraphs. Nested lists restart at 1, so a number is only
    accepted as a new paragraph when it does not go backwards."""
    out: list[tuple[str, list[str]]] = [("0", [])]
    current = 0
    for ln in body:
        m = PARA_RE.match(ln)
        if m:
            n = int(m.group(1))
            if current <= n <= current + 60 and not (n == current and not m.group(2)):
                current = n
                out.append((f"{n}{m.group(2)}", [ln]))
                continue
        out[-1][1].append(ln)
    return [(p, "\n".join(ls)) for p, ls in out if ls]


def sub_chunks(text: str) -> list[str]:
    chunks, cur, words = [], [], 0
    for ln in text.split("\n"):
        w = len(ln.split())
        if cur and words + w > MAX_WORDS:
            chunks.append("\n".join(cur))
            cur, words = [], 0
        while w > MAX_WORDS * 2:  # one very long line
            parts = ln.split()
            chunks.append(" ".join(parts[:MAX_WORDS]))
            ln = " ".join(parts[MAX_WORDS:])
            w = len(ln.split())
        cur.append(ln)
        words += w
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def iso_date(dated: str) -> str:
    try:
        return datetime.strptime(dated, "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def _top(paragraph: str) -> str:
    m = re.match(r"\d+[A-Z]{0,2}", paragraph)
    return m.group(0) if m else paragraph


def rule_paragraphs() -> dict[str, set[str]]:
    """Top-level paragraphs each document must keep, because a verified rule in rules/rules.yaml cites them."""
    import yaml

    need: dict[str, set[str]] = {}
    doc = yaml.safe_load(config.RULES_FILE.read_text(encoding="utf-8"))
    for r in doc["rules"]:
        pid = r.get("paragraph_id") or ""
        if r["status"] != "verified" or "/" not in pid or "annex" in pid.lower():
            continue
        src, para = pid.split("/", 1)
        need.setdefault(src, set()).add(_top(para))
    return need


def pdf_check(html_paras: list[tuple[str, str]], pdf_paras: list[tuple[str, str]], must_keep: set[str]) -> str:
    """Why a PDF's text should not replace the web page text, or '' when it is good enough.
    When the web page has no text of its own (it only links the PDF), the PDF only has to hold some text."""
    words = lambda ps: sum(len(t.split()) for _, t in ps)  # noqa: E731
    if words(pdf_paras) < 50:
        return f"PDF has almost no extractable text ({words(pdf_paras)} words); it may be a scanned image"
    if words(pdf_paras) < 0.6 * words(html_paras):
        return f"PDF text is short ({words(pdf_paras)} words against {words(html_paras)} on the web page)"
    html_ids = {_top(p) for p, _ in html_paras if p != "0"}
    pdf_ids = {_top(p) for p, _ in pdf_paras if p != "0"}
    if len(html_ids) >= 5 and len(pdf_ids) < 0.6 * len(html_ids):
        return f"only {len(pdf_ids)} numbered paragraphs found in the PDF against {len(html_ids)} on the web page"
    missing = sorted(must_keep - pdf_ids)
    if missing:
        return "paragraphs cited by the rules file not found in the PDF: " + ", ".join(missing)
    return ""


def parse_all() -> dict:
    """Parse every RBI document. A document uses its official PDF when one is saved and passes the checks
    in pdf_check(); otherwise its web page. Each chunk records which one it came from."""
    from src.ingest.pdf_text import VERSION_RE, downloaded, extract, pdf_for

    chunks: list[dict] = []
    docs: list[dict] = []
    have = downloaded()
    need = rule_paragraphs()
    # A PDF belongs to one document. The documents the rules file cites claim theirs first, so the current PDF
    # of a bank Direction is indexed once, under the key the rules cite (CB-CF), not also under its new page.
    claimed: dict[str, str] = {}
    for stem, key in RULE_SOURCE_KEYS.items():
        page = config.HTML_DIR / f"{stem}.html"
        if page.exists():
            _, saved_pdf = pdf_for(key, page.read_text(encoding="utf-8", errors="replace"), have)
            if saved_pdf:
                claimed[saved_pdf] = key
    for path in sorted(config.HTML_DIR.glob("*.html")):
        stem = path.stem
        if not (stem.startswith("rbi_md_") or stem.startswith("notif_") or stem in RULE_SOURCE_KEYS):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = html_to_text(raw)
        title, dated, body = body_lines(text)
        if not title:
            continue  # empty id
        doc_key = RULE_SOURCE_KEYS.get(stem) or ("MD-" + stem[7:] if stem.startswith("rbi_md_") else "N-" + stem[6:])
        lender = lender_type_of(title)
        paras, source, record = split_paragraphs(body), "html", {}
        url, saved = pdf_for(doc_key, raw, have)
        if saved and claimed.get(saved, doc_key) != doc_key:
            continue  # the same PDF is indexed under the document the rules file cites
        html_chars = sum(len(ln) for ln in body)
        if html_chars < MIN_CHARS and not saved:
            continue  # the web page only links a PDF, and the PDF has not been saved
        if saved:
            record = {"pdf_file": saved}
            try:
                lines, info = extract(saved)
                pdf_paras = split_paragraphs(lines)
                why = pdf_check(paras, pdf_paras, need.get(doc_key, set()))
            except Exception as exc:  # an unreadable PDF falls back to the web page
                info, pdf_paras, why = {}, [], f"PDF could not be read: {exc}"
            record["pdf_pages"] = info.get("pages")
            record["pdf_extractor"] = info.get("extractor")
            if why and html_chars < MIN_CHARS:
                continue  # no usable text from either source
            if why:
                record["fallback_reason"] = why
            else:
                paras, source = pdf_paras, "pdf"
                if info.get("version"):
                    record["pdf_version"] = info["version"]
                    title = VERSION_RE.sub(f"(Updated as on {info['version']})", title) if VERSION_RE.search(title) else title
        n_before = len(chunks)
        for para, ptext in paras:
            for k, piece in enumerate(sub_chunks(ptext)):
                chunks.append({
                    "chunk_id": f"{doc_key}/{para}#{k}", "paragraph_id": f"{doc_key}/{para}", "doc_key": doc_key,
                    "title": title, "lender_type": lender, "dated": iso_date(dated), "text": piece, "source_format": source,
                })
        docs.append({"doc_key": doc_key, "title": title, "lender_type": lender, "dated": iso_date(dated),
                     "file": path.name, "source_format": source, "pdf_url": url, **record, "chunks": len(chunks) - n_before})

    annex = config.TEXT_DIR / "365MD06022026_AN5.txt"
    if annex.exists():
        text = re.sub(r"\n\s*\d{3}\s*\n", "\n", annex.read_text(encoding="utf-8"))
        text = re.sub(r"[ \t]+", " ", text)
        title = "Reserve Bank of India (Housing Finance Companies) Directions, 2025 - Annex V: Valuation of Properties – Empanelment of Valuers"
        pieces = sub_chunks("\n".join(ln.strip() for ln in text.split("\n") if ln.strip()))
        for k, piece in enumerate(pieces):
            chunks.append({"chunk_id": f"HFC-MD/Annex-V#{k}", "paragraph_id": "HFC-MD/Annex-V", "doc_key": "HFC-MD",
                           "title": title, "lender_type": "hfc", "dated": "2025-11-28", "text": piece, "source_format": "pdf"})
        docs.append({"doc_key": "HFC-MD/Annex-V", "title": title, "lender_type": "hfc", "dated": "2025-11-28",
                     "file": annex.name, "source_format": "pdf", "chunks": len(pieces)})

    out = config.PARSED_DIR / "chunks.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for ch in chunks:
            fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
    version = hashlib.sha256(out.read_bytes()).hexdigest()[:12]
    manifest = {"corpus_version": version, "documents": len(docs), "chunks": len(chunks),
                "by_lender_type": {k: sum(1 for d in docs if d["lender_type"] == k) for k in ("hfc", "nbfc", "bank", "other")},
                "by_source_format": {k: sum(1 for d in docs if d.get("source_format") == k) for k in ("pdf", "html")},
                "pdf_fallbacks": [{"doc_key": d["doc_key"], "reason": d["fallback_reason"]} for d in docs if d.get("fallback_reason")],
                "docs": docs}
    (config.PARSED_DIR / "corpus_manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    m = parse_all()
    print({k: v for k, v in m.items() if k not in ("docs", "pdf_fallbacks")})
    for f in m["pdf_fallbacks"]:
        print("  web page kept for", f["doc_key"], "-", f["reason"])
