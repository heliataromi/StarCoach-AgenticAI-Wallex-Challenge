"""Adversarial stress harness for M1: MarketDataSnapshot and MockWallexClient.
Authored by Challenger M1-2 to empirically verify edge cases, boundary conditions,
division-by-zero, numerical stability, and concurrency invariants.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Dict, List, Tuple

import pytest

from src.wallex.client import WallexClient
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import (
    AccountBalance,
    Candle,
    MarketDataSnapshot,
    OrderBook,
    OrderExecutionResult,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WallexInsufficientFundsError,
    WallexNetworkError,
    WallexOrderError,
    WallexRateLimitError,
)
from src.wallex.rate_limiter import TokenBucketRateLimiter


# ==============================================================================
# Suite 1: Mathematical Edge Cases in OrderBook & MarketDataSnapshot
# ==============================================================================

class TestOrderBookMathAdversarial:
    """Stress tests OrderBook calculations under extreme or pathological inputs."""

    def test_completely_empty_orderbook(self):
        """Verifies empty orderbook produces safe defaults without raising exceptions."""
        ob = OrderBook(symbol="BTCTMN", timestamp=1700000000000.0, bids=[], asks=[])
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.mid_price is None
        assert ob.spread == 0.0
        assert ob.spread_pct == 0.0
        assert ob.total_bid_volume() == 0.0
        assert ob.total_ask_volume() == 0.0
        assert ob.calculate_imbalance() == 0.0

        snapshot = MarketDataSnapshot.from_orderbook(ob)
        assert snapshot.best_bid == 0.0
        assert snapshot.best_ask == 0.0
        assert snapshot.spread_pct == 0.0
        assert snapshot.imbalance_ratio == 0.0
        assert snapshot.bids == []
        assert snapshot.asks == []

    def test_single_sided_bids_only(self):
        """Verifies orderbook with bids only (no asks)."""
        ob = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(4_200_000_000.0, 1.5), (4_190_000_000.0, 2.0)],
            asks=[],
        )
        assert ob.best_bid == 4_200_000_000.0
        assert ob.best_ask is None
        assert ob.mid_price == 4_200_000_000.0
        assert ob.spread == 0.0
        assert ob.spread_pct == 0.0
        assert ob.calculate_imbalance() == 1.0  # 100% bids

        snapshot = MarketDataSnapshot.from_orderbook(ob)
        assert snapshot.best_bid == 4_200_000_000.0
        assert snapshot.best_ask == 0.0
        assert snapshot.imbalance_ratio == 1.0

    def test_single_sided_asks_only(self):
        """Verifies orderbook with asks only (no bids)."""
        ob = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[],
            asks=[(4_210_000_000.0, 2.5)],
        )
        assert ob.best_bid is None
        assert ob.best_ask == 4_210_000_000.0
        assert ob.mid_price == 4_210_000_000.0
        assert ob.spread == 0.0
        assert ob.spread_pct == 0.0
        assert ob.calculate_imbalance() == -1.0  # 100% asks

        snapshot = MarketDataSnapshot.from_orderbook(ob)
        assert snapshot.best_bid == 0.0
        assert snapshot.best_ask == 4_210_000_000.0
        assert snapshot.imbalance_ratio == -1.0

    def test_zero_and_negative_prices_in_spread_pct(self):
        """Verifies division-by-zero protection in spread_pct when best_bid is 0.0 or negative."""
        # Zero bid price
        ob_zero = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(0.0, 1.0)],
            asks=[(100.0, 1.0)],
        )
        assert ob_zero.spread_pct == 0.0

        # Negative bid price
        ob_neg = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(-50.0, 1.0)],
            asks=[(100.0, 1.0)],
        )
        assert ob_neg.spread_pct == 0.0

    def test_negative_volumes_in_imbalance_calculation(self):
        """Adversarial check: anomalous negative volumes in feed do not cause crash."""
        # Net total volume <= 0
        ob_neg_net = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(100.0, -10.0)],
            asks=[(101.0, 5.0)],
        )
        assert ob_neg_net.calculate_imbalance() == 0.0

        # Net total volume = 0 (exact cancellation)
        ob_zero_net = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(100.0, -5.0)],
            asks=[(101.0, 5.0)],
        )
        assert ob_zero_net.calculate_imbalance() == 0.0

        # Out-of-bounds ratio gets clamped to [-1.0, 1.0]
        ob_clamp = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(100.0, -2.0)],
            asks=[(101.0, 8.0)],
        )
        # ( -2 - 8 ) / ( -2 + 8 ) = -10 / 6 = -1.666... -> clamped to -1.0
        assert ob_clamp.calculate_imbalance() == -1.0

    def test_extreme_float_magnitudes(self):
        """Verifies behavior under large numbers (1e200)."""
        ob_huge = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(1e12, 1e200)],
            asks=[(1e12 + 100, 1e200)],
        )
        assert ob_huge.calculate_imbalance() == pytest.approx(0.0)

    def test_crossed_and_locked_orderbook_detection(self):
        """Verifies crossed book triggers is_stale in MarketDataSnapshot."""
        # Inverted / Crossed (Bid > Ask)
        ob_crossed = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(4_300_000_000.0, 1.0)],
            asks=[(4_200_000_000.0, 1.0)],
        )
        assert ob_crossed.spread == 0.0
        # Spread pct becomes negative on crossed book
        assert ob_crossed.spread_pct < 0.0
        snap_crossed = MarketDataSnapshot.from_orderbook(ob_crossed, max_age_ms=10000.0, current_time_ms=1700000000000.0)
        assert snap_crossed.is_stale is True

        # Locked Book (Bid == Ask)
        ob_locked = OrderBook(
            symbol="BTCTMN",
            timestamp=1700000000000.0,
            bids=[(4_200_000_000.0, 1.0)],
            asks=[(4_200_000_000.0, 1.0)],
        )
        assert ob_locked.spread == 0.0
        assert ob_locked.spread_pct == 0.0
        snap_locked = MarketDataSnapshot.from_orderbook(ob_locked, max_age_ms=10000.0, current_time_ms=1700000000000.0)
        assert snap_locked.best_bid == snap_locked.best_ask


# ==============================================================================
# Suite 2: Staleness Boundary and Epoch Scaling
# ==============================================================================

class TestStalenessAdversarial:
    """Verifies staleness detection boundaries and timestamp representation handling."""

    def test_exact_staleness_boundaries_1500ms(self):
        """Tests exact 1500ms threshold behavior: <= 1500ms is fresh, > 1500ms is stale."""
        base_ts_ms = 1_700_000_000_000.0
        ob = OrderBook(symbol="BTCTMN", timestamp=base_ts_ms, bids=[(4.2e9, 1.0)], asks=[(4.21e9, 1.0)])

        # Exact boundary: 1500.0 ms age -> is_stale is False (condition is age > max_age_ms)
        assert ob.is_stale(max_age_ms=1500.0, current_time_ms=base_ts_ms + 1500.0) is False

        # 1500.001 ms age -> is_stale is True
        assert ob.is_stale(max_age_ms=1500.0, current_time_ms=base_ts_ms + 1500.001) is True

        # 1499.999 ms age -> is_stale is False
        assert ob.is_stale(max_age_ms=1500.0, current_time_ms=base_ts_ms + 1499.999) is False

    def test_timestamp_epoch_scale_seconds_vs_milliseconds(self):
        """Verifies OrderBook handles timestamps in seconds (< 1e11) vs milliseconds (>= 1e11)."""
        ts_sec = 1_700_000_000.0          # Seconds (~1.7e9 < 1e11)
        ts_ms = 1_700_000_000_000.0       # Milliseconds (~1.7e12 >= 1e11)

        ob_sec = OrderBook(symbol="BTCTMN", timestamp=ts_sec, bids=[], asks=[])
        ob_ms = OrderBook(symbol="BTCTMN", timestamp=ts_ms, bids=[], asks=[])

        # Current time in ms: 1 second later (1000ms < 1500ms)
        now_ms = 1_700_000_001_000.0
        assert ob_sec.is_stale(max_age_ms=1500.0, current_time_ms=now_ms) is False
        assert ob_ms.is_stale(max_age_ms=1500.0, current_time_ms=now_ms) is False

        # Current time in ms: 2 seconds later (2000ms > 1500ms)
        stale_now_ms = 1_700_000_002_000.0
        assert ob_sec.is_stale(max_age_ms=1500.0, current_time_ms=stale_now_ms) is True
        assert ob_ms.is_stale(max_age_ms=1500.0, current_time_ms=stale_now_ms) is True

    def test_future_timestamp_handling(self):
        """Negative age (timestamp in the future due to clock skew)."""
        base_ts_ms = 1_700_000_000_000.0
        ob = OrderBook(symbol="BTCTMN", timestamp=base_ts_ms + 10_000.0, bids=[], asks=[])
        assert ob.is_stale(max_age_ms=1500.0, current_time_ms=base_ts_ms) is False

    def test_snapshot_timestamp_normalizes_to_milliseconds(self):
        """Verifies MarketDataSnapshot.timestamp normalizes seconds (< 1e11) to milliseconds epoch."""
        ob_sec = OrderBook(symbol="BTCTMN", timestamp=1_700_000_000.0, bids=[], asks=[])
        snap = MarketDataSnapshot.from_orderbook(ob_sec)
        assert snap.timestamp == 1_700_000_000_000.0


# ==============================================================================
# Suite 3: MockWallexClient Order Execution, Balances & Concurrency
# ==============================================================================

class TestMockWallexClientAdversarial:
    """Stress tests MockWallexClient order placement, balance invariants, and concurrency."""

    def test_zero_funds_buy_order_rejection(self):
        """Verifies BUY order rejection when available TMN is 0.0."""
        async def _test():
            mock = MockWallexClient(initial_tmn=0.0)
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=4_200_000_000.0,
                quantity=0.0001,
            )
            assert res.status == OrderStatus.REJECTED
            assert "Insufficient TMN" in res.raw_response.get("error", "")

        asyncio.run(_test())

    def test_zero_crypto_sell_order_rejection(self):
        """Verifies SELL order rejection when crypto balance is 0.0."""
        async def _test():
            mock = MockWallexClient()
            mock.crypto_balances["BTC"] = 0.0
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                price=4_200_000_000.0,
                quantity=0.0001,
            )
            assert res.status == OrderStatus.REJECTED
            assert "Insufficient BTC" in res.raw_response.get("error", "")

        asyncio.run(_test())

    def test_exact_balance_boundary_buy_fill(self):
        """Verifies BUY order when balance exactly equals cost + fee."""
        async def _test():
            price = 4_000_000_000.0
            qty = 0.001
            cost = price * qty  # 4,000,000 TMN
            fee = cost * 0.0025  # 10,000 TMN
            total_required = cost + fee  # 4,010,000 TMN

            mock = MockWallexClient(initial_tmn=total_required)
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=price,
                quantity=qty,
                client_order_id="EXACT-BUY",
            )
            assert res.status == OrderStatus.FILLED
            assert pytest.approx(mock.tmn_available, abs=1e-5) == 0.0
            assert pytest.approx(mock.crypto_balances["BTC"], abs=1e-8) == qty

        asyncio.run(_test())

    def test_off_by_one_cent_insufficient_funds(self):
        """Verifies BUY order is rejected when balance is 0.01 TMN short of total required."""
        async def _test():
            price = 4_000_000_000.0
            qty = 0.001
            cost = price * qty
            fee = cost * 0.0025
            total_required = cost + fee  # 4,010,000 TMN

            mock = MockWallexClient(initial_tmn=total_required - 0.01)
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=price,
                quantity=qty,
                client_order_id="SHORT-BUY",
            )
            assert res.status == OrderStatus.REJECTED

        asyncio.run(_test())

    def test_fee_deduction_arithmetic_precision(self):
        """Verifies maker/taker fee (0.25%) arithmetic precision on BUY and SELL."""
        async def _test():
            mock = MockWallexClient(initial_tmn=5_000_000.0)

            # 1. BUY: Cost = 2,000,000, Fee (0.25%) = 5,000 TMN -> Deduct 2,005,000 TMN
            buy_res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=4_000_000_000.0,
                quantity=0.0005,
                client_order_id="FEE-BUY",
            )
            assert buy_res.status == OrderStatus.FILLED
            assert buy_res.fee_paid_tmn == 5000.0
            assert pytest.approx(mock.tmn_available, 1e-4) == (5_000_000.0 - 2_005_000.0)
            assert pytest.approx(mock.crypto_balances["BTC"], 1e-8) == 0.0005

            # 2. SELL: Revenue = 2,000,000, Fee (0.25%) = 5,000 TMN -> Add 1,995,000 TMN
            sell_res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                price=4_000_000_000.0,
                quantity=0.0005,
                client_order_id="FEE-SELL",
            )
            assert sell_res.status == OrderStatus.FILLED
            assert sell_res.fee_paid_tmn == 5000.0
            expected_final_tmn = 5_000_000.0 - 10_000.0
            assert pytest.approx(mock.tmn_available, 1e-4) == expected_final_tmn
            assert pytest.approx(mock.crypto_balances["BTC"], 1e-8) == 0.0

        asyncio.run(_test())

    def test_probe_negative_quantity_behavior(self):
        """Verifies MockWallexClient rejects negative quantity with WallexOrderError."""
        async def _test():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=4_200_000_000.0,
                quantity=-0.0001,
                client_order_id="PROBE-NEG-BUY",
            )

        with pytest.raises(WallexOrderError, match="Invalid order quantity"):
            asyncio.run(_test())

    def test_probe_zero_quantity_behavior(self):
        """Verifies MockWallexClient rejects zero quantity with WallexOrderError."""
        async def _test():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=4_200_000_000.0,
                quantity=0.0,
                client_order_id="PROBE-ZERO-QTY",
            )

        with pytest.raises(WallexOrderError, match="Invalid order quantity"):
            asyncio.run(_test())

    def test_concurrent_orders_race_condition_and_total_equity(self):
        """
        Stress test: launches 20 concurrent BUY orders of 500,000 TMN each.
        Initial balance is 2,000,000 TMN.
        Only at most 3 orders should fill before balance is exhausted;
        the remaining orders must be REJECTED.
        Balance must never go negative!
        """
        async def _test():
            initial_tmn = 2_000_000.0
            rate_limiter = TokenBucketRateLimiter(capacity=100.0, refill_rate=100.0)
            mock = MockWallexClient(initial_tmn=initial_tmn, rate_limiter=rate_limiter)

            price = 4_000_000_000.0
            qty = 0.000125  # Cost: 500,000 TMN, Fee: 1,250 TMN -> Total: 501,250 TMN
            total_cost_per_order = 501_250.0

            async def place_order(idx: int):
                return await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    price=price,
                    quantity=qty,
                    client_order_id=f"CONCURRENT-{idx}",
                )

            tasks = [place_order(i) for i in range(20)]
            results = await asyncio.gather(*tasks)

            filled = [r for r in results if r.status == OrderStatus.FILLED]
            rejected = [r for r in results if r.status == OrderStatus.REJECTED]

            # Invariant 1: Filled + Rejected == 20
            assert len(filled) + len(rejected) == 20

            # Invariant 2: Filled must be exactly 3 (3 * 501,250 = 1,503,750 <= 2,000,000)
            assert len(filled) == 3
            assert len(rejected) == 17

            # Invariant 3: Available TMN must be non-negative
            assert mock.tmn_available >= 0.0
            expected_remaining = initial_tmn - (3 * total_cost_per_order)
            assert pytest.approx(mock.tmn_available, abs=1e-4) == expected_remaining

            # Invariant 4: Crypto balance equals 3 * qty
            assert pytest.approx(mock.crypto_balances["BTC"], abs=1e-8) == (3 * qty)

            # Invariant 5: Unique exchange IDs
            exchange_ids = [r.exchange_order_id for r in filled]
            assert len(set(exchange_ids)) == len(exchange_ids)

        asyncio.run(_test())

    def test_concurrent_orders_with_simulated_latency(self):
        """
        Concurrency with async latency:
        Simulate 5ms network latency in mock client.
        Verify that multiple coroutines yielding at await do not cause double-spend.
        """
        async def _test():
            initial_tmn = 1_000_000.0
            rate_limiter = TokenBucketRateLimiter(capacity=100.0, refill_rate=100.0)
            mock = MockWallexClient(initial_tmn=initial_tmn, rate_limiter=rate_limiter)
            mock.set_simulated_latency(0.005)  # 5ms latency

            price = 4_000_000_000.0
            qty = 0.00015  # Cost: 600,000 TMN, Fee: 1,500 TMN -> 601,500 TMN
            # Only 1 order can fill out of 5!

            async def place(idx: int):
                return await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    price=price,
                    quantity=qty,
                    client_order_id=f"LATENCY-ORD-{idx}",
                )

            results = await asyncio.gather(*[place(i) for i in range(5)])
            filled = [r for r in results if r.status == OrderStatus.FILLED]
            rejected = [r for r in results if r.status == OrderStatus.REJECTED]

            assert len(filled) == 1
            assert len(rejected) == 4
            assert mock.tmn_available >= 0.0

        asyncio.run(_test())
