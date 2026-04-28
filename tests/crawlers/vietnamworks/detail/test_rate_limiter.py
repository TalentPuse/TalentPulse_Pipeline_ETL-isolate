from src.utils.rate_limiter import TokenBucket, jitter_sleep


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_token_bucket_burst_consumed_then_blocks_until_refill():
    clock = FakeClock()
    sleeps = []

    def sleeper(dt):
        sleeps.append(dt)
        clock.advance(dt)

    bucket = TokenBucket(rate_per_sec=1.0, burst=2, clock=clock, sleeper=sleeper)

    # Burst: 2 immediate acquires, no sleep
    bucket.acquire()
    bucket.acquire()
    assert sleeps == []

    # Third acquire must wait ~1s for refill
    bucket.acquire()
    assert len(sleeps) == 1
    assert 0.9 <= sleeps[0] <= 1.1


def test_token_bucket_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=2.0, burst=1, clock=clock, sleeper=lambda dt: clock.advance(dt))
    bucket.acquire()           # consume the only token
    clock.advance(0.5)         # refill 1 token (2/s * 0.5s)
    bucket.acquire()           # should not need to sleep


def test_jitter_sleep_within_band():
    captured = []
    jitter_sleep(2.0, spread=0.4, sleeper=lambda d: captured.append(d))
    assert len(captured) == 1
    assert 1.2 <= captured[0] <= 2.8
