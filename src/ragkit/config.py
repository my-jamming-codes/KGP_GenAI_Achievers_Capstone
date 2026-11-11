"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Tiny .env reader: KEY=VALUE lines, existing environment wins."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    chat_model: str
    embed_model: str
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 4
    index_dir: Path = Path(".rag_index")
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        missing = [
            k
            for k in ("RAG_BASE_URL", "RAG_CHAT_MODEL", "RAG_EMBED_MODEL")
            if not os.environ.get(k)
        ]
        if missing:
            raise RuntimeError(
                f"Missing required settings: {', '.join(missing)}. "
                "Copy .env.example to .env and fill it in."
            )
        env = os.environ
        return cls(
            base_url=env["RAG_BASE_URL"].rstrip("/"),
            api_key=env.get("RAG_API_KEY", ""),
            chat_model=env["RAG_CHAT_MODEL"],
            embed_model=env["RAG_EMBED_MODEL"],
            chunk_size=int(env.get("RAG_CHUNK_SIZE", 800)),
            chunk_overlap=int(env.get("RAG_CHUNK_OVERLAP", 120)),
            top_k=int(env.get("RAG_TOP_K", 4)),
            index_dir=Path(env.get("RAG_INDEX_DIR", ".rag_index")),
            timeout=float(env.get("RAG_TIMEOUT", 60)),
        )
