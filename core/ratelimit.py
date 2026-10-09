"""Simple sliding-window rate limiter (per user+endpoint).

Protects the database from runaway clients: each identity gets
`per_minute` tool calls per rolling 60s window. In-memory, per process.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict


class RateLimiter:
    def __init__(self, per_minute: int = 120) -> None:
        self.per_minute = per_minute
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}

    def check(self, key: str) -> bool:
        """Return True if the call is allowed (and record it)."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and dq[0] <= now - 60:
                dq.popleft()
            if len(dq) >= self.per_minute:
                return False
            dq.append(now)
            return True


def _default_limit() -> int:
    try:
        return max(1, int(os.environ.get("DBMCP_RATE_LIMIT_PER_MIN", "120")))
    except ValueError:
        return 120


ratelimiter = RateLimiter(_default_limit())
