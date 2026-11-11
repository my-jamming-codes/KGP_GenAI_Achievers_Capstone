"""Offline tests: a fake client stands in for the inference API."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ragkit.chunking import split_text
from ragkit.client import HttpInferenceClient, InferenceError
from ragkit.config import Settings
from ragkit.pipeline import RagPipeline
from ragkit.store import VectorStore

VOCAB = ["kafka", "retention", "postgres", "replication", "billing", "invoice"]


def bag_of_words(text: str) -> list[float]:
    lowered = text.lower()
    return [float(lowered.count(w)) for w in VOCAB]


class FakeClient:
    def __init__(self) -> None:
        self.last_messages: list[dict[str, str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [bag_of_words(t) for t in texts]

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        self.last_messages = messages
        return "stub answer [1]"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        base_url="http://test/v1",
        api_key="k",
        chat_model="chat",
        embed_model="embed",
        chunk_size=200,
        chunk_overlap=20,
        top_k=1,
        index_dir=tmp_path / "idx",
    )


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "kafka.md").write_text("Kafka retention is seven days. Kafka topics are compacted.")
    (d / "db.md").write_text("Postgres replication is asynchronous with one standby.")
    (d / "billing.txt").write_text("Each invoice is issued by the billing service monthly.")
    return d


def test_split_respects_size_and_covers_text():
    text = "\n\n".join(f"paragraph {i} " + "x" * 90 for i in range(10))
    chunks = split_text(text, size=250, overlap=30)
    assert len(chunks) > 1
    assert all(len(c) <= 250 for c in chunks)
    assert all(f"paragraph {i} " in "".join(chunks) for i in range(10))


def test_split_hard_splits_long_paragraph():
    chunks = split_text("y" * 1000, size=300, overlap=50)
    assert all(len(c) <= 300 for c in chunks)
    assert sum(len(c) for c in chunks) >= 1000


def test_ingest_retrieve_and_ask(settings: Settings, docs: Path):
    client = FakeClient()
    pipeline = RagPipeline(settings, client)
    assert pipeline.ingest(docs) == 3

    answer = pipeline.ask("How does postgres replication work?")
    assert answer.text == "stub answer [1]"
    assert answer.sources[0][0].source.endswith("db.md")
    assert "Postgres replication" in client.last_messages[1]["content"]


def test_store_roundtrip(settings: Settings, docs: Path):
    pipeline = RagPipeline(settings, FakeClient())
    pipeline.ingest(docs)
    pipeline.store.save(settings.index_dir)

    reloaded = RagPipeline(settings, FakeClient(), VectorStore.load(settings.index_dir))
    hits = reloaded.retrieve("kafka retention")
    assert hits[0][0].source.endswith("kafka.md")


def test_empty_index_does_not_call_chat(settings: Settings):
    client = FakeClient()
    answer = RagPipeline(settings, client).ask("anything about billing?")
    assert answer.sources == []
    assert client.last_messages == []


def test_http_client_wire_format_and_retry(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ragkit.client.time.sleep", lambda _s: None)  # skip backoff
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        assert request.headers["authorization"] == "Bearer k"
        if request.url.path == "/v1/embeddings":
            if len(calls) == 1:
                return httpx.Response(503, text="busy")
            rows = [{"index": i, "embedding": [float(i), 1.0]} for i in range(len(body["input"]))]
            return httpx.Response(200, json={"data": list(reversed(rows))})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    client = HttpInferenceClient(settings, transport=httpx.MockTransport(handler))

    assert client.embed(["a", "b"]) == [[0.0, 1.0], [1.0, 1.0]]
    assert client.chat([{"role": "user", "content": "x"}]) == "hi"
    assert calls[-1][1]["model"] == "chat"
    assert [path for path, _ in calls].count("/v1/embeddings") == 2


def test_http_client_surfaces_errors(settings: Settings):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, text="bad key"))
    client = HttpInferenceClient(settings, transport=transport)
    with pytest.raises(InferenceError, match="401"):
        client.chat([{"role": "user", "content": "x"}])
