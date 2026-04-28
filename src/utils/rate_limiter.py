"""Sync token bucket + jitter sleep helper."""
import random
import time
from threading import Lock


class TokenBucket:
    """
    Refills `1 / period` tokens per second up to `burst`.
    `acquire()` blocks until 1 token is available.
    """

    def __init__(self, rate_per_sec: float, burst: int = 1, clock=time.monotonic, sleeper=time.sleep):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._refill_rate = rate_per_sec
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last = clock()
        self._lock = Lock()
        self._clock = clock
        self._sleeper = sleeper

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
            self._last = now

    def acquire(self) -> None:
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self._refill_rate
            self._sleeper(wait)


def jitter_sleep(base: float, spread: float = 0.4, sleeper=time.sleep) -> None:
    """Sleep for base +/- (base * spread) seconds, never below 0.1s."""
    delta = base * spread
    duration = max(0.1, base + random.uniform(-delta, delta))
    sleeper(duration)
