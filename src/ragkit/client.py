"""Thin client for the provided inference API.

This is the only module that knows the wire format. It assumes the
OpenAI-compatible shape (POST /embeddings, POST /chat/completions). If your
API differs, change the two request/response mappings below and nothing else.
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from ragkit.config import Settings

RETRY_STATUS = {429, 500, 502, 503, 504}


class InferenceClient(Protocol):
    """What the pipeline needs. Implement this to plug in any backend."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str: ...


class InferenceError(RuntimeError):
    pass


class HttpInferenceClient:
    def __init__(
        self,
        settings: Settings,
        *,
        embed_batch_size: int = 32,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._s = settings
        self._batch = embed_batch_size
        self._retries = max_retries
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        self._http = httpx.Client(
            base_url=settings.base_url,
            headers=headers,
            timeout=settings.timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, payload: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.post(path, json=payload)
                if resp.status_code in RETRY_STATUS and attempt < self._retries:
                    time.sleep(min(2**attempt, 8))
                    continue
                if resp.is_error:
                    raise InferenceError(f"{path} -> HTTP {resp.status_code}: {resp.text[:500]}")
                return resp.json()
            except httpx.TransportError as exc:
                last = exc
                if attempt < self._retries:
                    time.sleep(min(2**attempt, 8))
        raise InferenceError(f"{path} failed after {self._retries + 1} attempts: {last}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = texts[i : i + self._batch]
            data = self._post("/embeddings", {"model": self._s.embed_model, "input": batch})
            rows = sorted(data["data"], key=lambda r: r.get("index", 0))
            if len(rows) != len(batch):
                raise InferenceError(f"asked for {len(batch)} embeddings, got {len(rows)}")
            out.extend(r["embedding"] for r in rows)
        return out

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        data = self._post(
            "/chat/completions",
            {"model": self._s.chat_model, "messages": messages, "temperature": temperature},
        )
        return data["choices"][0]["message"]["content"]
