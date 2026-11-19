"""Download RBI Master Directions and notifications from www.rbi.org.in (plain HTML pages only).

Polite: one request per second, skips files already on disk, never touches the
CAPTCHA-protected rbidocs PDF host.

Usage: python -m src.ingest.download [--notifications N]
"""
import argparse
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "html"
BASE = "https://www.rbi.org.in/Scripts/"
UA = "Mozilla/5.0 (compatible; course-project-corpus-builder)"
LATEST_NOTIFICATION_ID = 13752  # top of the notifications index on 2026-11-12


def fetch(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 20000:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
    except Exception as exc:  # network errors are logged and skipped
        print(f"  failed {url}: {exc}", flush=True)
        return False
    finally:
        time.sleep(1.0)
    dest.write_bytes(data)
    return True


def has_body(path: Path) -> bool:
    """A page with real text, not just a link to a PDF or an empty id."""
    html = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)
    return len(re.sub(r"\s+", " ", text)) > 14000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notifications", type=int, default=220, help="notification pages with text to collect")
    ap.add_argument("--max-scan", type=int, default=700)
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    index = (ROOT / "data" / "raw" / "lists" / "md_index.tsv").read_text(encoding="utf-8").splitlines()
    md_ids = sorted({int(m.group(1)) for line in index if (m := re.match(r"(\d+)", line))})
    for n, doc_id in enumerate(md_ids, 1):
        fetch(f"{BASE}BS_ViewMasDirections.aspx?id={doc_id}", RAW / f"rbi_md_{doc_id}.html")
        if n % 50 == 0:
            print(f"master directions {n}/{len(md_ids)}", flush=True)

    got, scanned, doc_id = 0, 0, LATEST_NOTIFICATION_ID
    md_set = set(md_ids)
    while got < args.notifications and scanned < args.max_scan and doc_id > 0:
        if doc_id not in md_set:
            dest = RAW / f"notif_{doc_id}.html"
            scanned += 1
            if fetch(f"{BASE}NotificationUser.aspx?Id={doc_id}&Mode=0", dest) and has_body(dest):
                got += 1
                if got % 50 == 0:
                    print(f"notifications with text {got} (scanned {scanned})", flush=True)
        doc_id -= 1
    print(f"done: {len(md_ids)} master direction pages, {got} notifications with text", flush=True)


if __name__ == "__main__":
    main()
