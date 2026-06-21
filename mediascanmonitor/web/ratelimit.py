"""In-memory, per-client-IP login throttle (contract §C).

No persistence — counters live in process memory and reset on restart. Each key (the
client IP) keeps a list of recent failure timestamps; ``allowed`` prunes timestamps
older than the window and blocks once ``max_attempts`` remain. ``now`` is injectable so
tests drive the clock deterministically; production uses ``time.monotonic`` (immune to
wall-clock jumps).
"""

import time
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._now = now
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str) -> list[float]:
        cutoff = self._now() - self._window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def allowed(self, key: str) -> bool:
        return len(self._prune(key)) < self._max_attempts

    def record_failure(self, key: str) -> None:
        recent = self._prune(key)
        recent.append(self._now())
        self._failures[key] = recent

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
