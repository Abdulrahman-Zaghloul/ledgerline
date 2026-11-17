"""Token-bucket rate limiting.

Each client has a bucket holding up to ``capacity`` tokens that refills at a steady
rate. A request spends one token; an empty bucket means "slow down". Bursts up to
``capacity`` are allowed, but the long-run rate can never exceed the refill rate.

This implementation keeps buckets in process memory, which is correct for a single
API instance. Running several instances would need a shared store such as Redis.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class TokenBucketLimiter:
    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 100_000,
    ) -> None:
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_second > 0")
        self._capacity = float(capacity)
        self._rate = refill_per_second
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def consume(self, key: str, cost: float = 1.0) -> RateLimitDecision:
        with self._lock:
            now = self._clock()
            tokens, updated_at = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - updated_at) * self._rate)
            allowed = tokens >= cost
            if allowed:
                tokens -= cost
            self._buckets[key] = (tokens, now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max_keys:  # bound memory: evict least recent
                self._buckets.popitem(last=False)
        if allowed:
            return RateLimitDecision(allowed=True)
        return RateLimitDecision(
            allowed=False, retry_after_seconds=max(1, math.ceil((cost - tokens) / self._rate))
        )
