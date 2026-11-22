"""Hybrid retrieval: BM25 + dense vectors in Qdrant, fused with reciprocal rank fusion, then a cross-encoder reranker.

Build:  python -m src.index.hybrid build     (embeds every chunk once and saves the snapshot in data/index)
Search: get_index().search("query", lender_type="hfc", k=5, mode="hybrid_rerank")
"""
from __future__ import annotations

import json
import pickle
import re
import sys
from functools import lru_cache
from typing import Optional

import numpy as np

from src import config
from src.tracing import activity
from src.schemas import RetrievedParagraph

MODES = ("dense", "bm25", "hybrid", "hybrid_rerank")
LENDER_FILTER = {"hfc": {"hfc", "nbfc"}, "bank": {"bank"}}
TOKEN_RE = re.compile(r"[a-z0-9₹%]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def load_chunks() -> list[dict]:
    path = config.INDEX_DIR / "chunks.jsonl"
    if not path.exists():
        path = config.PARSED_DIR / "chunks.jsonl"
    return [json.loads(line) for line in path.open(encoding="utf-8")]


class EmbeddingError(RuntimeError):
    """The embedding endpoint could not be reached or returned something unusable."""


def embed_texts(texts: list[str]) -> np.ndarray:
    """Vectors for texts, from the configured provider (EMBED_PROVIDER)."""
    if config.EMBED_PROVIDER == "ollama":
        return _embed_endpoint(texts)
    model = _embedder()
    return np.asarray(list(model.embed(texts, batch_size=32, parallel=0 if len(texts) > 500 else None)), dtype=np.float32)


def _too_long(exc: Exception) -> Optional[int]:
    """If the endpoint refused an input for passing its token limit, return the token count it reports (or 0 when unstated)."""
    msg = str(exc)
    if "tokens" not in msg or not any(w in msg for w in ("less than", "maximum", "too long", "exceed")):
        return None
    m = re.search(r"Given:\s*(\d+)", msg)
    return int(m.group(1)) if m else 0


def _embed_fitting(client, text: str) -> list[float]:
    """Embed one text, shortening it until the endpoint accepts it. Only used after a batch was refused as too long."""
    for _ in range(8):
        try:
            return client.embeddings.create(model=config.EMBED_MODEL, input=[text]).data[0].embedding
        except Exception as exc:
            given = _too_long(exc)
            if given is None:
                raise
            keep = (config.EMBED_MAX_TOKENS / given * 0.95) if given else 0.8
            text = text[: max(50, int(len(text) * min(keep, 0.9)))]
    raise EmbeddingError("a text could not be shortened enough for the embedding endpoint")


def _embed_endpoint(texts: list[str]) -> np.ndarray:
    client = _embed_client()
    out: list[list[float]] = []
    for i in range(0, len(texts), config.EMBED_BATCH):
        batch = texts[i:i + config.EMBED_BATCH]
        try:
            resp = client.embeddings.create(model=config.EMBED_MODEL, input=batch)
            vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        except Exception as exc:
            if _too_long(exc) is None:
                raise EmbeddingError(f"embedding call to {config.EMBED_BASE_URL} ({config.EMBED_MODEL}) failed: {exc}") from exc
            try:   # one text in the batch is over the endpoint's token limit: send them one at a time and shorten that one
                vectors = [_embed_fitting(client, t) for t in batch]
            except Exception as inner:
                raise EmbeddingError(f"embedding call to {config.EMBED_BASE_URL} ({config.EMBED_MODEL}) failed: {inner}") from inner
        if len(vectors) != len(batch):
            raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(vectors)}")
        out.extend(vectors)
    return np.asarray(out, dtype=np.float32)


@lru_cache(maxsize=1)
def _embed_client():
    from openai import OpenAI

    from src.tls import http_client

    return OpenAI(base_url=config.EMBED_BASE_URL, api_key=config.EMBED_API_KEY, timeout=120, max_retries=2,
                  http_client=http_client(config.EMBED_CA_CERT))


@lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(config.EMBED_MODEL)


def embed_signature() -> str:
    """Which embedding model made the vectors; an index is only searched with the same one."""
    return f"{config.EMBED_PROVIDER}:{config.EMBED_MODEL}"


@lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(config.RERANK_MODEL)


def _embed_text(c: dict) -> str:
    return f"{c['title'][:80]}\n{c['text']}"[:1000]


def build() -> None:
    """Compute embeddings and save chunks, vectors and BM25 as the index snapshot.
    Vectors from the previous snapshot are reused for chunks whose text is unchanged, so a re-parse that adds
    a few PDFs only embeds the new and changed chunks."""
    import hashlib

    from rank_bm25 import BM25Okapi

    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((config.PARSED_DIR / "corpus_manifest.json").read_text(encoding="utf-8"))
    emb_path = config.INDEX_DIR / "embeddings.npy"
    meta_path = config.INDEX_DIR / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    key = lambda t: hashlib.sha1(t.encode("utf-8")).hexdigest()  # noqa: E731
    reuse: dict[str, np.ndarray] = {}
    old_chunks = config.INDEX_DIR / "chunks.jsonl"
    same_model = meta.get("embed_signature", "fastembed:" + meta.get("embed_model", "")) == embed_signature()
    if emb_path.exists() and old_chunks.exists() and same_model:
        old_vectors = np.load(emb_path)
        for c, v in zip((json.loads(line) for line in old_chunks.open(encoding="utf-8")), old_vectors):
            reuse[key(_embed_text(c))] = v
    src = config.PARSED_DIR / "chunks.jsonl"
    chunks = [json.loads(line) for line in src.open(encoding="utf-8")]
    if meta.get("corpus_version") == manifest["corpus_version"] and emb_path.exists() and same_model:
        print("embeddings already cached for this corpus version")
    else:
        texts = [_embed_text(c) for c in chunks]
        todo = [i for i, t in enumerate(texts) if key(t) not in reuse]
        print(f"{len(chunks)} chunks: {len(chunks) - len(todo)} vectors reused, {len(todo)} to embed", flush=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", embed_signature()).strip("-")
        parts_dir = config.INDEX_DIR / f"parts_{manifest['corpus_version']}_{slug}"
        parts_dir.mkdir(exist_ok=True)
        for start in range(0, len(todo), 2000):  # resumable: each block is saved as it finishes
            part = parts_dir / f"{start:06d}.npy"
            if not part.exists():
                np.save(part, embed_texts([texts[i] for i in todo[start:start + 2000]]))
                print(f"embedded {min(start + 2000, len(todo))}/{len(todo)}", flush=True)
            for i, v in zip(todo[start:start + 2000], np.load(part)):
                reuse[key(texts[i])] = v
        np.save(emb_path, np.vstack([reuse[key(t)] for t in texts]).astype(np.float32))
    (config.INDEX_DIR / "chunks.jsonl").write_bytes(src.read_bytes())
    bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])
    with (config.INDEX_DIR / "bm25.pkl").open("wb") as fh:
        pickle.dump(bm25, fh)
    meta_path.write_text(json.dumps({"corpus_version": manifest["corpus_version"], "chunks": len(chunks),
                                     "documents": manifest["documents"], "embed_model": config.EMBED_MODEL,
                                     "embed_provider": config.EMBED_PROVIDER, "embed_signature": embed_signature(),
                                     "by_source_format": manifest.get("by_source_format", {})}))
    print("index built:", len(chunks), "chunks")


class HybridIndex:
    def __init__(self) -> None:
        from qdrant_client import QdrantClient, models

        self.models = models
        self.chunks = load_chunks()
        self.meta = json.loads((config.INDEX_DIR / "meta.json").read_text())
        self.corpus_version = self.meta["corpus_version"]
        built_with = self.meta.get("embed_signature", "fastembed:" + self.meta.get("embed_model", ""))
        if built_with != embed_signature():
            raise EmbeddingError(f"the index was built with {built_with} but the settings say {embed_signature()}; "
                                 "set EMBED_PROVIDER and EMBED_MODEL to match, or rebuild: python -m src.index.hybrid build")
        with (config.INDEX_DIR / "bm25.pkl").open("rb") as fh:
            self.bm25 = pickle.load(fh)
        self.lender = np.array([c["lender_type"] for c in self.chunks])
        self.client = QdrantClient(url=config.QDRANT_URL) if config.QDRANT_URL else QdrantClient(":memory:")
        self.collection = f"{config.QDRANT_COLLECTION}_{self.corpus_version}"
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        m = self.models
        if self.client.collection_exists(self.collection):
            if self.client.count(self.collection).count == len(self.chunks):
                return
            self.client.delete_collection(self.collection)
        vectors = np.load(config.INDEX_DIR / "embeddings.npy")
        self.client.create_collection(self.collection, vectors_config=m.VectorParams(size=vectors.shape[1], distance=m.Distance.COSINE))
        for i in range(0, len(self.chunks), 2000):
            ids = list(range(i, min(i + 2000, len(self.chunks))))
            self.client.upload_collection(
                self.collection, vectors=vectors[ids], ids=ids,
                payload=[{"lender_type": self.chunks[j]["lender_type"], "paragraph_id": self.chunks[j]["paragraph_id"]} for j in ids],
            )

    def _dense(self, query: str, allowed: Optional[set], n: int) -> list[tuple[int, float]]:
        m = self.models
        flt = None
        if allowed:
            flt = m.Filter(must=[m.FieldCondition(key="lender_type", match=m.MatchAny(any=sorted(allowed)))])
        where = "Qdrant server" if config.QDRANT_URL else "Qdrant (in memory)"
        with activity.span("embed", f"Embedding the query ({config.EMBED_SOURCE_LABEL})", model=config.EMBED_MODEL,
                           endpoint=config.EMBED_BASE_URL if config.EMBED_PROVIDER == "ollama" else None) as res:
            vec = embed_texts([query])[0]
            res.update(dims=len(vec))
        with activity.span("vector", f"Vector search in {where}", collection=self.collection, query=activity.safe_query(query),
                           filter=sorted(allowed) if allowed else None, limit=n, embed_model=config.EMBED_MODEL) as res:
            hits = self.client.query_points(self.collection, query=vec.tolist(), query_filter=flt, limit=n).points
            res.update(hits=len(hits), top_score=round(float(hits[0].score), 4) if hits else None)
        return [(int(h.id), float(h.score)) for h in hits]

    def _bm25(self, query: str, allowed: Optional[set], n: int) -> list[tuple[int, float]]:
        with activity.span("bm25", "BM25 keyword search", query=activity.safe_query(query), filter=sorted(allowed) if allowed else None,
                           limit=n, chunks=len(self.chunks)) as res:
            scores = self.bm25.get_scores(tokenize(query))
            if allowed:
                scores = np.where(np.isin(self.lender, sorted(allowed)), scores, -1.0)
            top = np.argsort(-scores)[:n]
            out = [(int(i), float(scores[i])) for i in top if scores[i] > 0]
            res.update(hits=len(out))
        return out

    def search(self, query: str, lender_type: Optional[str] = None, k: int = 5, mode: str = "hybrid_rerank") -> list[RetrievedParagraph]:
        allowed = LENDER_FILTER.get(lender_type or "")
        if mode == "dense":
            ranked = self._dense(query, allowed, k * 3)
        elif mode == "bm25":
            ranked = self._bm25(query, allowed, k * 3)
        else:
            fused: dict[int, float] = {}
            for hits in (self._dense(query, allowed, 40), self._bm25(query, allowed, 40)):
                for rank, (idx, _) in enumerate(hits):
                    fused[idx] = fused.get(idx, 0.0) + 1.0 / (60 + rank)
            ranked = sorted(fused.items(), key=lambda kv: -kv[1])[:20]
            if mode == "hybrid_rerank" and ranked:
                with activity.span("rerank", "Reranking with the cross-encoder", model=config.RERANK_MODEL, candidates=len(ranked)) as res:
                    scores = list(_reranker().rerank(query, [self.chunks[i]["text"][:1200] for i, _ in ranked]))
                    res.update(hits=len(scores))
                ranked = sorted(((i, float(s)) for (i, _), s in zip(ranked, scores)), key=lambda kv: -kv[1])
        out, seen = [], set()
        for idx, score in ranked:
            c = self.chunks[idx]
            if c["chunk_id"] in seen:
                continue
            seen.add(c["chunk_id"])
            out.append(RetrievedParagraph(paragraph_id=c["paragraph_id"], doc_key=c["doc_key"], lender_type=c["lender_type"],
                                          score=round(score, 4), text=c["text"]))
            if len(out) >= k:
                break
        return out


    def fetch_paragraph(self, paragraph_id: str) -> list[RetrievedParagraph]:
        """Targeted fetch of a paragraph by id, used on a critic retry when search missed it."""
        doc, para = base_paragraph(paragraph_id)
        out = []
        for c in self.chunks:
            if c["doc_key"] == doc and base_paragraph(c["paragraph_id"]) == (doc, para):
                out.append(RetrievedParagraph(paragraph_id=c["paragraph_id"], doc_key=c["doc_key"], lender_type=c["lender_type"],
                                              score=1.0, text=c["text"]))
        return out


@lru_cache(maxsize=1)
def get_index() -> HybridIndex:
    return HybridIndex()


def base_paragraph(paragraph_id: str) -> tuple[str, str]:  # noqa: E302
    """('HFC-MD', '58') for 'HFC-MD/58-Note1'. Citations match on document and top-level paragraph."""
    doc, _, para = paragraph_id.partition("/")
    if para.lower().startswith("annex"):
        return doc, para.lower()
    m = re.match(r"\d+[A-Z]{0,2}", para)
    return doc, (m.group(0) if m else para)


def same_paragraph(a: str, b: str) -> bool:
    return base_paragraph(a) == base_paragraph(b)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build()
    else:
        for r in get_index().search(" ".join(sys.argv[1:]) or "loan to value ratio housing loans", k=5):
            print(r.paragraph_id, r.score, r.text[:100].replace("\n", " "))
