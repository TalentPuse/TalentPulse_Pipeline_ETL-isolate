"""Circuit breaker that opens on sustained HTTP errors."""
import time
from collections import deque


class CircuitBreaker:
    """
    Tracks the last `window` HTTP status codes (None = network error).
    Opens when:
      - 3 consecutive 403 (treated as block signal), OR
      - >50% of last `window` results are errors (>=400 or None), OR
      - >20% of last `window` are 429/503 (rate-limit signal).
    Once open, stays open for `cooldown_sec` seconds.
    """

    BLOCK_STATUSES = {403}
    RATELIMIT_STATUSES = {429, 503}

    def __init__(self, window: int = 20, cooldown_sec: int = 900, clock=time.monotonic):
        self._window = window
        self._cooldown = cooldown_sec
        self._events: deque[int | None] = deque(maxlen=window)
        self._opened_at: float | None = None
        self._reason: str | None = None
        self._clock = clock

    def record(self, status: int | None) -> None:
        self._events.append(status)
        self._evaluate()

    def _evaluate(self) -> None:
        if len(self._events) >= 3 and all(s == 403 for s in list(self._events)[-3:]):
            self._trip("3 consecutive 403 responses")
            return
        if len(self._events) < self._window:
            return
        total = len(self._events)
        errors = sum(1 for s in self._events if s is None or s >= 400)
        ratelimits = sum(1 for s in self._events if s in self.RATELIMIT_STATUSES)
        if ratelimits / total > 0.20:
            self._trip(f"rate-limit ratio {ratelimits}/{total} > 20%")
        elif errors / total > 0.50:
            self._trip(f"error ratio {errors}/{total} > 50%")

    def _trip(self, reason: str) -> None:
        self._opened_at = self._clock()
        self._reason = reason

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self._cooldown:
            self._opened_at = None
            self._reason = None
            self._events.clear()
            return False
        return True

    def reason(self) -> str | None:
        return self._reason
