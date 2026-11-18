"""Build the list of official RBI PDFs for the corpus, from the PDF links inside the RBI pages already downloaded.

Usage: python -m src.ingest.pdf_list
Writes data/raw/lists/pdf_download_list.html (links to click in a browser) and data/raw/lists/pdf_download_list.csv.

The PDFs are served from rbidocs.rbi.org.in, which shows a bot check to scripts, so they are downloaded by a person
in a browser and saved into data/raw/pdf/ under the file name RBI gives them. The parser matches them back to
their document by that file name, using the CSV.

Tiers:
  1  the Directions cited by rules/rules.yaml (the current PDF where only the PDF is current)
  2  every other document for HFCs, NBFCs and banks
  3  the rest of the corpus (other RBI notifications), optional
"""
from __future__ import annotations

import csv
import html
import json

from src import config
from src.ingest.parse import RULE_SOURCE_KEYS

from src.ingest.pdf_text import CURRENT_PDF, downloaded, main_pdf  # noqa: E402  (shared with the parser)

TIER_NAME = {1: "Tier 1: Directions cited by the rules file", 2: "Tier 2: other HFC, NBFC and bank documents",
             3: "Tier 3: other RBI notifications (optional)"}


def build() -> list[dict]:
    manifest = json.loads((config.PARSED_DIR / "corpus_manifest.json").read_text(encoding="utf-8"))
    cited = set(RULE_SOURCE_KEYS.values())
    have = downloaded()
    rows = []
    for d in manifest["docs"]:
        page = config.HTML_DIR / d["file"]
        if not page.exists():
            continue
        url = CURRENT_PDF.get(d["doc_key"]) or main_pdf(page.read_text(encoding="utf-8", errors="replace"))
        if not url:
            continue
        tier = 1 if d["doc_key"] in cited else 2 if d["lender_type"] in ("hfc", "nbfc", "bank") else 3
        rows.append({"tier": tier, "doc_key": d["doc_key"], "lender_type": d["lender_type"], "title": d["title"],
                     "dated": d["dated"], "pdf_file": url.rsplit("/", 1)[-1], "pdf_url": url, "page_file": d["file"],
                     "saved_at": have.get(url.rsplit("/", 1)[-1].lower(), "")})
    # Web pages that only link their PDF have no text of their own, so the corpus leaves them out until the PDF is
    # saved. List them too, so their PDF can be downloaded and they can join the corpus.
    from src.ingest.parse import body_lines, html_to_text, lender_type_of

    listed_pages = {r["page_file"] for r in rows}
    claimed = {r["pdf_file"].lower() for r in rows}
    for page in sorted(config.HTML_DIR.glob("*.html")):
        if page.name in listed_pages or not (page.stem.startswith("rbi_md_") or page.stem.startswith("notif_")):
            continue
        raw = page.read_text(encoding="utf-8", errors="replace")
        url = main_pdf(raw)
        title, dated, _ = body_lines(html_to_text(raw))
        if not url or not title or url.rsplit("/", 1)[-1].lower() in claimed:
            continue
        key = ("MD-" + page.stem[7:]) if page.stem.startswith("rbi_md_") else ("N-" + page.stem[6:])
        lender = lender_type_of(title)
        rows.append({"tier": 2 if lender in ("hfc", "nbfc", "bank") else 3, "doc_key": key, "lender_type": lender, "title": title,
                     "dated": dated, "pdf_file": url.rsplit("/", 1)[-1], "pdf_url": url, "page_file": page.name,
                     "saved_at": have.get(url.rsplit("/", 1)[-1].lower(), "")})
    rows.sort(key=lambda r: (r["tier"], r["lender_type"], r["doc_key"]))
    return rows


def write(rows: list[dict]) -> None:
    config.LISTS_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = config.LISTS_DIR / "pdf_download_list.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    parts = ["<!doctype html><meta charset='utf-8'><title>RBI PDF download list</title>",
             "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1d2433}"
             "td{padding:4px 8px;border-bottom:1px solid #ddd;vertical-align:top}th{text-align:left;padding:4px 8px}"
             "a{color:#1d4e89}h2{margin-top:32px}</style>",
             "<h1>RBI PDFs for the Loan Guarantor corpus</h1>",
             "<p>Click each link and save the PDF into <code>data/raw/pdf/</code> without renaming it. "
             "Then run <code>python -m src.ingest.parse</code> and <code>python -m src.index.hybrid build</code>. "
             "Any document without a PDF keeps its web page text. Re-run <code>python -m src.ingest.pdf_list</code> to refresh the ticks.</p>",
             f"<p><b>{sum(1 for r in rows if r['saved_at'])} of {len(rows)} downloaded.</b> "
             "Saved files are found in data/raw/pdf. "
             "<label><input type='checkbox' id='hide' onchange=\"document.body.classList.toggle('missing-only', this.checked)\"> "
             "Show only the ones still to download</label></p>",
             "<style>.done{color:#2f6b3a}.missing-only tr.got{display:none}</style>"]
    for tier in (1, 2, 3):
        group = [r for r in rows if r["tier"] == tier]
        got = sum(1 for r in group if r["saved_at"])
        parts.append(f"<h2>{TIER_NAME[tier]}: {got} of {len(group)} downloaded</h2>"
                     "<table><tr><th>#</th><th>Status</th><th>Document</th><th>Lender</th><th>Dated</th><th>PDF</th></tr>")
        for i, r in enumerate(group, 1):
            status = (f"<span class='done'>✓ downloaded</span><br><small>{html.escape(r['saved_at'])}</small>" if r["saved_at"] else "to download")
            parts.append(f"<tr class='{'got' if r['saved_at'] else 'todo'}'><td>{i}</td><td>{status}</td><td>{html.escape(r['title'])}<br><small>{r['doc_key']}</small></td><td>{r['lender_type']}</td>"
                         f"<td>{r['dated']}</td><td><a href='{html.escape(r['pdf_url'])}'>{html.escape(r['pdf_file'])}</a></td></tr>")
        parts.append("</table>")
    (config.LISTS_DIR / "pdf_download_list.html").write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    rows = build()
    write(rows)
    for t in (1, 2, 3):
        group = [r for r in rows if r["tier"] == t]
        print(f"{TIER_NAME[t]}: {sum(1 for r in group if r['saved_at'])} of {len(group)} downloaded")
    missing1 = [r["pdf_file"] for r in rows if r["tier"] == 1 and not r["saved_at"]]
    print("Tier 1 still to download:", ", ".join(missing1) or "none")
    print("wrote", config.LISTS_DIR / "pdf_download_list.html")
