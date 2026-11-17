import pytest

from ledger.api.rate_limit import TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_a_burst_up_to_capacity_then_refuses() -> None:
    limiter = TokenBucketLimiter(capacity=3, refill_per_second=1, clock=FakeClock())
    assert [limiter.consume("a").allowed for _ in range(4)] == [True, True, True, False]


def test_refused_requests_are_told_when_to_retry() -> None:
    limiter = TokenBucketLimiter(capacity=1, refill_per_second=0.5, clock=FakeClock())
    limiter.consume("a")
    decision = limiter.consume("a")
    assert not decision.allowed
    assert decision.retry_after_seconds == 2


def test_tokens_refill_over_time_but_never_beyond_capacity() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=2, refill_per_second=1, clock=clock)
    limiter.consume("a")
    limiter.consume("a")
    clock.advance(1)
    assert limiter.consume("a").allowed
    assert not limiter.consume("a").allowed
    clock.advance(100)  # a long idle period still only refills to capacity
    assert [limiter.consume("a").allowed for _ in range(3)] == [True, True, False]


def test_each_client_has_its_own_bucket() -> None:
    limiter = TokenBucketLimiter(capacity=1, refill_per_second=1, clock=FakeClock())
    assert limiter.consume("alice").allowed
    assert limiter.consume("bob").allowed
    assert not limiter.consume("alice").allowed


def test_memory_is_bounded_by_evicting_the_least_recent_client() -> None:
    limiter = TokenBucketLimiter(capacity=1, refill_per_second=1, clock=FakeClock(), max_keys=2)
    limiter.consume("a")
    limiter.consume("b")
    limiter.consume("c")  # evicts "a"
    assert limiter.consume("a").allowed  # "a" starts over with a full bucket


@pytest.mark.parametrize(("capacity", "rate"), [(0, 1.0), (1, 0.0)])
def test_invalid_configuration_is_rejected(capacity: int, rate: float) -> None:
    with pytest.raises(ValueError, match="capacity"):
        TokenBucketLimiter(capacity=capacity, refill_per_second=rate)
