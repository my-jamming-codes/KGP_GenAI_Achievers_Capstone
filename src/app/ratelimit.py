"""Fixed number of assistant questions per minute per session. A refused question gets a clear message, never an error."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

from src import config


class RateLimiter:
    def __init__(self, per_minute: int = config.RATE_LIMIT_PER_MINUTE, window_s: float = 60.0) -> None:
        self.per_minute, self.window_s = per_minute, window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, session_id: str, now: Optional[float] = None) -> tuple[bool, float]:
        """Return (allowed, seconds to wait)."""
        now = time.time() if now is None else now
        q = self._hits[session_id]
        while q and now - q[0] >= self.window_s:
            q.popleft()
        if len(q) >= self.per_minute:
            return False, round(self.window_s - (now - q[0]), 1)
        q.append(now)
        return True, 0.0

    def message(self, wait_s: float) -> str:
        return (f"You have reached the limit of {self.per_minute} questions a minute. "
                f"Please wait about {int(wait_s) + 1} seconds and ask again.")
