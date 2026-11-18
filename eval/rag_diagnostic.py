"""Retrieval versus generation: was the expected paragraph retrieved, and was the answer right?

Usage: python -m eval.rag_diagnostic
Reads the saved evaluation rows (eval/results/<split>_<strategy>.jsonl) and their traces; makes no model calls.

For every gold query that expects a paragraph, it takes the paragraphs the pipeline actually retrieved in that run
(from the trace) and splits the queries into four cells:

                         answer right        answer wrong
  paragraph retrieved    working             generator error   (the model had the evidence and still got it wrong)
  paragraph missed       lucky / tool-led    retrieval error   (fix the search before blaming the model)

This is the diagnostic the course asks for: retrieval recall measured apart from end-to-end answer quality,
so a weak generator is not mistaken for weak retrieval, or the other way round.
Writes eval/results/rag_diagnostic.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.run_eval import load_gold
from src.index.hybrid import same_paragraph

RESULTS = Path(__file__).resolve().parent / "results"


def expected_paragraphs(q: dict) -> list[str]:
    exp = q["expected"]
    return [w["paragraph_id"] for w in exp["checks"].values() if "paragraph_id" in w] or list(exp["cite"] or [])


def traces(split: str) -> dict[str, dict]:
    path = RESULTS / f"traces_{split}.jsonl"
    out = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            t = json.loads(line)
            if t.get("type") == "trace":
                out[t["trace_id"]] = t
    return out


def diagnose(split: str, strategy: str) -> dict | None:
    rows_path = RESULTS / f"{split}_{strategy}.jsonl"
    if not rows_path.exists():
        return None
    gold = {q["id"]: q for q in load_gold(split)}
    tr = traces(split)
    cells = {"retrieved_right": [], "retrieved_wrong": [], "missed_right": [], "missed_wrong": []}
    for line in rows_path.open(encoding="utf-8"):
        row = json.loads(line)
        wants = expected_paragraphs(gold[row["id"]])
        t = tr.get(row["trace_id"])
        if not wants or t is None or row.get("stopped"):
            continue   # refusals, and runs stopped by a limit, say nothing about retrieval
        got = t["outcome"].get("retrieved") or []
        found = any(same_paragraph(g, w) for g in got for w in wants)
        cell = ("retrieved" if found else "missed") + ("_right" if row["correct"] else "_wrong")
        cells[cell].append(row["id"])
    n = sum(len(v) for v in cells.values())
    found = len(cells["retrieved_right"]) + len(cells["retrieved_wrong"])
    return {
        "queries_with_expected_paragraph": n,
        "pipeline_recall": round(found / n, 3) if n else None,
        "answer_accuracy": round((len(cells["retrieved_right"]) + len(cells["missed_right"])) / n, 3) if n else None,
        "accuracy_when_retrieved": round(len(cells["retrieved_right"]) / found, 3) if found else None,
        "accuracy_when_missed": round(len(cells["missed_right"]) / (n - found), 3) if n - found else None,
        "cells": {k: sorted(v) for k, v in cells.items()},
    }


def main() -> None:
    report = {}
    for split, strategies in (("dev", ("A", "B", "C")), ("test", ("A", "C"))):
        for s in strategies:
            d = diagnose(split, s)
            if d:
                report[f"{split}_{s}"] = d
    (RESULTS / "rag_diagnostic.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("run      queries  pipeline recall  answer accuracy  accuracy if retrieved  accuracy if missed  generator errors  retrieval errors")
    for k, d in report.items():
        c = d["cells"]
        print(f"{k:8s} {d['queries_with_expected_paragraph']:7d}  {d['pipeline_recall']!s:15s}  {d['answer_accuracy']!s:15s}  "
              f"{d['accuracy_when_retrieved']!s:21s}  {d['accuracy_when_missed']!s:18s}  "
              f"{','.join(c['retrieved_wrong']) or '-':16s}  {','.join(c['missed_wrong']) or '-'}")


if __name__ == "__main__":
    main()
