"""Extra report: Ragas scores on the dev set. It does NOT replace eval/run_eval.py, which scores correctness in code
against the gold answers. Ragas adds model-judged scores that need no gold answer:

  faithfulness        is every claim in the answer supported by the retrieved paragraphs?
  answer relevancy    does the answer address the question? (uses the embedding endpoint)
  context precision   are the retrieved paragraphs useful for the question? (judged without a reference answer)

The judge is the course chat model (Qwen3-8B), so the scores are noisy: read them as a trend, not as accuracy.
Refusal queries are skipped (there is no answer to judge). Gold is read only through eval.run_eval.

Usage: python -m eval.ragas_eval --split dev --strategy C
       python -m eval.ragas_eval --limit 3          # quick try
Writes eval/results/ragas_<split>_<strategy>.json (per-query scores and averages).
"""
from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path

from src import config
from src.agents import loop
from src.app.store import Store
from src.index.hybrid import get_index
from src.llm import LLM
from src.tls import async_http_client, http_client
from src.tracing import activity

from eval.run_eval import build_inputs, load_gold

warnings.filterwarnings("ignore", category=DeprecationWarning)
ROOT = Path(__file__).resolve().parent
MAX_CONTEXTS = 4          # the course model reads 8,192 tokens in total, so each judge call must stay small
MAX_CONTEXT_CHARS = 700


def contexts_for(index, out) -> list[str]:
    """Text of the paragraphs the agent retrieved, in order, one entry per paragraph."""
    seen, texts = set(), []
    for pid in out.retrieved:
        if pid in seen:
            continue
        seen.add(pid)
        for p in index.fetch_paragraph(pid)[:1]:
            texts.append(f"[{p.paragraph_id}] {p.text}"[:MAX_CONTEXT_CHARS])
        if len(texts) >= MAX_CONTEXTS:
            break
    return texts


def answer_text(out) -> str:
    """The answer to judge. With --no-draft a rule check has no free-text answer, so it is built from the results."""
    if out.answer:
        return out.answer
    lines = []
    for r in out.results:
        why = getattr(r, "reason", "") or getattr(r, "explanation", "") or getattr(r, "note", "")
        lines.append(f"{r.check}: {r.status}" + (f" ({r.paragraph_id})" if r.paragraph_id else "") + (f". {why}" if why else ""))
    return "\n".join(lines)


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


@activity.not_logged()
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--strategy", default="C", choices=["A", "B", "C"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-draft", action="store_true", help="skip the drafter; rule-check answers are built from the results")
    args = ap.parse_args()

    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

    gold = [q for q in load_gold(args.split) if not q["expected"]["refuse"]][: args.limit or None]
    index, store, llm = get_index(), Store(), LLM()
    trace_path = ROOT / "results" / f"traces_ragas_{args.split}.jsonl"
    samples, meta = [], []
    for q in gold:
        case, screen = build_inputs(q, store)
        out = loop.run(q["question"], case=case, application_id=q["application_id"], screen=screen, strategy=args.strategy,
                       llm=llm, index=index, user="eval", draft=not args.no_draft, trace_path=trace_path)
        ctx, ans = contexts_for(index, out), answer_text(out)
        print(f"{q['id']} {q['type']:14s} contexts={len(ctx)} answer_chars={len(ans)}", flush=True)
        if out.refused or not ctx or not ans:
            meta.append({"id": q["id"], "type": q["type"], "question": q["question"], "skipped": "refused" if out.refused else "no contexts or answer"})
            continue
        samples.append({"user_input": q["question"], "response": ans, "retrieved_contexts": ctx})
        meta.append({"id": q["id"], "type": q["type"], "question": q["question"], "trace_id": out.trace_id})

    judge, emb = judge_models()
    metrics = [Faithfulness(), ResponseRelevancy(), LLMContextPrecisionWithoutReference()]
    # One request at a time and generous timeouts: the course endpoint allows 1 active request per key.
    result = evaluate(EvaluationDataset.from_list(samples), metrics=metrics, llm=judge, embeddings=emb,
                      run_config=RunConfig(max_workers=1, timeout=300, max_retries=6), show_progress=True)
    scores = result.to_pandas()
    names = [c for c in scores.columns if c not in ("user_input", "response", "retrieved_contexts", "reference")]

    judged = [m for m in meta if "skipped" not in m]
    rows = []
    for m, (_, s) in zip(judged, scores.iterrows()):
        rows.append({**m, **{n: (None if s[n] != s[n] else round(float(s[n]), 3)) for n in names}})
    summary = {"split": args.split, "strategy": args.strategy, "drafted": not args.no_draft, "judge_model": config.LLM_MODEL, "queries_judged": len(rows),
               "skipped": [m for m in meta if "skipped" in m], "average": {n: mean([r[n] for r in rows]) for n in names},
               "failed_scores": {n: sum(1 for r in rows if r[n] is None) for n in names}, "per_query": rows}
    out_path = ROOT / "results" / f"ragas_{args.split}_{args.strategy}{'_nodraft' if args.no_draft else ''}.json"
    out_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("\nRagas averages (judge: %s, %d queries)" % (config.LLM_MODEL, len(rows)))
    for n in names:
        print(f"  {n:45s} {summary['average'][n]}   (no score on {summary['failed_scores'][n]})")
    print("written", out_path)


if __name__ == "__main__":
    main()
