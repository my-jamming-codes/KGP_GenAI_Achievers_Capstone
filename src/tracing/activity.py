"""Live activity: what the assistant is doing right now, and a log of every model and retrieval call.

Events are emitted by the agent graph (each step starting and ending), the model client (each call to the LLM),
the index (each Qdrant vector query, BM25 scoring and reranking) and the tools. They go to:

- listeners registered for the current run (the workspace shows them live while a question is answered), and
- a JSON-lines log (ACTIVITY_FILE, next to the traces), read by the Activity log page.

Listeners and the run context live in context variables, so two officers' runs never see each other's events.
No prompt text is logged: only what kind of call it was, its size, timing, tokens and result. Search queries are
logged with personal identifiers masked.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from src import config
from src.safety.pii import mask_text

ICON = {"agent": "🤖", "llm": "🧠", "embed": "🧬", "vector": "🔎", "bm25": "📚", "rerank": "↕️", "tool": "🛠️"}
MAX_BYTES = 3_000_000   # the log is trimmed to its newest half past this size

AGENT_LABEL = {
    "guard": "Checking the request: refusals, injected instructions, cache",
    "planner": "Planning which RBI rules to check",
    "executor": "Running the rule tools and searching the RBI corpus",
    "judge": "Model reading the paragraphs and judging each rule",
    "critic": "Critic checking every judgement against the tools",
    "revise": "Re-planning after the critic's feedback",
    "finalize": "Collecting the confirmed results",
    "drafter": "Writing the cited note for the officer",
    "lookup_guard": "Checking whether the rule asked about is verified",
    "search": "Searching the RBI corpus",
    "lookup_draft": "Model drafting a cited answer",
    "lookup_critic": "Critic checking the answer's citations and numbers",
}
LLM_LABEL = {
    "PlannerReply": "planning the checks", "JudgeReply": "judging a rule", "ExtractReply": "extracting the rule words",
    "PlanJudgeReply": "planning, judging and self-checking a rule", "DraftReply": "drafting the answer",
}

_listeners: contextvars.ContextVar[tuple] = contextvars.ContextVar("activity_listeners", default=())
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("activity_context", default={})
_logging: contextvars.ContextVar[bool] = contextvars.ContextVar("activity_logging", default=True)
_lock = threading.Lock()


def log_path() -> Path:
    return Path(getattr(config, "ACTIVITY_FILE", None) or Path(config.TRACE_FILE).with_name("activity.jsonl"))


@contextlib.contextmanager
def listen(callback: Callable[[dict], None]) -> Iterator[None]:
    """Receive every event emitted in this context (this run) until the block ends."""
    token = _listeners.set(_listeners.get() + (callback,))
    try:
        yield
    finally:
        _listeners.reset(token)


@contextlib.contextmanager
def context(**values: Any) -> Iterator[None]:
    """Tag the events emitted inside the block, for example with the run's trace id or the rule being judged."""
    token = _context.set({**_context.get(), **values})
    try:
        yield
    finally:
        _context.reset(token)


@contextlib.contextmanager
def not_logged() -> Iterator[None]:
    """Keep what is emitted inside the block out of the log file. Listeners still hear it, so the live view works.
    The evaluation and safety scripts use it, so their thousands of calls never mix with the officers' activity.
    It also works as a decorator: `@activity.not_logged()`."""
    token = _logging.set(False)
    try:
        yield
    finally:
        _logging.reset(token)


def emit(kind: str, label: str, phase: str = "end", **detail: Any) -> dict:
    event = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "kind": kind, "phase": phase,
             "label": label, **{k: v for k, v in _context.get().items() if v is not None},
             **{k: v for k, v in detail.items() if v is not None}}
    for cb in _listeners.get():
        try:
            cb(event)
        except Exception:   # a display problem must never break a run
            pass
    if phase != "start" and _logging.get():    # the log keeps finished calls; start events are only for the live view
        _append(event)
    return event


@contextlib.contextmanager
def span(kind: str, label: str, **detail: Any) -> Iterator[dict]:
    """Emit a start event, run the block, then an end event with its duration and anything added to `result`."""
    result: dict = {}
    emit(kind, label, "start", **detail)
    started = time.time()
    try:
        yield result
    except Exception as exc:
        emit(kind, label, "end", status="error", error=f"{type(exc).__name__}: {str(exc)[:200]}",
             ms=int((time.time() - started) * 1000), **detail)
        raise
    emit(kind, label, "end", status=result.pop("status", "ok"), ms=int((time.time() - started) * 1000), **detail, **result)


def safe_query(text: str, limit: int = 160) -> str:
    return mask_text(" ".join(str(text).split()))[:limit]


def _append(event: dict) -> None:
    path = log_path()
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            if path.stat().st_size > MAX_BYTES:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                path.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")
    except OSError:
        pass


def read(limit: int = 500, kinds: Optional[set] = None, user: Optional[str] = None) -> list[dict]:
    """Newest first."""
    path = log_path()
    if not path.exists():
        return []
    out = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if kinds and e.get("kind") not in kinds:
            continue
        if user and e.get("user") != user:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def describe(event: dict) -> str:
    """One line for the live view."""
    icon = ICON.get(event["kind"], "•")
    bits = []
    if event.get("ms") is not None:
        bits.append(f"{event['ms'] / 1000:.1f} s")
    if event.get("tokens"):
        bits.append(f"{event['tokens']:,} tokens")
    if event.get("hits") is not None:
        bits.append(f"{event['hits']} hits")
    if event.get("status") not in (None, "ok"):
        bits.append(str(event["status"]))
    return f"{icon} {event['label']}" + (f" — {', '.join(bits)}" if bits and event["phase"] == "end" else " …")


class LiveLines:
    """The lines of the live view as a nested Markdown list: each step is a bullet, and the calls it made are
    indented under it. A run of identical cache hits is folded into one line ("8 \u00d7 Model reply served from cache")."""

    def __init__(self) -> None:
        self.lines: list[dict] = []

    def feed(self, e: dict) -> None:
        if e["phase"] == "start":
            self.lines.append({"kind": e["kind"], "label": e["label"], "text": describe(e), "done": False, "count": 1})
            return
        for line in reversed(self.lines):                      # the end of a call that started earlier
            if not line["done"] and line["kind"] == e["kind"] and line["label"] == e["label"]:
                line["text"], line["done"] = describe(e), True
                return
        prev = self.lines[-1] if self.lines else None          # a call with no start line, such as a cache hit
        if e.get("status") == "cache hit" and prev and prev["done"] and prev["kind"] == e["kind"] and prev["label"] == e["label"]:
            prev["count"] += 1
            prev["text"] = describe({**e, "label": f"{prev['count']} \u00d7 {e['label']}"})
        else:
            self.lines.append({"kind": e["kind"], "label": e["label"], "text": describe(e), "done": True, "count": 1})

    def markdown(self, last: Optional[int] = None) -> str:
        out, in_step = [], False
        for ln in self.lines:
            in_step = in_step or ln["kind"] == "agent"
            indent = "    " if in_step and ln["kind"] != "agent" else ""
            out.append(f"{indent}- {ln['text']}" + ("" if ln["done"] else "  \u23f3"))
        if last:
            out = out[-last:]
        if out and out[0].startswith("    "):                  # a list cannot start indented: that would be a code block
            out[0] = out[0][4:]
        return "\n".join(out)


# ------------------------------------------------------------------ grouping for the Activity log page
_NEXT_NOTE = {"revise": "sent back for a retry", "redraft": "sent back for one redraft", "finalize": "passed", "end": "run ends"}


def _ended(e: dict) -> datetime:
    return datetime.fromisoformat(e["time"])


def _started(e: dict) -> datetime:
    return _ended(e) - timedelta(milliseconds=e.get("ms") or 0)


def detail(e: dict) -> str:
    """The one-line detail shown beside an event: what it used or how it ended, never prompt text."""
    kind, bits = e["kind"], []
    if kind == "agent":
        if e.get("node") != "drafter":
            bits.append(_NEXT_NOTE.get(e.get("next") or ""))
    elif kind == "llm":
        bits += [e.get("model"), f"rule: {e['check']}" if e.get("check") else None,
                 f"attempt {e['attempt']}" if (e.get("attempt") or 1) > 1 else None]
    elif kind == "embed":
        bits.append(e.get("model"))
    elif kind in ("vector", "bm25"):
        q = e.get("query") or ""
        if len(q) > 70:
            q = q[:70] + "\u2026"
        bits.append(f"\u201c{q}\u201d" if q else None)
        if e.get("filter"):
            bits.append("only " + ", ".join(e["filter"]))
    elif kind == "rerank":
        bits += [f"{e['candidates']} candidates" if e.get("candidates") else None, e.get("model")]
    elif kind == "tool":
        bits.append(f"result: {e['result']}" if e.get("result") else None)
    return " \u00b7 ".join(str(b) for b in bits if b)


def _fold_cached(rows: list[dict]) -> list[dict]:
    """Fold a run of identical cache hits into one row."""
    out: list[dict] = []
    for r in rows:
        p = out[-1] if out else None
        if r["status"] == "cache hit" and p and p["status"] == "cache hit" and (p["kind"], p["depth"], p["base"]) == (r["kind"], r["depth"], r["base"]):
            p["count"] += 1
            p["label"] = f"{p['count']} \u00d7 {r['base']}"
        else:
            out.append(r)
    return out


def _row(e: dict, depth: int, t0: datetime) -> dict:
    ms = e.get("ms")
    return {"depth": depth, "kind": e["kind"], "label": e["label"], "base": e["label"], "count": 1, "detail": detail(e),
            "at": round((_started(e) - t0).total_seconds(), 1), "seconds": None if ms is None else round(ms / 1000, 2),
            "tokens": e.get("tokens"), "hits": e.get("hits"), "status": e.get("status") if e.get("status") not in (None, "ok") else ""}


def group_runs(events: list[dict], kinds: Optional[set] = None, titles: Optional[dict] = None) -> list[dict]:
    """Group events (oldest first) by run, newest run first. Inside a run the events are in the order they happened,
    with the calls a step made listed under it: a step is logged when it ends, after the calls inside it, so the
    calls logged since the previous step belong to the next one.

    kinds: keep only rows of these kinds. titles: {run: {"title", "outcome"}} from the traces, for the group heading."""
    by_run: dict[str, list[dict]] = {}
    last_seen: dict[str, int] = {}
    for i, e in enumerate(events):
        by_run.setdefault(e.get("run") or "-", []).append(e)
        last_seen[e.get("run") or "-"] = i
    runs = []
    for run, evs in by_run.items():
        t0, end = min(_started(e) for e in evs), max(_ended(e) for e in evs)
        rows, pending = [], []
        for e in evs:
            if e["kind"] == "agent":
                rows.append(_row(e, 0, t0))
                rows += [_row(c, 1, t0) for c in pending]
                pending = []
            else:
                pending.append(e)
        rows += [_row(c, 0, t0) for c in pending]      # a lab page's calls have no step above them
        if kinds is not None:
            rows = [r for r in rows if r["kind"] in kinds]
            if "agent" not in kinds:
                for r in rows:
                    r["depth"] = 0
        rows = _fold_cached(rows)
        llm = [e for e in evs if e["kind"] == "llm" and e.get("tokens")]
        who = {k: next((e[k] for e in evs if e.get(k)), None) for k in ("user", "application_id", "strategy", "title")}
        info = (titles or {}).get(run, {})
        runs.append({**who, "run": run, "_last": last_seen[run], "when": t0.astimezone(timezone.utc).strftime("%d %b %H:%M:%S"), "ended": end,
                     "title": who["title"] or info.get("title") or ("Calls with no question attached" if run == "-" else f"Run {run}"),
                     "outcome": info.get("outcome", ""), "seconds": round((end - t0).total_seconds(), 1), "rows": rows,
                     "llm_calls": len(llm), "tokens": sum(e["tokens"] for e in llm), "llm_ms": sum(e.get("ms") or 0 for e in llm),
                     "searches": sum(1 for e in evs if e["kind"] == "vector"),
                     "issues": sum(1 for e in evs if e.get("status") not in (None, "ok", "cache hit"))})
    runs.sort(key=lambda r: (r["ended"], r.pop("_last")), reverse=True)   # a later position in the log is newer
    return runs


def read_runs(runs: int = 10, user: Optional[str] = None, kinds: Optional[set] = None, titles: Optional[dict] = None) -> list[dict]:
    """The newest `runs` groups from the log, optionally only one user's."""
    path = log_path()
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "time" in e and "kind" in e and "label" in e:
            events.append(e)
    if user:
        mine = {e.get("run") or "-" for e in events if e.get("user") == user}
        events = [e for e in events if (e.get("run") or "-") in mine]
    return group_runs(events, kinds, titles)[:runs]
