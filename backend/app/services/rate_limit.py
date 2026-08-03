"""In-process sliding-window rate limiter (no Redis required)."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque

logger = logging.getLogger("stock_agent.rate_limit")


class SlidingWindowLimiter:
    """Per-key hit tracker. Process-local; fine for a single API instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, Deque[float]] = defaultdict(deque)

    def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
    ) -> tuple[bool, int, int]:
        """
        Record a hit and return (allowed, remaining, retry_after_seconds).

        `remaining` is how many more requests are allowed in the current window
        after this hit (0 when blocked or at the limit).
        """
        if limit <= 0:
            return True, 0, 0

        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            hits = self._windows[key]
            while hits and hits[0] < cutoff:
                hits.popleft()

            if len(hits) >= limit:
                retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
                return False, 0, retry_after

            hits.append(now)
            remaining = max(0, limit - len(hits))
            return True, remaining, 0

    def prune(self, max_keys: int = 10_000) -> None:
        """Drop empty / excess keys so long-running processes stay bounded."""
        with self._lock:
            empty = [k for k, v in self._windows.items() if not v]
            for k in empty:
                del self._windows[k]
            overflow = len(self._windows) - max_keys
            if overflow <= 0:
                return
            # Drop oldest-looking keys (arbitrary but bounded).
            for key in list(self._windows.keys())[:overflow]:
                del self._windows[key]


_limiter = SlidingWindowLimiter()


def get_limiter() -> SlidingWindowLimiter:
    return _limiter
