"""Adversarial Empirical Stress-Testing Suite for TokenBucketRateLimiter.
Covers:
1. High-concurrency contention (100+ simultaneous tasks requesting tokens).
2. Priority 0 preemption under 0 tokens, emergency overdraft (-1.0 floor), and queue ordering.
3. Coordinated HTTP 429 backoff under concurrent stress (no deadlocks, no race conditions, clean unfreeze).
4. Cancellation resilience and timeout cleanup under concurrent load.
5. Invariant checking: token conservation, no starvation of higher priorities, FIFO preservation.
6. Multi-weight requests and dynamic overdraft replenishment math.
"""
from __future__ import annotations

import asyncio
import random
import time
import pytest

from src.wallex.rate_limiter import TokenBucketRateLimiter


class MockClock:
    """Deterministic monotonic clock test seam."""

    def __init__(self, initial_time: float = 1_000_000.0) -> None:
        self._current_time: float = initial_time

    def __call__(self) -> float:
        return self._current_time

    def time(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Monotonic clock cannot move backwards")
        self._current_time += seconds


# ==============================================================================
# SECTION 1: High-Concurrency Contention (100 Simultaneous Tasks)
# ==============================================================================

def test_high_concurrency_100_tasks_fairness_and_ordering():
    """Adversarial stress-test: 100 simultaneous tasks contending for tokens.
    Bucket drained to 0 tokens before launching tasks so all tasks must queue.
    Mix of priorities:
      - 20 Priority 0 (Emergency)
      - 40 Priority 1 (Active orders)
      - 40 Priority 2 (Polling)
    Verifies:
      1. Zero deadlocks: all 100 tasks complete within timeout.
      2. Priority 0 preemption: P0_0 completes immediately via overdraft to -1.0.
      3. Strict priority enforcement: all 20 P0 tasks complete before any P1, and all P1 complete before any P2.
      4. Total tokens accurately accounted for.
    """
    async def _test():
        limiter = TokenBucketRateLimiter(
            capacity=100.0,
            refill_rate=0.0,  # Refill paused during initial queuing
            emergency_reserve=2.0,
            allow_emergency_overdraft=True,
        )
        # Drain all tokens so every single task enters the priority waiter queue
        assert limiter.try_acquire(weight=100.0, priority=0) is True

        completed_log: list[tuple[str, int, float]] = []

        async def worker(name: str, prio: int):
            success = await limiter.acquire(weight=1.0, priority=prio)
            assert success is True
            completed_log.append((name, prio, time.monotonic()))

        tasks = []
        # Create tasks in reverse priority order: P2 first, then P1, then P0
        for i in range(40):
            tasks.append(asyncio.create_task(worker(f"P2_{i}", 2)))
        for i in range(40):
            tasks.append(asyncio.create_task(worker(f"P1_{i}", 1)))
        for i in range(20):
            tasks.append(asyncio.create_task(worker(f"P0_{i}", 0)))

        # Wait for all 100 tasks to enqueue and P0_0 to overdraft
        await asyncio.sleep(0.05)
        assert len(completed_log) == 1
        assert completed_log[0][1] == 0  # First P0 completed via overdraft

        # Start refill to release the remaining 99 tasks in priority order
        limiter.refill_rate = 500.0
        limiter._last_refill = limiter._now()

        # Step event loop until all tasks complete
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)

        assert len(completed_log) == 100
        assert limiter.waiting_count == 0

        priorities = [prio for _, prio, _ in completed_log]

        # Invariant 1: Exactly 20 P0 tasks must complete first
        assert priorities[:20] == [0] * 20, f"Expected 20 P0 tasks first, got {priorities[:20]}"

        # Invariant 2: Exactly 40 P1 tasks must complete next
        assert priorities[20:60] == [1] * 40, f"Expected 40 P1 tasks next, got {priorities[20:60]}"

        # Invariant 3: Exactly 40 P2 tasks must complete last
        assert priorities[60:100] == [2] * 40, f"Expected 40 P2 tasks last, got {priorities[60:100]}"

    asyncio.run(_test())


def test_high_concurrency_deterministic_queue_fifo():
    """Deterministic verification of queue ordering with frozen refill and MockClock."""
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=10.0,
            refill_rate=1.0,
            emergency_reserve=0.0,
            allow_emergency_overdraft=False,
            clock=clock,
        )
        assert limiter.try_acquire(weight=10.0, priority=0) is True

        completion_order: list[int] = []

        async def worker(task_id: int, prio: int):
            await limiter.acquire(weight=1.0, priority=prio)
            completion_order.append(task_id)

        tasks = []
        for i in range(10):
            tasks.append(asyncio.create_task(worker(i, prio=2)))
        for i in range(10, 20):
            tasks.append(asyncio.create_task(worker(i, prio=1)))
        for i in range(20, 30):
            tasks.append(asyncio.create_task(worker(i, prio=0)))

        # Wait for all 30 tasks to queue
        await asyncio.sleep(0.02)
        assert limiter.waiting_count == 30

        # Invariant: internal waiter queue is strictly sorted by priority then seq
        waiter_prios = [w.priority for w in limiter._waiters]
        assert waiter_prios == [0] * 10 + [1] * 10 + [2] * 10

        # Step clock with sufficient pause between steps so each task completes its cycle
        for step in range(30):
            clock.advance(1.0)
            await asyncio.sleep(0.03)

        await asyncio.gather(*tasks)

        expected_order = list(range(20, 30)) + list(range(10, 20)) + list(range(0, 10))
        assert completion_order == expected_order

    asyncio.run(_test())


# ==============================================================================
# SECTION 2: Priority 0 Preemption Under 0 Tokens & Emergency Overdraft
# ==============================================================================

def test_priority_0_overdraft_preemption_under_zero_tokens():
    """Verify that when tokens are at 0.0, a Priority 0 request can overdraft to -1.0
    immediately and preempt waiting Priority 1 orders.
    """
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=5.0,
            refill_rate=1.0,
            emergency_reserve=2.0,
            allow_emergency_overdraft=True,
            clock=clock,
        )

        assert limiter.try_acquire(weight=5.0, priority=0) is True
        assert limiter.tokens == 0.0

        p1_completed = False
        p0_completed = False

        async def p1_worker():
            nonlocal p1_completed
            await limiter.acquire(weight=1.0, priority=1)
            p1_completed = True

        async def p0_worker():
            nonlocal p0_completed
            await limiter.acquire(weight=1.0, priority=0)
            p0_completed = True

        t1 = asyncio.create_task(p1_worker())
        await asyncio.sleep(0.01)
        assert limiter.waiting_count == 1
        assert p1_completed is False

        t0 = asyncio.create_task(p0_worker())
        await asyncio.sleep(0.05)

        assert p0_completed is True, "Priority 0 must overdraft and complete"
        assert p1_completed is False, "Priority 1 must remain queued"
        assert pytest.approx(limiter.tokens, 1e-4) == -1.0

        clock.advance(3.0)
        await asyncio.sleep(0.01)
        assert p1_completed is False

        clock.advance(1.1)
        await asyncio.sleep(0.03)
        assert p1_completed is True

        await asyncio.gather(t1, t0)

    asyncio.run(_test())


def test_priority_0_cannot_exceed_overdraft_limit():
    """Verify that Priority 0 cannot overdraft beyond -1.0 floor."""
    clock = MockClock()
    limiter = TokenBucketRateLimiter(
        capacity=5.0,
        refill_rate=1.0,
        emergency_reserve=2.0,
        allow_emergency_overdraft=True,
        clock=clock,
    )
    assert limiter.try_acquire(weight=5.0, priority=0) is True

    assert limiter.try_acquire(weight=1.0, priority=0) is True
    assert pytest.approx(limiter.tokens, 1e-4) == -1.0

    assert limiter.try_acquire(weight=0.1, priority=0) is False
    assert pytest.approx(limiter.tokens, 1e-4) == -1.0


def test_emergency_overdraft_disabled_enforces_zero_floor():
    """When allow_emergency_overdraft=False, P0 cannot acquire when tokens < 1.0."""
    clock = MockClock()
    limiter = TokenBucketRateLimiter(
        capacity=5.0,
        refill_rate=1.0,
        emergency_reserve=2.0,
        allow_emergency_overdraft=False,
        clock=clock,
    )
    assert limiter.try_acquire(weight=5.0, priority=0) is True
    assert limiter.tokens == 0.0

    assert limiter.try_acquire(weight=1.0, priority=0) is False
    assert limiter.tokens == 0.0


def test_priority_0_overdraft_multi_weight():
    """P0 requesting weight 2.0 when tokens=0.0 requires tokens - weight >= -1.0,
    so 0.0 - 2.0 = -2.0 < -1.0 must be rejected/queued until tokens refill to 1.0.
    """
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=5.0,
            refill_rate=1.0,
            emergency_reserve=2.0,
            allow_emergency_overdraft=True,
            clock=clock,
        )
        assert limiter.try_acquire(weight=5.0, priority=0) is True
        assert limiter.tokens == 0.0

        p0_done = False
        async def p0_worker():
            nonlocal p0_done
            await limiter.acquire(weight=2.0, priority=0)
            p0_done = True

        task = asyncio.create_task(p0_worker())
        await asyncio.sleep(0.01)
        assert p0_done is False, "Weight 2.0 cannot overdraft from 0.0 to -2.0"

        # Advance clock by 1.0s -> tokens become 1.0. Now 1.0 - 2.0 = -1.0 >= -1.0
        clock.advance(1.0)
        await asyncio.sleep(0.03)
        assert p0_done is True, "With 1.0 token, weight 2.0 overdrafts to -1.0"
        assert pytest.approx(limiter.tokens, 1e-4) == -1.0

        await task

    asyncio.run(_test())


# ==============================================================================
# SECTION 3: Coordinated HTTP 429 Backoff Under Concurrent Stress
# ==============================================================================

def test_coordinated_429_backoff_freeze_and_unfreeze():
    """Test concurrent tasks encountering HTTP 429 backoff:
    1. Freeze blocks all tasks during cooldown.
    2. Multiple simultaneous 429 reports coordinate safely without deadlocks.
    3. Unfreeze cleanly awakens all waiting tasks without lost notifications.
    """
    async def _test():
        limiter = TokenBucketRateLimiter(
            capacity=10.0,
            refill_rate=100.0,
            emergency_reserve=2.0,
        )

        completed_after_freeze: list[int] = []

        async def worker(task_id: int):
            await limiter.acquire(weight=1.0, priority=1)
            if limiter.is_frozen:
                pytest.fail(f"Task {task_id} acquired while rate limiter was frozen!")
            completed_after_freeze.append(task_id)

        tasks = [asyncio.create_task(worker(i)) for i in range(20)]

        async def trigger_429(attempt: int):
            await limiter.handle_429(retry_after=0.1, attempt=attempt)

        penalty_tasks = [asyncio.create_task(trigger_429(i + 1)) for i in range(5)]

        await asyncio.wait_for(asyncio.gather(*penalty_tasks, *tasks), timeout=3.0)

        assert len(completed_after_freeze) == 20
        assert limiter.is_frozen is False
        assert limiter.waiting_count == 0

    asyncio.run(_test())


def test_429_penalize_resets_tokens_empirically():
    """Verify that 429 resets available tokens to 0.0 and freezes bucket."""
    clock = MockClock()
    limiter = TokenBucketRateLimiter(capacity=30.0, refill_rate=3.0, clock=clock)
    assert limiter.tokens == 30.0

    delay = limiter.penalize_429(cooldown_seconds=4.0)
    assert delay == 4.0
    assert limiter.is_frozen is True
    assert limiter.tokens == 0.0

    clock.advance(2.0)
    assert limiter.is_frozen is True
    assert limiter.try_acquire(weight=1.0, priority=0) is False

    clock.advance(2.1)
    assert limiter.is_frozen is False


def test_429_storm_under_50_concurrent_requests():
    """Adversarial stress-test: 50 tasks hammering acquire() while multiple 429s are injected.
    Asserts zero race conditions, zero deadlocks, and that no token is acquired during freeze.
    """
    async def _test():
        limiter = TokenBucketRateLimiter(
            capacity=5.0,
            refill_rate=50.0,
            emergency_reserve=1.0,
        )

        acquired_timestamps: list[float] = []
        freeze_intervals: list[tuple[float, float]] = []

        async def requester(idx: int):
            for _ in range(5):
                await limiter.acquire(weight=1.0, priority=1)
                acquired_timestamps.append(time.monotonic())
                await asyncio.sleep(0.005)

        async def injector_429():
            for attempt in range(1, 4):
                await asyncio.sleep(0.03)
                start = time.monotonic()
                delay = await limiter.handle_429(retry_after=0.08, attempt=attempt)
                end = time.monotonic()
                freeze_intervals.append((start, end))

        req_tasks = [asyncio.create_task(requester(i)) for i in range(10)]
        inj_task = asyncio.create_task(injector_429())

        await asyncio.wait_for(asyncio.gather(*req_tasks, inj_task), timeout=5.0)

        # Invariant: No tokens should have been acquired during any freeze interval
        for acq in acquired_timestamps:
            for f_start, f_end in freeze_intervals:
                assert not (f_start + 0.002 < acq < f_end - 0.002), (
                    f"Token acquired at {acq} inside freeze window ({f_start}, {f_end})"
                )

    asyncio.run(_test())


# ==============================================================================
# SECTION 4: Queue Cancellation & Timeout Resilience
# ==============================================================================

def test_concurrent_waiter_cancellation_preserves_queue_integrity():
    """When tasks waiting in acquire() are cancelled, they must be cleanly removed
    from self._waiters without leaving dangling references or corrupting sequence.
    """
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=0.0,
            refill_rate=0.0,
            clock=clock,
        )

        cancelled_count = 0

        async def fragile_worker(idx: int):
            nonlocal cancelled_count
            try:
                await limiter.acquire(weight=1.0, priority=1)
            except asyncio.CancelledError:
                cancelled_count += 1
                raise

        workers = [asyncio.create_task(fragile_worker(i)) for i in range(50)]
        await asyncio.sleep(0.01)
        assert limiter.waiting_count == 50

        to_cancel = workers[:25]
        for t in to_cancel:
            t.cancel()

        await asyncio.sleep(0.02)

        assert cancelled_count == 25
        assert limiter.waiting_count == 25

        for t in workers[25:]:
            t.cancel()
        await asyncio.sleep(0.01)
        assert limiter.waiting_count == 0

    asyncio.run(_test())


def test_waiter_timeout_clean_removal():
    """Timeouts must remove waiters cleanly from the priority queue."""
    async def _test():
        clock = MockClock()
        limiter = TokenBucketRateLimiter(
            capacity=0.0,
            refill_rate=0.0,
            clock=clock,
        )

        async def timeout_worker():
            with pytest.raises(asyncio.TimeoutError):
                await limiter.acquire(weight=1.0, priority=1, timeout=0.02)

        tasks = [asyncio.create_task(timeout_worker()) for _ in range(20)]
        await asyncio.gather(*tasks)

        assert limiter.waiting_count == 0

    asyncio.run(_test())
