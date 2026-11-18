"""Recall at 5 on the development queries for dense only, BM25 only, hybrid, and hybrid plus reranker.

Usage: python -m eval.retrieval_eval
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.run_eval import load_gold
from src.agents.steps import CHECK_QUERY
from src.app.store import Store
from src.index.hybrid import MODES, get_index, same_paragraph
from src.schemas import case_from_application
from src.tools.checks import lender_type_from_label
from src.tracing import activity


def pairs(split: str = "dev") -> list[tuple[str, str, list[str]]]:
    """(query, lender type, acceptable paragraph ids) for every expected citation."""
    store, out = Store(), []
    for q in load_gold(split):
        exp = q["expected"]
        if exp["checks"]:
            lender = q["scenario"]["lender_type"] if q["scenario"] else case_from_application(store.application(q["application_id"])).lender_type
            for check, want in exp["checks"].items():
                if "paragraph_id" in want:
                    out.append((CHECK_QUERY[check], lender, [want["paragraph_id"]]))
        elif exp["cite"]:
            out.append((q["question"], lender_type_from_label(q["question"]), exp["cite"]))
    return out


@activity.not_logged()
def main() -> None:
    index = get_index()
    items = pairs()
    report = {"queries": len(items), "corpus_documents": index.meta["documents"], "chunks": index.meta["chunks"],
              "by_source_format": index.meta.get("by_source_format", {}), "recall_at_5": {}}
    for mode in MODES:
        hits = 0
        for query, lender, wants in items:
            found = index.search(query, lender_type=lender, k=5, mode=mode)
            hits += any(same_paragraph(f.paragraph_id, w) for f in found for w in wants)
        report["recall_at_5"][mode] = round(hits / len(items), 3)
        print(f"{mode:14s} recall@5 = {hits}/{len(items)} = {report['recall_at_5'][mode]}")
    out = Path(__file__).resolve().parent / "results" / "retrieval_dev.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
