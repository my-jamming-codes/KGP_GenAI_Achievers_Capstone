"""In-memory vector store persisted as a .npy matrix plus a JSON sidecar.

Brute-force cosine similarity is fine up to roughly 100k chunks. Past that,
swap this class for FAISS, pgvector, Qdrant, etc. behind the same three methods.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ragkit.chunking import Chunk


class VectorStore:
    def __init__(self) -> None:
        self._vectors = np.zeros((0, 0), dtype=np.float32)
        self._chunks: list[Chunk] = []

    def __len__(self) -> int:
        return len(self._chunks)

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings differ in length")
        if not chunks:
            return
        new = np.asarray(embeddings, dtype=np.float32)
        norms = np.linalg.norm(new, axis=1, keepdims=True)
        new = new / np.where(norms == 0, 1.0, norms)
        if len(self._chunks) and new.shape[1] != self._vectors.shape[1]:
            raise ValueError(
                f"embedding dimension {new.shape[1]} does not match index "
                f"dimension {self._vectors.shape[1]} (did the embedding model change?)"
            )
        self._vectors = new if not len(self._chunks) else np.vstack([self._vectors, new])
        self._chunks.extend(chunks)

    def search(self, query_embedding: list[float], k: int = 4) -> list[tuple[Chunk, float]]:
        if not self._chunks:
            return []
        q = np.asarray(query_embedding, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self._vectors @ q
        k = min(k, len(self._chunks))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(self._chunks[i], float(scores[i])) for i in top]

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self._vectors)
        (directory / "chunks.json").write_text(
            json.dumps([asdict(c) for c in self._chunks], ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path) -> VectorStore:
        store = cls()
        vec_path, chunk_path = directory / "vectors.npy", directory / "chunks.json"
        if not (vec_path.is_file() and chunk_path.is_file()):
            raise FileNotFoundError(f"No index in {directory}. Run `rag ingest <path>` first.")
        store._vectors = np.load(vec_path)
        store._chunks = [Chunk(**c) for c in json.loads(chunk_path.read_text(encoding="utf-8"))]
        return store
