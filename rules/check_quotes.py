"""Check that every quote in rules/rules.yaml appears verbatim in its parsed RBI source.

Whitespace is collapsed on both sides; nothing else is normalised.
Usage: python rules/check_quotes.py [--sample N --seed S]
Exit code 1 if any quote is missing.
"""
import argparse
import random
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="check only N random records with quotes")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    doc = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    sources = doc["sources"]
    cache = {}

    def text_of(source_id: str) -> str:
        if source_id not in cache:
            cache[source_id] = norm((ROOT / sources[source_id]["local_file"]).read_text(encoding="utf-8"))
        return cache[source_id]

    rules = doc["rules"]
    if args.sample:
        with_quotes = [r for r in rules if r.get("quote")]
        rules = random.Random(args.seed).sample(with_quotes, args.sample)

    bad = 0
    for rule in rules:
        checks = [(rule["source"], q) for q in rule.get("quote") or []]
        link = rule.get("link_quote")
        if link:
            checks.append((link["source"], link["quote"]))
        missing = [q for src, q in checks if norm(q) not in text_of(src)]
        state = "OK  " if not missing else "FAIL"
        print(f"{state} {rule['rule_id']:34s} {rule['status']:10s} {len(checks)} quote(s) para {rule.get('paragraph')}")
        for q in missing:
            bad += 1
            print(f"       not found: {norm(q)[:140]}")
        if rule["status"] == "verified" and not checks:
            bad += 1
            print("       verified record has no quote")
    print(f"\n{len(rules)} record(s) checked, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
