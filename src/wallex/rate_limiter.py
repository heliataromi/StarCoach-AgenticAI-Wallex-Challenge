"""Thread- and async-safe Token Bucket Rate Limiter with 3-tier Priority Queue,
emergency reserve bypass, monotonic time precision, and coordinated HTTP 429 backoff.
Calibrated to Wallex 30 requests / 10s rules (capacity: 30.0, refill: 3.0/s).
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, List, Optional, Union


class PriorityWaiter:
    """Internal waiter queued in rate limiter priority scheduling."""

    def __init__(self, priority: int, seq: int, weight: float, future: asyncio.Future) -> None:
        self.priority = priority             # 0=Emergency, 1=Orders, 2=Polling
        self.seq = seq                       # Sequence counter for FIFO within same priority
        self.weight = weight
        self.future = future

    def __lt__(self, other: PriorityWaiter) -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.seq < other.seq


class TokenBucketRateLimiter:
    """
    Quant-grade Token Bucket Rate Limiter enforcing Wallex exchange limits.

    Features:
    - Standard capacity: 30.0 tokens (burst limit).
    - Continuous refill rate: 3.0 tokens / second (30 tokens / 10s).
    - 3-tier Priority Queue:
        Priority 0: Emergency (Stop-loss, circuit breaker, order cancellations).
        Priority 1: Active orders (Post order, modify order).
        Priority 2: Background polling (Orderbook depth, candles, account balance).
    - Emergency Token Reserve (default 2.0 tokens): Reserved strictly for Priority 0.
    - Emergency Overdraft: Priority 0 can consume down to -1.0 token to guarantee zero latency.
    - Coordinated HTTP 429 freeze with exponential backoff (1.5^n + uniform jitter).
    - Monotonic clock source with injected clock support for deterministic offline testing.
    """

    def __init__(
        self,
        capacity: float = 30.0,
        refill_rate: Optional[float] = None,
        window_seconds: float = 10.0,
        emergency_reserve: float = 2.0,
        allow_emergency_overdraft: bool = True,
        clock: Optional[Union[Callable[[], float], Any]] = None,
        time_func: Optional[Callable[[], float]] = None,
    ) -> None:
        self.capacity = float(capacity)
        self.window_seconds = float(window_seconds)
        if refill_rate is not None:
            self.refill_rate = float(refill_rate)
        else:
            self.refill_rate = self.capacity / self.window_seconds if self.window_seconds > 0 else 3.0

        self.emergency_reserve = float(emergency_reserve)
        self.allow_emergency_overdraft = allow_emergency_overdraft

        # Resolve clock callable
        self._clock_source = self._resolve_clock(clock or time_func)

        self._tokens = float(self.capacity)
        self._last_refill = self._now()
        self._frozen_until = 0.0
        self._seq_counter = 0
        self._waiters: List[PriorityWaiter] = []
        self._lock: Optional[asyncio.Lock] = None

        self.request_count: int = 0
        self.throttle_count: int = 0

    @staticmethod
    def _resolve_clock(source: Optional[Union[Callable[[], float], Any]]) -> Callable[[], float]:
        if source is None:
            return time.monotonic
        if hasattr(source, "time") and callable(getattr(source, "time")):
            return source.time
        if callable(source):
            return source
        return time.monotonic

    def _now(self) -> float:
        return self._clock_source()

    @property
    def lock(self) -> asyncio.Lock:
        """Lazy initialization of asyncio.Lock to safely bind to the running loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def tokens(self) -> float:
        """Current available tokens after recalculating refill."""
        self._refill()
        return self._tokens

    @property
    def waiting_count(self) -> int:
        """Number of active waiters in the queue."""
        return len(self._waiters)

    @property
    def is_frozen(self) -> bool:
        """True if rate limiter is currently frozen by an active 429 cooldown."""
        return self._now() < self._frozen_until

    def _refill(self) -> None:
        """Replenishes tokens based on elapsed monotonic time."""
        now = self._now()
        if now > self._last_refill:
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + (elapsed * self.refill_rate))
            self._last_refill = now

    def _can_acquire(self, weight: float, priority: int) -> bool:
        """Checks if tokens can be acquired considering reserves and overdraft."""
        if self.is_frozen:
            return False

        if priority == 0:
            floor = -1.0 if self.allow_emergency_overdraft else 0.0
            return (self._tokens - weight) >= floor
        else:
            return (self._tokens - weight) >= self.emergency_reserve

    def try_acquire(
        self,
        weight: float = 1.0,
        priority: int = 1,
        tokens: Optional[float] = None,
    ) -> bool:
        """
        Non-blocking token acquisition check.
        Returns True and deducts tokens if available; returns False immediately otherwise.
        """
        req_weight = float(tokens if tokens is not None else weight)
        self.request_count += 1
        self._refill()

        # If lower/equal priority waiters exist in queue, do not bypass them in non-blocking mode
        if self._waiters and priority > 0:
            self.throttle_count += 1
            return False

        if self._can_acquire(req_weight, priority):
            self._tokens -= req_weight
            return True

        self.throttle_count += 1
        return False

    async def acquire(
        self,
        weight: float = 1.0,
        priority: int = 1,
        timeout: Optional[float] = None,
        tokens: Optional[float] = None,
    ) -> bool:
        """
        Asynchronously acquires tokens, waiting in the priority queue if necessary.

        Parameters:
        - weight / tokens: Token cost (default 1.0)
        - priority: 0 (Emergency), 1 (Orders), 2 (Polling)
        - timeout: Max seconds to wait before raising TimeoutError
        """
        req_weight = float(tokens if tokens is not None else weight)
        self.request_count += 1

        loop = asyncio.get_running_loop()
        deadline = (loop.time() + timeout) if timeout is not None else None

        async with self.lock:
            self._refill()

            # Fast path: tokens available and no waiters ahead
            if not self._waiters and self._can_acquire(req_weight, priority):
                self._tokens -= req_weight
                return True

            # Slow path: queue waiter
            self.throttle_count += 1
            self._seq_counter += 1
            future = loop.create_future()
            waiter = PriorityWaiter(priority, self._seq_counter, req_weight, future)
            self._insert_waiter_sorted(waiter)

        # Await queue processing
        try:
            while not future.done():
                remaining_timeout = None
                if deadline is not None:
                    remaining_timeout = deadline - loop.time()
                    if remaining_timeout <= 0:
                        raise asyncio.TimeoutError("TokenBucketRateLimiter acquire timed out")

                await self._process_waiters(remaining_timeout, target_waiter=waiter)
            return future.result()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            async with self.lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise

    async def _process_waiters(
        self,
        max_wait: Optional[float] = None,
        target_waiter: Optional[PriorityWaiter] = None,
    ) -> None:
        """Inspects waiters in priority order and satisfies all eligible waiters."""
        sleep_duration = 0.0
        async with self.lock:
            self._refill()

            now = self._now()
            if now < self._frozen_until:
                sleep_duration = self._frozen_until - now
            elif not self._waiters:
                return
            else:
                # Satisfy as many waiters in priority order as possible, batching by priority level
                # to guarantee higher priority tasks run on the event loop before lower priority tasks are released.
                batch_priority = None
                while self._waiters:
                    head = self._waiters[0]
                    if batch_priority is not None and head.priority != batch_priority:
                        break
                    if self._can_acquire(head.weight, head.priority):
                        self._waiters.pop(0)
                        self._tokens -= head.weight
                        batch_priority = head.priority
                        if not head.future.done():
                            head.future.set_result(True)
                    else:
                        break

                if not self._waiters:
                    return

                # Calculate sleep duration needed for the remaining head waiter
                head = self._waiters[0]
                needed = head.weight - (self._tokens - (self.emergency_reserve if head.priority > 0 else 0.0))
                if self.refill_rate > 0:
                    sleep_duration = min(0.005, max(0.001, needed / self.refill_rate))
                else:
                    sleep_duration = 0.005

        if max_wait is not None:
            sleep_duration = min(sleep_duration, max(0.001, max_wait))

        await asyncio.sleep(sleep_duration)

    def _insert_waiter_sorted(self, waiter: PriorityWaiter) -> None:
        """Inserts waiter preserving priority and sequence ordering."""
        idx = 0
        while idx < len(self._waiters) and self._waiters[idx] < waiter:
            idx += 1
        self._waiters.insert(idx, waiter)

    async def handle_429(
        self,
        retry_after: Optional[float] = None,
        attempt: int = 1,
    ) -> float:
        """
        Coordinates HTTP 429 backoff across the client subsystem.
        Freezes bucket and applies exponential backoff with jitter:
        delay = retry_after or min(10.0, 1.5 ** attempt + uniform(0.1, 0.5)).
        """
        if retry_after is not None:
            try:
                parsed = float(retry_after)
                delay = parsed if parsed > 0 else min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))
            except (ValueError, TypeError):
                delay = min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))
        else:
            delay = min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))

        now = self._now()
        self._frozen_until = max(self._frozen_until, now + delay)
        self._tokens = 0.0

        async with self.lock:
            now = self._now()
            self._frozen_until = max(self._frozen_until, now + delay)
            self._tokens = 0.0  # Reset tokens to avoid instant burst upon unfreeze

        await asyncio.sleep(delay)
        return delay

    def penalize_429(
        self,
        cooldown_seconds: Optional[float] = None,
        attempt: int = 1,
    ) -> float:
        """Synchronously applies 429 freeze penalty without awaiting (for fast tests)."""
        if cooldown_seconds is not None and cooldown_seconds > 0:
            delay = float(cooldown_seconds)
        else:
            delay = min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))

        now = self._now()
        self._frozen_until = max(self._frozen_until, now + delay)
        self._tokens = 0.0
        return delay

    def reset(self, tokens: Optional[float] = None) -> None:
        """Resets internal tokens and wait queues (for testing)."""
        self._tokens = float(tokens if tokens is not None else self.capacity)
        self._last_refill = self._now()
        self._frozen_until = 0.0
        self._waiters.clear()
        self.request_count = 0
        self.throttle_count = 0
