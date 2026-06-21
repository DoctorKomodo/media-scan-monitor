"""LoginRateLimiter: per-IP failure counting inside a sliding monotonic window."""

from mediascanmonitor.web.ratelimit import LoginRateLimiter


class FakeClock:
    """Injectable monotonic clock so the window logic is deterministic."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_allowed_until_max_attempts() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300.0, now=clock)
    assert limiter.allowed("1.2.3.4") is True
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.allowed("1.2.3.4") is True  # 2 < 3
    limiter.record_failure("1.2.3.4")
    assert limiter.allowed("1.2.3.4") is False  # 3 >= 3


def test_keys_are_isolated_per_ip() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=300.0, now=clock)
    limiter.record_failure("1.1.1.1")
    assert limiter.allowed("1.1.1.1") is False
    assert limiter.allowed("2.2.2.2") is True


def test_window_expiry_forgets_old_failures() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=300.0, now=clock)
    limiter.record_failure("ip")
    limiter.record_failure("ip")
    assert limiter.allowed("ip") is False
    clock.t = 301.0  # both failures now outside the 300s window
    assert limiter.allowed("ip") is True


def test_reset_clears_failures() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=300.0, now=clock)
    limiter.record_failure("ip")
    assert limiter.allowed("ip") is False
    limiter.reset("ip")
    assert limiter.allowed("ip") is True


def test_reset_unknown_key_is_noop() -> None:
    limiter = LoginRateLimiter()
    limiter.reset("never-seen")  # must not raise
    assert limiter.allowed("never-seen") is True
