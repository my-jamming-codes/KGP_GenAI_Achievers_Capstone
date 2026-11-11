"""Ingest and query orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ragkit.chunking import Chunk, chunk_path
from ragkit.client import InferenceClient
from ragkit.config import Settings
from ragkit.store import VectorStore

SYSTEM_PROMPT = (
    "You answer questions using only the numbered context passages provided. "
    "Cite the passages you rely on like [1] or [2]. If the context does not "
    "contain the answer, say you don't know rather than guessing."
)


@dataclass(frozen=True)
class Answer:
    text: str
    sources: list[tuple[Chunk, float]]


def build_messages(question: str, hits: list[tuple[Chunk, float]]) -> list[dict[str, str]]:
    context = "\n\n".join(
        f"[{i}] (source: {chunk.source})\n{chunk.text}" for i, (chunk, _) in enumerate(hits, 1)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


class RagPipeline:
    def __init__(
        self, settings: Settings, client: InferenceClient, store: VectorStore | None = None
    ) -> None:
        self.settings = settings
        self.client = client
        self.store = store or VectorStore()

    def ingest(self, path: Path) -> int:
        chunks = chunk_path(path, self.settings.chunk_size, self.settings.chunk_overlap)
        if not chunks:
            return 0
        self.store.add(chunks, self.client.embed([c.text for c in chunks]))
        return len(chunks)

    def retrieve(self, question: str, k: int | None = None) -> list[tuple[Chunk, float]]:
        [query_vec] = self.client.embed([question])
        return self.store.search(query_vec, k or self.settings.top_k)

    def ask(self, question: str, k: int | None = None) -> Answer:
        hits = self.retrieve(question, k)
        if not hits:
            return Answer("The index is empty. Ingest some documents first.", [])
        return Answer(self.client.chat(build_messages(question, hits)), hits)
