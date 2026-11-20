"""OpenAI-compatible chat client with schema-validated JSON output, retries, a per-question budget and a disk cache.

Works with a local Ollama model or the course endpoint: base URL, key and model come from the environment.
JSON-schema guided output is tried first; if the endpoint rejects it the client falls back to plain JSON
and relies on Pydantic validation with retries.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src import config
from src.tracing import activity

T = TypeVar("T", bound=BaseModel)
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class ToolCallError(Exception):
    """A reply failed schema validation after all retries, or the endpoint call failed."""


class BudgetExceeded(Exception):
    """The per-question token budget or time limit ran out."""


class Budget:
    def __init__(self, tokens: int = config.QUERY_TOKEN_BUDGET, seconds: float = config.QUERY_TIME_LIMIT_S) -> None:
        self.tokens_left = tokens
        self.deadline = time.time() + seconds
        self.reason = ""

    def check(self) -> None:
        if self.tokens_left <= 0:
            self.reason = "token_budget"
            raise BudgetExceeded("token budget for this question is used up")
        if time.time() > self.deadline:
            self.reason = "time_limit"
            raise BudgetExceeded("time limit for this question reached")

    def charge(self, tokens: int) -> None:
        self.tokens_left -= tokens


def _extract_json(text: str) -> str:
    text = THINK_RE.sub("", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


_ENDPOINT_SLOTS = threading.BoundedSemaphore(config.LLM_MAX_CONCURRENT)   # one process-wide queue for every LLM in the app


class RateWindow:
    """Allow at most `limit` calls in any 60 seconds; wait() blocks until the next call is allowed (limit 0 = off)."""

    def __init__(self, limit: int, clock=time.monotonic, sleep=time.sleep) -> None:
        self.limit, self.clock, self.sleep = limit, clock, sleep
        self.times: list[float] = []
        self.lock = threading.Lock()

    def wait(self) -> float:
        """Reserve a slot, sleeping first if the last minute is full. Returns the seconds waited."""
        if self.limit <= 0:
            return 0.0
        with self.lock:
            now = self.clock()
            self.times = [t for t in self.times if now - t < 60]
            delay = 0.0 if len(self.times) < self.limit else 60 - (now - self.times[0]) + 0.05
            if delay > 0:
                activity.emit("llm", f"Request limit reached; waiting {delay:.0f} s", status="wait")
                self.sleep(delay)
                now = self.clock()
                self.times = [t for t in self.times if now - t < 60]
            self.times.append(now)
            return delay


_RATE = RateWindow(config.LLM_RATE_PER_MINUTE)
_TOO_LARGE = ("context length", "context_length", "maximum context", "too many tokens", "max_tokens", "too long", "token limit", "8192")


class LLM:
    def __init__(self, base_url: str = config.LLM_BASE_URL, api_key: str = config.LLM_API_KEY, model: str = config.LLM_MODEL,
                 cache_dir: Optional[Path] = None, use_cache: Optional[bool] = None) -> None:
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.mode = "json_schema" if config.LLM_JSON_MODE == "auto" else config.LLM_JSON_MODE
        self.reasoning_effort = config.LLM_REASONING_EFFORT
        self.cache_dir = Path(cache_dir) if cache_dir else config.CACHE_DIR / "llm"
        self.use_cache = (os.environ.get("LLM_CACHE", "1") == "1") if use_cache is None else use_cache
        self.calls = 0          # real endpoint calls
        self.cache_hits = 0
        self.validation_retries = 0
        self._client = None

    # -- endpoint -------------------------------------------------------------------------------
    def _raw(self, messages: list[dict], schema: dict, max_tokens: int) -> tuple[str, int, int]:
        """One endpoint call. Subclasses used in tests override this."""
        if self._client is None:
            from openai import OpenAI

            from src.tls import http_client

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=config.LLM_TIMEOUT_S, max_retries=0,
                                  http_client=http_client(config.LLM_CA_CERT))
        max_tokens = min(max_tokens, config.LLM_MAX_OUTPUT_TOKENS)
        waits = list(config.LLM_BACKOFF_S)
        while True:
            kwargs: dict[str, Any] = dict(model=self.model, messages=messages, temperature=0, max_tokens=max_tokens)
            if self.mode == "json_schema":
                kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}}
            elif self.mode == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            if self.reasoning_effort:
                kwargs["reasoning_effort"] = self.reasoning_effort
            try:
                with _ENDPOINT_SLOTS:   # queue here instead of earning a 429 from the endpoint
                    _RATE.wait()
                    resp = self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # fall back one feature at a time, then give up
                msg = str(exc).lower()
                if getattr(exc, "status_code", None) == 400 and any(w in msg for w in _TOO_LARGE):
                    raise ToolCallError(f"the request is too large for the endpoint (prompt plus answer must fit its token limit): {exc}") from exc
                if getattr(exc, "status_code", None) in (429, 503) and waits:
                    wait = waits.pop(0)   # busy endpoint: wait 2, 4, then 8 seconds, then report the failure
                    activity.emit("llm", f"Model endpoint busy; retrying in {wait:g} s", status="retry")
                    time.sleep(wait)
                elif self.reasoning_effort and "reasoning" in msg:
                    self.reasoning_effort = ""
                elif self.mode == "json_schema" and any(w in msg for w in ("response_format", "json_schema", "schema", "400")):
                    self.mode = "json_object"
                elif self.mode == "json_object" and any(w in msg for w in ("response_format", "json_object", "400")):
                    self.mode = "none"
                else:
                    raise ToolCallError(f"endpoint call failed: {exc}") from exc
        usage = resp.usage
        content = resp.choices[0].message.content or ""
        p = usage.prompt_tokens if usage else sum(len(m["content"]) for m in messages) // 4
        c = usage.completion_tokens if usage else len(content) // 4
        return content, p, c

    # -- public ---------------------------------------------------------------------------------
    def chat_json(self, system: str, user: str, schema_model: Type[T], *, budget: Optional[Budget] = None, trace=None,
                  max_tokens: int = 700) -> T:
        """Return a validated instance of schema_model. Retries with the validation error, then raises ToolCallError."""
        schema = schema_model.model_json_schema()
        system_full = f"{system}\nReply with one JSON object only, matching this JSON schema:\n{json.dumps(schema)}"
        messages = [{"role": "system", "content": system_full}, {"role": "user", "content": user}]
        key = hashlib.sha256(json.dumps([self.model, messages], sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        what = activity.LLM_LABEL.get(schema_model.__name__, schema_model.__name__)
        if self.use_cache and cache_file.exists():
            try:
                self.cache_hits += 1
                obj = schema_model.model_validate_json(cache_file.read_text(encoding="utf-8"))
                activity.emit("llm", f"Model reply served from cache: {what}", schema=schema_model.__name__, model=self.model, status="cache hit")
                return obj
            except ValidationError:
                pass
        last_error = ""
        for attempt in range(config.LLM_MAX_RETRIES + 1):
            if budget:
                budget.check()
            started = time.time()
            label = f"Model thinking: {what}" + (f" (retry {attempt})" if attempt else "")
            with activity.span("llm", label, schema=schema_model.__name__, model=self.model, endpoint=self.base_url,
                               attempt=attempt + 1, prompt_chars=sum(len(m["content"]) for m in messages)) as res:
                content, p, c = self._raw(messages, schema, max_tokens)
                res.update(tokens=p + c, prompt_tokens=p, completion_tokens=c,
                           status="ok" if _valid(schema_model, content) else "invalid JSON, will retry")
            self.calls += 1
            if budget:
                budget.charge(p + c)
            if trace is not None:
                trace.add_tokens(p, c)
                trace.record.setdefault("model_calls", []).append(
                    {"schema": schema_model.__name__, "attempt": attempt + 1, "latency_ms": int((time.time() - started) * 1000),
                     "prompt_tokens": p, "completion_tokens": c})
            try:
                obj = schema_model.model_validate_json(_extract_json(content))
                if self.use_cache:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(obj.model_dump_json(), encoding="utf-8")
                return obj
            except ValidationError as exc:
                self.validation_retries += 1
                last_error = str(exc)[:400]
                messages = messages[:2] + [
                    {"role": "assistant", "content": content[:1500]},
                    {"role": "user", "content": f"That reply failed validation: {last_error}\nSend the corrected JSON object only."}]
        raise ToolCallError(f"reply failed schema validation after {config.LLM_MAX_RETRIES} retries: {last_error}")


def _valid(schema_model, content: str) -> bool:
    try:
        schema_model.model_validate_json(_extract_json(content))
        return True
    except (ValidationError, ValueError):
        return False


class FakeLLM(LLM):
    """Test double. `responder(schema_name, system, user)` returns a dict or JSON string."""

    def __init__(self, responder, **kw) -> None:
        super().__init__(use_cache=kw.pop("use_cache", False), **kw)
        self.responder = responder
        self.prompts: list[tuple[str, str, str]] = []

    def _raw(self, messages, schema, max_tokens):
        name = schema.get("title", "")
        self.prompts.append((name, messages[0]["content"], messages[-1]["content"] if len(messages) == 2 else messages[1]["content"]))
        out = self.responder(name, messages[0]["content"], messages[1]["content"])
        return (out if isinstance(out, str) else json.dumps(out)), 50, 20
