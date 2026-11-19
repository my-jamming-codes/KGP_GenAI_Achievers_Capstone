"""Settings, all from environment variables (see .env.example). Nothing secret lives in the repository."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# OpenAI-compatible chat endpoint. Default is a local Ollama; point these at the course endpoint to switch.
LLM_BASE_URL = _env("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = _env("LLM_API_KEY", "ollama")
LLM_MODEL = _env("LLM_MODEL", "qwen3:8b")
LLM_REASONING_EFFORT = _env("LLM_REASONING_EFFORT", "none")  # "" to omit the parameter for endpoints that reject it
LLM_JSON_MODE = _env("LLM_JSON_MODE", "auto")  # auto | json_schema | json_object | none
LLM_MAX_RETRIES = int(_env("LLM_MAX_RETRIES", "2"))
LLM_TIMEOUT_S = float(_env("LLM_TIMEOUT_S", "120"))

QUERY_TOKEN_BUDGET = int(_env("QUERY_TOKEN_BUDGET", "40000"))  # prompt + completion tokens per question
QUERY_TIME_LIMIT_S = float(_env("QUERY_TIME_LIMIT_S", "240"))
MAX_STEPS = 10
RATE_LIMIT_PER_MINUTE = int(_env("RATE_LIMIT_PER_MINUTE", "6"))

QDRANT_URL = _env("QDRANT_URL", "")  # empty = in-memory Qdrant loaded from the saved embeddings
QDRANT_COLLECTION = _env("QDRANT_COLLECTION", "rbi_paragraphs")
# Embeddings. "ollama" calls an OpenAI-compatible /v1/embeddings endpoint (a local Ollama by default);
# "fastembed" runs the ONNX model in-process. Both serve bge-small-en-v1.5 and give the same vectors.
EMBED_PROVIDER = _env("EMBED_PROVIDER", "ollama")
EMBED_MODEL = _env("EMBED_MODEL", "qllama/bge-small-en-v1.5" if EMBED_PROVIDER == "ollama" else "BAAI/bge-small-en-v1.5")
EMBED_BASE_URL = _env("EMBED_BASE_URL", "http://localhost:11434/v1")
EMBED_API_KEY = _env("EMBED_API_KEY", "ollama")
EMBED_BATCH = int(_env("EMBED_BATCH", "64"))
# How the embedding source is named on screen (the live status and the Activity log); the setting itself is unchanged.
EMBED_SOURCE_LABEL = {"ollama": "model server", "fastembed": "in-process"}.get(EMBED_PROVIDER, EMBED_PROVIDER)
RERANK_MODEL = _env("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")   # cross-encoder, always in-process (fastembed)

DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
HTML_DIR = RAW_DIR / "html"        # RBI web pages, as downloaded
LISTS_DIR = RAW_DIR / "lists"      # the download index and the PDF checklist
PARSED_DIR = DATA / "parsed"       # chunks.jsonl and corpus_manifest.json
TEXT_DIR = PARSED_DIR / "text"     # reference texts the rule quotes are checked against
INDEX_DIR = Path(_env("INDEX_DIR", str(DATA / "index")))
CACHE_DIR = Path(_env("CACHE_DIR", str(DATA / "cache")))
STORE_DIR = Path(_env("STORE_DIR", str(DATA / "store")))
APPLICATIONS_DIR = DATA / "applications"
RULES_FILE = ROOT / "rules" / "rules.yaml"
TRACE_FILE = Path(_env("TRACE_FILE", str(ROOT / "traces" / "traces.jsonl")))
