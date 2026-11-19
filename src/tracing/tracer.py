"""One trace per question or rule check, stored as one JSON line. Doubles as the audit trail."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src import config
from src.safety.pii import mask_obj


class StepCapReached(Exception):
    pass


def new_id() -> str:
    """A 12-character hex id for a trace or a group of activity. A letter is forced at every third position, so an id
    never holds a run of 10 or more digits and the PII masker cannot mistake it for a phone or Aadhaar number."""
    h = list(uuid.uuid4().hex[:12])
    for i in (0, 3, 6, 9):
        h[i] = "abcdef"[int(h[i], 16) % 6]
    return "".join(h)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Trace:
    def __init__(self, question: str, user: str = "system", application_id: Optional[str] = None, strategy: str = "",
                 path: Optional[Path] = None, max_steps: int = config.MAX_STEPS) -> None:
        self.path = Path(path) if path else config.TRACE_FILE
        self.max_steps = max_steps
        self.started = time.time()
        self.record: dict[str, Any] = {
            "type": "trace", "trace_id": new_id(), "timestamp": _now(), "user": user,
            "application_id": application_id, "question": mask_obj(question), "strategy": strategy,
            "steps": [], "cache_hit": False, "tokens": {"prompt": 0, "completion": 0}, "outcome": {},
        }

    @property
    def trace_id(self) -> str:
        return self.record["trace_id"]

    @property
    def steps_used(self) -> int:
        return len(self.record["steps"])

    def step(self, agent: str, input: Any = None, output: Any = None, **extra: Any) -> dict:
        """Record one agent step. Raises StepCapReached when the hard cap is hit."""
        if self.steps_used >= self.max_steps:
            raise StepCapReached(f"step cap of {self.max_steps} reached")
        entry = {"step": self.steps_used + 1, "agent": agent, "strategy": self.record["strategy"],
                 "input": mask_obj(input), "output": mask_obj(output), **mask_obj(extra)}
        self.record["steps"].append(entry)
        return entry

    def add_tokens(self, prompt: int, completion: int) -> None:
        self.record["tokens"]["prompt"] += prompt
        self.record["tokens"]["completion"] += completion

    def finish(self, **outcome: Any) -> str:
        self.record["outcome"] = mask_obj(outcome)
        self.record["latency_ms"] = int((time.time() - self.started) * 1000)
        _append(self.path, self.record)
        return self.trace_id


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def log_officer_action(user: str, application_id: str, action: str, detail: str = "", trace_id: Optional[str] = None,
                       path: Optional[Path] = None) -> None:
    """accepted, disputed, commented, information_requested, cleared, returned."""
    _append(Path(path) if path else config.TRACE_FILE, mask_obj({
        "type": "officer_action", "timestamp": _now(), "user": user, "application_id": application_id,
        "action": action, "detail": detail, "trace_id": trace_id}))


def read_traces(path: Optional[Path] = None, application_id: Optional[str] = None) -> list[dict]:
    p = Path(path) if path else config.TRACE_FILE
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()]
    return [r for r in rows if application_id is None or r.get("application_id") == application_id]
