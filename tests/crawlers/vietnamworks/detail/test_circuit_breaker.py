from src.utils.circuit_breaker import CircuitBreaker


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_breaker_starts_closed():
    cb = CircuitBreaker()
    assert not cb.is_open()
    assert cb.reason() is None


def test_three_consecutive_403_trips_breaker():
    cb = CircuitBreaker()
    for _ in range(3):
        cb.record(403)
    assert cb.is_open()
    assert "403" in cb.reason()


def test_two_403_does_not_trip():
    cb = CircuitBreaker()
    cb.record(403)
    cb.record(200)
    cb.record(403)
    assert not cb.is_open()


def test_high_ratelimit_ratio_trips():
    cb = CircuitBreaker(window=10)
    # 3 of 10 are 429 → 30% > 20%
    for _ in range(7):
        cb.record(200)
    for _ in range(3):
        cb.record(429)
    assert cb.is_open()


def test_high_error_ratio_trips():
    cb = CircuitBreaker(window=10)
    for _ in range(4):
        cb.record(200)
    for _ in range(6):
        cb.record(500)
    assert cb.is_open()


def test_breaker_resets_after_cooldown():
    clock = FakeClock()
    cb = CircuitBreaker(cooldown_sec=60, clock=clock)
    for _ in range(3):
        cb.record(403)
    assert cb.is_open()
    clock.t = 61
    assert not cb.is_open()
