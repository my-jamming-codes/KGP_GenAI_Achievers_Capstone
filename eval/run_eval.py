"""Evaluation on the frozen gold file. This is the only code that reads gold/gold_queries.jsonl.

Usage: python -m eval.run_eval --split dev --strategies A B C
       python -m eval.run_eval --split test --strategies A C
Prints accuracy, citation rate and the failure table, and writes eval/results/.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from src import config
from src.agents import loop
from src.app.store import Store
from src.index.hybrid import get_index, same_paragraph
from src.llm import LLM
from src.rules import source_lender_type
from src.schemas import Case, case_from_application
from src.tracing import activity

ROOT = Path(__file__).resolve().parent
GOLD = config.ROOT / "gold" / "gold_queries.jsonl"
CATEGORIES = ["retrieval miss", "reasoning error", "tool call error", "hallucination"]


def load_gold(split: str) -> list[dict]:
    rows = [json.loads(line) for line in GOLD.open(encoding="utf-8")]
    return [r for r in rows if split == "all" or r["split"] == split]


def build_inputs(q: dict, store: Store):
    case, screen = None, None
    if q["application_id"]:
        app = store.application(q["application_id"])
        case = case_from_application(app)
        if q.get("shared_tab"):
            screen = {"application_id": q["application_id"], "tab": q["shared_tab"], "fields": store.masked_view(app)["tabs"][q["shared_tab"]]}
    elif q["scenario"]:
        case = Case(**q["scenario"])
    return case, screen


def score(q: dict, out) -> dict:
    exp = q["expected"]
    row = {"id": q["id"], "type": q["type"], "trace_id": out.trace_id, "refused": out.refused, "stopped": out.stopped,
           "tokens": out.tokens, "critic_failures": out.critic_failures, "critic_kinds": out.critic_kinds, "steps": out.steps_used,
           "expected_cites": 0, "cited": 0, "first_pass_correct": None}
    if exp["refuse"]:
        row["correct"] = out.refused
        row["detail"] = "" if out.refused else "not refused"
        return row
    problems = []
    if out.refused:
        problems.append(f"refused ({out.refusal_reason})")
    if exp["checks"]:
        by = {r.check: r for r in out.results}
        first_ok = True
        for check, want in exp["checks"].items():
            got = by.get(check)
            fp = out.first_pass.get(check)
            if "paragraph_id" in want:
                row["expected_cites"] += 1
                row["cited"] += bool(got and got.paragraph_id and same_paragraph(got.paragraph_id, want["paragraph_id"]))
            if got is None:
                problems.append(f"{check}: not checked (expected {want['status']})")
                first_ok = False
                continue
            if got.status != want["status"]:
                problems.append(f"{check}: got {got.status}, expected {want['status']}")
            elif "value" in want and got.values.get("risk_weight_pct") != want["value"]:
                problems.append(f"{check}: value {got.values.get('risk_weight_pct')}, expected {want['value']}")
            if want["status"] != "unknown":
                ok = fp is not None and fp["status"] == want["status"] and (
                    "paragraph_id" not in want or (fp["paragraph_id"] and same_paragraph(fp["paragraph_id"], want["paragraph_id"])))
                first_ok &= bool(ok)
        row["first_pass_correct"] = first_ok
    else:  # lookup
        answer = out.answer.lower().replace(",", "")
        for group in exp["answer_contains"]:
            if not any(alt.lower().replace(",", "") in answer for alt in group):
                problems.append(f"answer lacks any of {group}")
        if exp["cite"]:
            row["expected_cites"] = 1
            row["cited"] = int(any(same_paragraph(c, e) for c in out.citations for e in exp["cite"]))
            if not row["cited"]:
                problems.append(f"no citation of {exp['cite']}")
    row["correct"] = not problems
    row["detail"] = "; ".join(problems)
    return row


def label_failure(q: dict, out, row: dict) -> tuple[str, str]:
    """Automatic first label for a wrong result, following docs/labelling_guide.md. A person can overwrite it
    in eval/failure_labels.jsonl."""
    exp = q["expected"]
    note = ""
    wants = [w["paragraph_id"] for w in exp["checks"].values() if "paragraph_id" in w] or exp["cite"]
    lender = (q["scenario"] or {}).get("lender_type")
    if out.wrong_rulebook_caught or any(source_lender_type(c) not in (None, lender) for c in out.citations if lender):
        note = "wrong_rulebook"
    if out.stopped or "tool error" in row["detail"]:
        return "tool call error", note
    if exp["refuse"] or out.refused:
        return "reasoning error", note
    if wants and out.retrieved and not any(same_paragraph(r, w) for r in out.retrieved for w in wants):
        return "retrieval miss", note
    if "could not verify" in out.answer or any(k in ("ungrounded_citation", "wrong_number") for k in out.critic_kinds) or \
            any(not any(same_paragraph(c, r) for r in out.retrieved) for c in out.citations if out.retrieved and not exp["checks"]):
        return "hallucination", note
    return "reasoning error", note


@activity.not_logged()
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--strategies", nargs="+", default=["A", "B", "C"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    gold = load_gold(args.split)[: args.limit or None]
    index, store = get_index(), Store()
    trace_path = ROOT / "results" / f"traces_{args.split}.jsonl"
    summary = {"split": args.split, "queries": len(gold), "model": config.LLM_MODEL, "corpus_version": index.corpus_version, "strategies": {}}
    labels = []
    for strat in args.strategies:
        llm = LLM(use_cache=False)   # every call is real, so tokens and timings are true
        rows, started = [], time.time()
        for q in gold:
            case, screen = build_inputs(q, store)
            out = loop.run(q["question"], case=case, application_id=q["application_id"], screen=screen, strategy=strat,
                           llm=llm, index=index, user="eval", draft=False, trace_path=trace_path)
            row = score(q, out)
            row["strategy"] = strat
            if not row["correct"]:
                row["category"], row["note"] = label_failure(q, out, row)
                labels.append({k: row[k] for k in ("id", "strategy", "type", "trace_id", "category", "note", "detail")})
            rows.append(row)
            print(f"{strat} {q['id']} {q['type']:14s} {'ok ' if row['correct'] else 'BAD'} steps={row['steps']} critic={row['critic_failures']} {row['detail'][:110]}", flush=True)
        (ROOT / "results" / f"{args.split}_{strat}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        n = len(rows)
        fp = [r for r in rows if r["first_pass_correct"] is not None]
        cats = Counter(r["category"] for r in rows if not r["correct"])
        by_type = defaultdict(lambda: [0, 0])
        for r in rows:
            by_type[r["type"]][0] += r["correct"]
            by_type[r["type"]][1] += 1
        summary["strategies"][strat] = {
            "accuracy": round(sum(r["correct"] for r in rows) / n, 3),
            "first_pass_accuracy": round(sum(r["first_pass_correct"] for r in fp) / max(len(fp), 1), 3),
            "first_pass_queries": len(fp),
            "citation_rate": round(sum(r["cited"] for r in rows) / max(sum(r["expected_cites"] for r in rows), 1), 3),
            "queries_with_critic_failure": sum(1 for r in rows if r["critic_failures"]),
            "critic_kinds": dict(Counter(k for r in rows for k in r["critic_kinds"])),
            "failures": {c: cats.get(c, 0) for c in CATEGORIES},
            "wrong_rulebook_notes": sum(1 for r in rows if not r["correct"] and r.get("note") == "wrong_rulebook"),
            "by_type": {t: f"{c}/{k}" for t, (c, k) in by_type.items()},
            "failed": [{"id": r["id"], "trace_id": r["trace_id"], "category": r["category"], "detail": r["detail"]} for r in rows if not r["correct"]],
            "avg_tokens_per_query": round(sum(r["tokens"] for r in rows) / n), "stopped": dict(Counter(r["stopped"] for r in rows if r["stopped"])),
            "model_calls": llm.calls, "cache_hits": llm.cache_hits, "validation_retries": llm.validation_retries,
            "seconds": round(time.time() - started),
        }
    (ROOT / "results" / f"summary_{args.split}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (ROOT / f"failure_labels_{args.split}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in labels), encoding="utf-8")
    print("\nstrategy  accuracy  first-pass  citation  " + "  ".join(CATEGORIES))
    for s, r in summary["strategies"].items():
        print(f"{s:8s}  {r['accuracy']:<8}  {r['first_pass_accuracy']:<10}  {r['citation_rate']:<8}  " +
              "  ".join(f"{r['failures'][c]:<{len(c)}}" for c in CATEGORIES))
        print("          by type:", r["by_type"])
        for f in r["failed"]:
            print(f"          {f['id']} trace {f['trace_id']} [{f['category']}] {f['detail'][:100]}")


if __name__ == "__main__":
    main()
