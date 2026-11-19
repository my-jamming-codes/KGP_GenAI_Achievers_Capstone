"""Chunking experiments: recall@k for four chunking schemes x four retrieval modes, on the dev and test queries.

Usage: python -m eval.chunking_eval            (embeds each new scheme once, about 15-25 minutes per scheme on a laptop CPU)
       python -m eval.chunking_eval --limit-docs 50   (a quick smoke run on a small corpus)

Schemes, all over the same 1,080 RBI documents:
  paragraph_220   paragraph-aware: one chunk per numbered RBI paragraph, split at 220 words (the production index)
  paragraph_120   paragraph-aware, split at 120 words
  fixed_200       fixed 200-word windows that ignore paragraph boundaries, no overlap
  fixed_400_o80   fixed 400-word windows with an 80-word overlap

Each scheme gets its own BM25 index and its own bge-small embeddings; search uses the same fusion and reranker as
production (src/index/hybrid.py), with exact cosine search in numpy so every scheme is compared the same way.
A fixed window can span several RBI paragraphs; it counts as a hit when it contains text of the expected paragraph.
Choose a scheme on dev; the test column is a check, not a second chance to choose.
Writes eval/results/chunking.json.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from eval.retrieval_eval import pairs
from src import config
from src.index.hybrid import LENDER_FILTER, _reranker, embed_texts, load_chunks, same_paragraph, tokenize
from src.tracing import activity

SCHEMES = ("paragraph_220", "paragraph_120", "fixed_200", "fixed_400_o80")
MODES = ("dense", "bm25", "hybrid", "hybrid_rerank")
KS = (1, 3, 5, 10)
EXP_DIR = config.INDEX_DIR / "experiments"


# ------------------------------------------------------------------ building the schemes
def documents(chunks: list[dict]) -> list[dict]:
    """Rebuild each document as a word sequence tagged with the paragraph each word belongs to."""
    docs: dict[str, dict] = {}
    for c in chunks:
        d = docs.setdefault(c["doc_key"] + "|" + c["title"], {"doc_key": c["doc_key"], "title": c["title"], "lender_type": c["lender_type"],
                                                             "words": [], "paras": []})
        w = c["text"].split()
        d["words"] += w
        d["paras"] += [c["paragraph_id"]] * len(w)
    return list(docs.values())


def paragraph_scheme(chunks: list[dict], max_words: int) -> list[dict]:
    if max_words == 220:
        return [{**c, "covers": [c["paragraph_id"]]} for c in chunks]   # the production chunks, unchanged
    out = []
    by_para: dict[tuple, list[str]] = defaultdict(list)
    order, meta = [], {}
    for c in chunks:
        key = (c["doc_key"], c["title"], c["paragraph_id"])
        if key not in by_para:
            order.append(key)
            meta[key] = c
        by_para[key] += c["text"].split()
    for key in order:
        words, c = by_para[key], meta[key]
        for i in range(0, max(len(words), 1), max_words):
            out.append({"paragraph_id": c["paragraph_id"], "doc_key": c["doc_key"], "title": c["title"], "lender_type": c["lender_type"],
                        "text": " ".join(words[i:i + max_words]), "covers": [c["paragraph_id"]]})
    return out


def fixed_scheme(chunks: list[dict], size: int, overlap: int) -> list[dict]:
    out = []
    step = size - overlap
    for d in documents(chunks):
        n = len(d["words"])
        for i in range(0, max(n, 1), step):
            span = d["paras"][i:i + size]
            covers = list(dict.fromkeys(span))
            out.append({"paragraph_id": covers[0] if covers else d["doc_key"], "doc_key": d["doc_key"], "title": d["title"],
                        "lender_type": d["lender_type"], "text": " ".join(d["words"][i:i + size]), "covers": covers})
            if i + size >= n:
                break
    return out


def build_scheme(name: str, chunks: list[dict]) -> list[dict]:
    return {"paragraph_220": lambda: paragraph_scheme(chunks, 220), "paragraph_120": lambda: paragraph_scheme(chunks, 120),
            "fixed_200": lambda: fixed_scheme(chunks, 200, 0), "fixed_400_o80": lambda: fixed_scheme(chunks, 400, 80)}[name]()


def embeddings(name: str, scheme: list[dict], tag: str) -> np.ndarray:
    """Embed once per scheme, resumably, in blocks of 2,000; the production scheme reuses the production vectors."""
    if name == "paragraph_220" and tag == "full":
        return np.load(config.INDEX_DIR / "embeddings.npy")
    folder = EXP_DIR / f"{name}_{tag}"
    folder.mkdir(parents=True, exist_ok=True)
    texts = [f"{c['title'][:80]}\n{c['text']}"[:1000] for c in scheme]   # same text recipe as production
    parts = []
    for i in range(0, len(texts), 2000):
        part = folder / f"{i:06d}.npy"
        if not part.exists():
            np.save(part, embed_texts(texts[i:i + 2000]))
            print(f"  {name}: embedded {min(i + 2000, len(texts))}/{len(texts)}", flush=True)
        parts.append(np.load(part))
    return np.vstack(parts)


# ------------------------------------------------------------------ searching a scheme
class SchemeIndex:
    def __init__(self, scheme: list[dict], vectors: np.ndarray) -> None:
        from rank_bm25 import BM25Okapi

        self.chunks = scheme
        self.vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in scheme])
        self.lender = np.array([c["lender_type"] for c in scheme])

    def _mask(self, lender_type):
        allowed = LENDER_FILTER.get(lender_type or "")
        return np.isin(self.lender, sorted(allowed)) if allowed else np.ones(len(self.chunks), bool)

    def ranked(self, query: str, lender_type, mode: str, qvec: np.ndarray, n: int = 10) -> list[int]:
        mask = self._mask(lender_type)
        dense = np.where(mask, self.vectors @ qvec, -2.0)
        sparse = np.where(mask, self.bm25.get_scores(tokenize(query)), -1.0)
        if mode == "dense":
            return list(np.argsort(-dense)[:n])
        if mode == "bm25":
            return [int(i) for i in np.argsort(-sparse)[:n] if sparse[i] > 0]
        fused: dict[int, float] = {}
        for order in (np.argsort(-dense)[:40], [i for i in np.argsort(-sparse)[:40] if sparse[i] > 0]):
            for rank, idx in enumerate(order):
                fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (60 + rank)
        top = [i for i, _ in sorted(fused.items(), key=lambda kv: -kv[1])[:20]]
        if mode == "hybrid":
            return top[:n]
        scores = list(_reranker().rerank(query, [self.chunks[i]["text"][:1200] for i in top]))
        return [i for i, _ in sorted(zip(top, scores), key=lambda kv: -kv[1])][:n]


def score(index: SchemeIndex, items, qvecs, mode: str) -> dict:
    hits = {k: 0 for k in KS}
    rr = 0.0
    for (query, lender, wants), qv in zip(items, qvecs):
        ranked = index.ranked(query, lender, mode, qv, n=max(KS))
        first = next((r for r, i in enumerate(ranked) if any(same_paragraph(c, w) for c in index.chunks[i]["covers"] for w in wants)), None)
        for k in KS:
            hits[k] += first is not None and first < k
        rr += 0.0 if first is None else 1.0 / (first + 1)
    n = len(items)
    return {**{f"recall@{k}": round(hits[k] / n, 3) for k in KS}, "mrr@10": round(rr / n, 3)}


@activity.not_logged()
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-docs", type=int, default=0, help="smoke run on the first N documents plus every document a query expects")
    ap.add_argument("--schemes", nargs="+", default=list(SCHEMES))
    args = ap.parse_args()
    chunks = load_chunks()
    sets = {"dev": pairs("dev"), "test": pairs("test")}
    tag = "full"
    if args.limit_docs:
        wanted = {w.split("/")[0] for items in sets.values() for _, _, ws in items for w in ws}
        keys = list(dict.fromkeys(c["doc_key"] for c in chunks))[: args.limit_docs]
        keep = set(keys) | wanted
        chunks = [c for c in chunks if c["doc_key"] in keep]
        tag = f"limit{args.limit_docs}"
    qvecs = {s: [v / np.linalg.norm(v) for v in embed_texts([q for q, _, _ in items])] for s, items in sets.items()}
    report = {"documents": len({c["doc_key"] + c["title"] for c in chunks}), "queries": {s: len(v) for s, v in sets.items()},
              "embed_model": config.EMBED_MODEL, "rerank_model": config.RERANK_MODEL, "tag": tag, "schemes": {}}
    for name in args.schemes:
        started = time.time()
        scheme = build_scheme(name, chunks)
        vectors = embeddings(name, scheme, tag)
        index = SchemeIndex(scheme, vectors)
        words = [len(c["text"].split()) for c in scheme]
        entry = {"chunks": len(scheme), "mean_words": round(float(np.mean(words)), 1), "max_words": int(max(words)), "results": {}}
        for split, items in sets.items():
            entry["results"][split] = {mode: score(index, items, qvecs[split], mode) for mode in MODES}
        entry["seconds"] = round(time.time() - started)
        report["schemes"][name] = entry
        print(f"{name:14s} chunks={len(scheme):6d} mean_words={entry['mean_words']:6.1f}  dev hybrid_rerank "
              f"r@5={entry['results']['dev']['hybrid_rerank']['recall@5']}  test r@5={entry['results']['test']['hybrid_rerank']['recall@5']}", flush=True)
        out = Path(__file__).resolve().parent / "results" / ("chunking.json" if tag == "full" else f"chunking_{tag}.json")
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\nscheme          split mode            r@1    r@3    r@5    r@10   mrr@10")
    for name, e in report["schemes"].items():
        for split in ("dev", "test"):
            for mode in MODES:
                r = e["results"][split][mode]
                print(f"{name:15s} {split:5s} {mode:14s}  " + "  ".join(f"{r[k]:.3f}" for k in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr@10")))


if __name__ == "__main__":
    main()
