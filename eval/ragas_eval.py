"""Extra report: Ragas scores on the dev set. It does NOT replace eval/run_eval.py, which scores correctness in code
against the gold answers.

Answers are scored in two groups, because they are built differently. The judge is given what the model's drafting
step was actually given, recorded from the real prompt:

  lookup        a rule question. Contexts: the retrieved paragraphs the drafter read.
                faithfulness, answer relevancy, context precision (without reference)
  application   rule checks on a case. The drafter is given the check results and the cited paragraphs, so faithfulness
                here means "supported by the results or the paragraph text".
                faithfulness, answer relevancy

Agent metrics, for queries whose gold answer lists rule checks (reference = one tool call per listed check,
with the case's lender type as its argument):

  tool call F1, tool call accuracy   compared in code, any order
  agent goal accuracy                model-judged against the gold statuses
  tool recall                        our own figure, not a Ragas metric: share of expected tool calls that were made

On a full application check the gold lists only the rules it grades, while the agent correctly runs more tools, so
F1 and accuracy come out low there. Read them with tool recall.

The judge is the course chat model (Qwen3-8B), so judged scores are noisy: read them as a trend, not as accuracy.
Refusal queries are skipped. Gold is read only through eval.run_eval.

Usage: python -m eval.ragas_eval --split dev --strategy C
       python -m eval.ragas_eval --limit 3          # quick try
Writes eval/results/ragas_<split>_<strategy>.json (per-query scores and averages for each group).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import warnings
from pathlib import Path

from src import config
from src.agents import loop
from src.prompts import strategies as P
from src.app.store import Store
from src.index.hybrid import get_index
from src.llm import LLM
from src.tools.checks import CHECK_TOOL
from src.tls import async_http_client, http_client
from src.tracing import activity

from eval.run_eval import build_inputs, load_gold

warnings.filterwarnings("ignore", category=DeprecationWarning)
ROOT = Path(__file__).resolve().parent
DATA_BLOCK = re.compile(r'<data source="(?:paragraph )?([^"]+)">\n(.*?)\n</data>', re.S)


class RecordingLLM(LLM):
    """The normal client, keeping each prompt so the judge can be given what the drafter was given."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts: list[tuple[str, str]] = []

    def chat_json(self, system, user, schema_model, **kwargs):
        self.prompts.append((system, user))
        return super().chat_json(system, user, schema_model, **kwargs)


def drafter_prompt(prompts: list, system: str) -> str:
    """The user message of the last drafter call (the redraft, if there was one)."""
    return next((u for s, u in reversed(prompts) if s == system), "")


def lookup_contexts(prompts: list) -> list[str]:
    return [f"[{pid}] {text}" for pid, text in DATA_BLOCK.findall(drafter_prompt(prompts, P.DRAFT_LOOKUP_SYSTEM))]


def application_contexts(prompts: list, out) -> list[str]:
    """The check results and cited paragraphs the drafter was given. Without a drafter call (--no-draft), the same lines built from the results."""
    user = drafter_prompt(prompts, P.DRAFT_REPORT_SYSTEM)
    if user:
        listing = user.split("Rule check results:\n", 1)[-1].split("\n\nCited paragraphs:", 1)[0]
        return [line[2:] for line in listing.splitlines() if line.startswith("- ")] + [f"[{pid}] {text}" for pid, text in DATA_BLOCK.findall(user)]
    return [f"{r.check}: {r.status}; paragraph {r.paragraph_id or 'none'}; {r.reason[:260]}" for r in out.results]


def answer_text(out) -> str:
    """The answer to judge. With --no-draft a rule check has no free-text answer, so it is built from the results."""
    if out.answer:
        return out.answer
    lines = []
    for r in out.results:
        lines.append(f"{r.check}: {r.status}" + (f" ({r.paragraph_id})" if r.paragraph_id else "") + (f". {r.reason}" if r.reason else ""))
    return "\n".join(lines)


def tool_calls_made(trace_path: Path, trace_id: str) -> list[tuple[str, dict, str]]:
    """(tool, arguments, result status) for every tool the trace shows, retries counted once."""
    seen: dict[tuple, tuple] = {}
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t.get("trace_id") != trace_id:
            continue
        for step in t["steps"]:
            for entry in step["output"].values() if isinstance(step.get("output"), dict) else []:
                if isinstance(entry, dict) and "tool" in entry and "arguments" in entry:
                    key = (entry["tool"], json.dumps(entry["arguments"], sort_keys=True))
                    seen[key] = (entry["tool"], entry["arguments"], str((entry.get("result") or {}).get("status", "")))
    return list(seen.values())


def expected_tool_calls(query: dict, case) -> list[tuple[str, dict]]:
    return [(CHECK_TOOL[check], {"lender_type": case.lender_type}) for check in query["expected"]["checks"]]


def goal_reference(query: dict) -> str:
    """The expected end result only. Wording about how it is reached ("the checks are run") makes the judge fail correct
    answers, because it compares this text with the outcome it summarises from the conversation."""
    return " ".join(f"The {check.replace('_', ' ')} check is {want['status'].replace('_', ' ')}" +
                    (f" under paragraph {want['paragraph_id']}." if "paragraph_id" in want else ".")
                    for check, want in query["expected"]["checks"].items())


def tool_recall(made: list, expected: list) -> float:
    have = {(n, json.dumps(a, sort_keys=True)) for n, a, *_ in made}
    return round(sum((n, json.dumps(a, sort_keys=True)) in have for n, a in expected) / len(expected), 3)


def judge_models():
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    kw = {"reasoning_effort": config.LLM_REASONING_EFFORT} if config.LLM_REASONING_EFFORT else {}
    chat = ChatOpenAI(model=config.LLM_MODEL, base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, temperature=0,
                      max_tokens=config.LLM_MAX_OUTPUT_TOKENS, max_retries=4, timeout=config.LLM_TIMEOUT_S,
                      http_client=http_client(config.LLM_CA_CERT),
                      http_async_client=async_http_client(config.LLM_CA_CERT), **kw)
    emb = OpenAIEmbeddings(model=config.EMBED_MODEL, base_url=config.EMBED_BASE_URL, api_key=config.EMBED_API_KEY,
                           check_embedding_ctx_length=False, chunk_size=config.EMBED_BATCH, max_retries=4,
                           http_client=http_client(config.EMBED_CA_CERT),
                           http_async_client=async_http_client(config.EMBED_CA_CERT))
    return LangchainLLMWrapper(chat), LangchainEmbeddingsWrapper(emb)


def mean(values: list) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    return round(sum(vals) / len(vals), 3) if vals else None


def group_summary(rows: list[dict], names: list[str]) -> dict:
    return {"queries": len(rows), "average": {n: mean([r.get(n) for r in rows]) for n in names},
            "failed_scores": {n: sum(1 for r in rows if r.get(n) is None) for n in names}}


@activity.not_logged()
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--strategy", default="C", choices=["A", "B", "C"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-draft", action="store_true", help="skip the drafter; rule-check answers are built from the results")
    args = ap.parse_args()

    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.dataset_schema import MultiTurnSample
    from ragas.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
    from ragas.metrics import (AgentGoalAccuracyWithReference, Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy,
                               ToolCallAccuracy, ToolCallF1)

    gold = [q for q in load_gold(args.split) if not q["expected"]["refuse"]][: args.limit or None]
    index, store, llm = get_index(), Store(), RecordingLLM()
    trace_path = ROOT / "results" / f"traces_ragas_{args.split}.jsonl"
    trace_path.unlink(missing_ok=True)
    answers: dict[str, list] = {"lookup": [], "application": []}   # (row, sample)
    agent: list = []   # (row, sample)
    rows, skipped = [], []
    for q in gold:
        case, screen = build_inputs(q, store)
        llm.prompts.clear()
        out = loop.run(q["question"], case=case, application_id=q["application_id"], screen=screen, strategy=args.strategy,
                       llm=llm, index=index, user="eval", draft=not args.no_draft, trace_path=trace_path)
        group = "application" if out.results else "lookup"
        ctx = application_contexts(llm.prompts, out) if out.results else lookup_contexts(llm.prompts)
        ans = answer_text(out)
        print(f"{q['id']} {q['type']:14s} {group:11s} contexts={len(ctx)} answer_chars={len(ans)}", flush=True)
        row = {"id": q["id"], "type": q["type"], "group": group, "question": q["question"], "trace_id": out.trace_id}
        if out.refused or not ctx or not ans:
            skipped.append({**row, "skipped": "refused" if out.refused else "the drafter was given no text (answered in code)"})
        else:
            answers[group].append((row, {"user_input": q["question"], "response": ans, "retrieved_contexts": ctx}))
        if q["expected"]["checks"] and case is not None:
            made, expected = tool_calls_made(trace_path, out.trace_id), expected_tool_calls(q, case)
            messages = [HumanMessage(content=q["question"])]
            if made:
                messages.append(AIMessage(content="Running the rule check tools.", tool_calls=[ToolCall(name=n, args=a) for n, a, _ in made]))
                messages += [ToolMessage(content=f"{n}: {status}") for n, _, status in made]
            messages.append(AIMessage(content=ans or "No answer."))
            row.update(tools_expected=len(expected), tools_called=len(made), tool_recall=tool_recall(made, expected))
            agent.append((row, MultiTurnSample(user_input=messages, reference=goal_reference(q),
                                               reference_tool_calls=[ToolCall(name=n, args=a) for n, a in expected])))
        rows.append(row)

    judge, emb = judge_models()
    # One request at a time and generous timeouts: the course endpoint allows 1 active request per key.
    run = RunConfig(max_workers=1, timeout=300, max_retries=6)

    def score(pairs: list, dataset, metrics: list) -> list[str]:
        table = evaluate(dataset, metrics=metrics, llm=judge, embeddings=emb, run_config=run, show_progress=True).to_pandas()
        names = [m.name for m in metrics]
        for (row, _), (_, s) in zip(pairs, table.iterrows()):
            row.update({n: (None if s[n] != s[n] else round(float(s[n]), 3)) for n in names})
        return names

    groups: dict[str, dict] = {}
    for group, metrics in (("lookup", [Faithfulness(), ResponseRelevancy(), LLMContextPrecisionWithoutReference()]),
                           ("application", [Faithfulness(), ResponseRelevancy()])):
        pairs = answers[group]
        if pairs:
            names = score(pairs, EvaluationDataset.from_list([s for _, s in pairs]), metrics)
            groups[group] = group_summary([r for r, _ in pairs], names)
    if agent:
        names = score(agent, EvaluationDataset(samples=[s for _, s in agent]),
                      [ToolCallF1(), ToolCallAccuracy(strict_order=False), AgentGoalAccuracyWithReference()])
        groups["agent"] = group_summary([r for r, _ in agent], ["tool_recall", *names])

    summary = {"split": args.split, "strategy": args.strategy, "drafted": not args.no_draft, "judge_model": config.LLM_MODEL,
               "queries": len(rows), "skipped": skipped, "groups": groups, "per_query": rows}
    out_path = ROOT / "results" / f"ragas_{args.split}_{args.strategy}{'_nodraft' if args.no_draft else ''}.json"
    out_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("\nRagas averages (judge: %s)" % config.LLM_MODEL)
    for group, g in groups.items():
        print(f"  {group} ({g['queries']} queries)")
        for n, v in g["average"].items():
            print(f"    {n:42s} {v}   (no score on {g['failed_scores'][n]})")
    print("written", out_path)


if __name__ == "__main__":
    main()
