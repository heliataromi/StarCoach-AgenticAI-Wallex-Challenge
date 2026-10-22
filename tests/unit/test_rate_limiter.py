"""Unit tests for TokenBucketRateLimiter.
Covers capacity limits, burst recovery, fractional refills, priority bypass,
emergency reserve, 429 backoff freezing, and concurrent queue processing.
"""
from __future__ import annotations

import asyncio
import pytest

from src.wallex.rate_limiter import TokenBucketRateLimiter


class MockClock:
    """Deterministic monotonic clock test seam."""

    def __init__(self, initial_time: float = 100_000.0) -> None:
        self._current_time: float = initial_time

    def __call__(self) -> float:
        return self._current_time

    def time(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Time cannot move backwards in monotonic clock")
        self._current_time += seconds


def test_rate_limiter_initial_state():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(
        capacity=30.0,
        refill_rate=3.0,
        emergency_reserve=2.0,
        clock=clock,
    )
    assert limiter.capacity == 30.0
    assert limiter.refill_rate == 3.0
    assert limiter.emergency_reserve == 2.0
    assert limiter.tokens == 30.0
    assert limiter.waiting_count == 0
    assert not limiter.is_frozen


def test_rate_limiter_try_acquire_single_token():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=30.0, refill_rate=3.0, clock=clock)
    assert limiter.try_acquire(weight=1.0, priority=1) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 29.0
    assert limiter.request_count == 1
    assert limiter.throttle_count == 0


def test_rate_limiter_try_acquire_multiple_tokens():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=30.0, refill_rate=3.0, clock=clock)
    assert limiter.try_acquire(weight=10.0, priority=1) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 20.0


def test_rate_limiter_capacity_exhaustion():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(
        capacity=30.0,
        refill_rate=3.0,
        emergency_reserve=2.0,
        clock=clock,
    )
    # Consume 28 tokens
    assert limiter.try_acquire(weight=28.0, priority=1) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 2.0

    # Next priority 1 request should be rejected because 2 tokens are reserved for priority 0
    assert limiter.try_acquire(weight=1.0, priority=1) is False
    assert limiter.throttle_count == 1
    assert pytest.approx(limiter.tokens, 1e-4) == 2.0


def test_rate_limiter_emergency_reserve_protection():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(
        capacity=5.0,
        refill_rate=1.0,
        emergency_reserve=2.0,
        clock=clock,
    )
    # Drain down to reserve
    assert limiter.try_acquire(weight=3.0, priority=1) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 2.0

    # Priority 1 is blocked
    assert limiter.try_acquire(weight=1.0, priority=1) is False

    # Priority 0 CAN acquire the emergency reserve token
    assert limiter.try_acquire(weight=1.0, priority=0) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 1.0


def test_rate_limiter_emergency_overdraft():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(
        capacity=5.0,
        refill_rate=1.0,
        emergency_reserve=2.0,
        allow_emergency_overdraft=True,
        clock=clock,
    )
    # Drain all 5 tokens
    assert limiter.try_acquire(weight=5.0, priority=0) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 0.0

    # Normal orders rejected
    assert limiter.try_acquire(weight=1.0, priority=1) is False

    # Emergency order overdrafts down to -1.0 token
    assert limiter.try_acquire(weight=1.0, priority=0) is True
    assert pytest.approx(limiter.tokens, 1e-4) == -1.0

    # Cannot overdraft beyond -1.0
    assert limiter.try_acquire(weight=0.5, priority=0) is False


def test_rate_limiter_burst_recovery_refill():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=30.0, refill_rate=3.0, clock=clock)
    # Drain all tokens
    assert limiter.try_acquire(weight=30.0, priority=0) is True
    assert pytest.approx(limiter.tokens, 1e-4) == 0.0

    # Advance clock by 1 second -> +3 tokens
    clock.advance(1.0)
    assert pytest.approx(limiter.tokens, 1e-4) == 3.0

    # Advance clock by 4 more seconds -> +12 tokens (total 15)
    clock.advance(4.0)
    assert pytest.approx(limiter.tokens, 1e-4) == 15.0

    # Advance clock by 20 more seconds -> should be capped at capacity (30.0)
    clock.advance(20.0)
    assert pytest.approx(limiter.tokens, 1e-4) == 30.0


def test_rate_limiter_fractional_refill():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=10.0, refill_rate=3.0, clock=clock)
    assert limiter.try_acquire(weight=10.0, priority=0) is True

    # Advance by 1/3 second -> 0.3333 * 3.0 = 1.0 token
    clock.advance(1.0 / 3.0)
    assert pytest.approx(limiter.tokens, 1e-3) == 1.0
    assert limiter.try_acquire(weight=1.0, priority=0) is True


def test_rate_limiter_429_penalize_and_recovery():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=10.0, refill_rate=1.0, clock=clock)

    delay = limiter.penalize_429(cooldown_seconds=5.0)
    assert delay == 5.0
    assert limiter.is_frozen is True
    assert limiter.tokens == 0.0

    # While frozen, acquire is blocked
    assert limiter.try_acquire(weight=1.0, priority=0) is False

    # Advance clock past cooldown
    clock.advance(5.1)
    assert limiter.is_frozen is False

    # Now tokens start refilling
    clock.advance(2.0)
    assert limiter.tokens >= 2.0
    assert limiter.try_acquire(weight=1.0, priority=0) is True


def test_rate_limiter_reset():
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=20.0, refill_rate=2.0, clock=clock)
    limiter.try_acquire(weight=15.0, priority=1)
    limiter.penalize_429(cooldown_seconds=10.0)
    assert limiter.is_frozen is True

    limiter.reset(tokens=20.0)
    assert limiter.tokens == 20.0
    assert limiter.is_frozen is False
    assert limiter.request_count == 0
    assert limiter.throttle_count == 0


def test_rate_limiter_priority_scheduling_order():
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=10.0,
            refill_rate=1.0,
            emergency_reserve=0.0,
            allow_emergency_overdraft=False,
            clock=clock,
        )
        # Drain all 10 tokens
        assert limiter.try_acquire(weight=10.0, priority=0) is True

        order_completed = []

        async def worker(name: str, prio: int):
            await limiter.acquire(weight=1.0, priority=prio)
            order_completed.append(name)

        # Enqueue in reverse priority order: P2 first, then P1, then P0
        t2 = asyncio.create_task(worker("P2_poll", prio=2))
        await asyncio.sleep(0.001)
        t1 = asyncio.create_task(worker("P1_order", prio=1))
        await asyncio.sleep(0.001)
        t0 = asyncio.create_task(worker("P0_emergency", prio=0))
        await asyncio.sleep(0.001)

        # 1. Assert internal queue preserves priority ordering (0 < 1 < 2)
        assert [w.priority for w in limiter._waiters] == [0, 1, 2]

        # 2. Advance clock to refill 1 token -> P0 should complete
        clock.advance(1.0)
        await asyncio.sleep(0.03)
        assert order_completed == ["P0_emergency"]

        # 3. Advance clock to refill 1 more token -> P1 should complete
        clock.advance(1.0)
        await asyncio.sleep(0.03)
        assert order_completed == ["P0_emergency", "P1_order"]

        # 4. Advance clock to refill 1 more token -> P2 should complete
        clock.advance(1.0)
        await asyncio.sleep(0.03)
        assert order_completed == ["P0_emergency", "P1_order", "P2_poll"]

        await asyncio.gather(t2, t1, t0)

    asyncio.run(_test())


def test_rate_limiter_acquire_timeout():
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=0.0,
            refill_rate=0.0,
            emergency_reserve=0.0,
            allow_emergency_overdraft=False,
            clock=clock,
        )

        with pytest.raises(asyncio.TimeoutError):
            await limiter.acquire(weight=1.0, priority=1, timeout=0.02)

        # Confirm waiter was removed from queue on timeout
        assert limiter.waiting_count == 0

    asyncio.run(_test())
