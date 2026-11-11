# ragkit

Minimal retrieval-augmented generation on top of an inference API you already have.
Two runtime dependencies (`httpx`, `numpy`), no framework.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # add ,pdf for PDF ingestion: ".[dev,pdf]"
cp .env.example .env           # then fill in base URL, key and model names
```

## Use

```bash
rag ingest data/                         # chunk, embed, write .rag_index/
rag ingest more-docs/ --append           # add to the existing index
rag ask "What is the Kafka retention period?"
rag ask "..." -k 6 --show-context        # inspect what was retrieved
```

From Python:

```python
from ragkit import RagPipeline, Settings
from ragkit.client import HttpInferenceClient
from ragkit.store import VectorStore

settings = Settings.from_env()
pipeline = RagPipeline(settings, HttpInferenceClient(settings), VectorStore.load(settings.index_dir))
answer = pipeline.ask("What is the Kafka retention period?")
print(answer.text, [c.source for c, _ in answer.sources])
```

## Layout

| File | Role |
| --- | --- |
| `src/ragkit/client.py` | The only code that knows the API's wire format |
| `src/ragkit/chunking.py` | File loading and paragraph-aware splitting |
| `src/ragkit/store.py` | Cosine-similarity vector store, saved as `.npy` + JSON |
| `src/ragkit/pipeline.py` | Ingest, retrieve, prompt assembly, ask |
| `src/ragkit/cli.py` | `rag` command |
| `tests/` | Offline tests using a fake client and a mocked HTTP transport |

## Adapting to your API

`client.py` assumes the OpenAI-compatible shape:

- `POST {RAG_BASE_URL}/embeddings` with `{"model", "input": [...]}`, returning `data[].embedding`
- `POST {RAG_BASE_URL}/chat/completions` with `{"model", "messages"}`, returning `choices[0].message.content`
- `Authorization: Bearer {RAG_API_KEY}`

If yours differs, edit the payloads and response parsing in `HttpInferenceClient.embed` and
`.chat`. If it has no embeddings endpoint, write a class with the same `embed`/`chat` methods
(see the `InferenceClient` protocol) that embeds locally, for example with
`sentence-transformers`, and delegates `chat` to the API.

## Checks

```bash
pytest
ruff check .
```

## Natural next steps

- Swap `VectorStore` for FAISS, pgvector or Qdrant once the corpus outgrows brute-force search
- Token-based chunk sizes using the model's tokenizer
- Hybrid retrieval (BM25 + vectors) and a reranking step
- Streaming responses and an HTTP service wrapper (FastAPI)
- A small evaluation set of question/expected-source pairs to measure retrieval quality
