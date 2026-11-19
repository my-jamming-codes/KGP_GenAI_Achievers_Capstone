"""Conversation memory: the state that persists across turns.

One JSON-lines file per (user, application) under STORE_DIR/memory, so a conversation survives a page reload or an
app restart. Each turn keeps the masked question, the answer, the citations, the per-rule statuses, the lender type
and the trace id. The loop reads the last turns to resolve a follow-up question such as "and for a bank?".

Turns hold text the officer typed, so they are treated as untrusted data when they go back into a prompt.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import config
from src.safety.pii import mask_text

KEEP_TURNS = 20      # older turns are dropped from the file
ANSWER_CHARS = 600   # enough to resolve a follow-up; the full answer stays in the trace


class ConversationMemory:
    def __init__(self, directory: Optional[Path] = None, keep: int = KEEP_TURNS) -> None:
        self.dir = Path(directory) if directory else config.STORE_DIR / "memory"
        self.keep = keep

    def _path(self, user: str, application_id: Optional[str]) -> Path:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{user}__{application_id or 'general'}")
        return self.dir / f"{name}.jsonl"

    def load(self, user: str, application_id: Optional[str], n: int = 8) -> list[dict]:
        """The last n turns, oldest first. A damaged line is skipped rather than breaking the conversation."""
        path = self._path(user, application_id)
        if not path.exists():
            return []
        turns = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return turns[-n:]

    def append(self, user: str, application_id: Optional[str], turn: dict) -> dict:
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": mask_text(str(turn.get("question", "")))[:500],
            "answer": mask_text(str(turn.get("answer", "")))[:ANSWER_CHARS],
            "citations": list(turn.get("citations") or []),
            "results": dict(turn.get("results") or {}),
            "lender_type": turn.get("lender_type"),
            "trace_id": turn.get("trace_id"),
            "refused": bool(turn.get("refused")),
            "notes": list(turn.get("notes") or []),
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(user, application_id)
        turns = self.load(user, application_id, n=self.keep - 1) + [record]
        path.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in turns), encoding="utf-8")
        return record

    def clear(self, user: str, application_id: Optional[str]) -> None:
        self._path(user, application_id).unlink(missing_ok=True)
