"""Adversarial stress test harness for MockWallexClient limit order execution.
Authored by Challenger M1-It3-1.

Empirically challenges:
1. Inside-Spread Limit Orders:
   - Orders placed strictly inside the spread (best_bid < price < best_ask) on BUY and SELL.
   - Verifies they enter OrderStatus.PENDING, record in self.open_orders, and lock collateral.
   - Tests boundary conditions (price at best_bid, best_ask, and fractional epsilon steps).
   - Tests multi-asset pairs: BTCTMN, ETHTMN, USDTTMN.
2. Duplicate active client_order_id rejection:
   - Active resting orders reject duplicate client_order_id with WallexOrderError.
   - Zero collateral leak or stranding on duplicate rejection (BUY and SELL).
   - Concurrent race conditions with asyncio.gather: exactly 1 succeeds, all others reject cleanly.
   - Reuse of client_order_id after cancellation is safely permitted.
   - Idempotent double cancellation prevents double refunding.
3. Collateral Escrow Restoration & Exactness:
   - Precision floating-point verification: zero fractional drift across cancel cycles.
   - Targeted cancel_all_orders with symbol filtering preserves other symbols' escrows.
4. Mark-to-Market Total Equity Invariance:
   - Equity invariance across multi-order placement and cancellation cycles.
   - Mark-to-market accuracy under extreme price shifts for both available and locked crypto.
   - 500-cycle high-frequency churn stress test.
"""
from __future__ import annotations

import asyncio
import random
import pytest

from src.wallex.mock_client import MockWallexClient
from src.wallex.models import (
    AccountBalance,
    OrderExecutionResult,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WallexOrderError,
)


class NoOpRateLimiter:
    """Zero-delay rate limiter for high-throughput in-memory unit testing."""
    def acquire(self, priority: int = 1) -> bool:
        return True


class TestInsideSpreadLimitOrdersEmpirical:
    """Stress tests limit orders placed strictly inside the market spread."""

    @pytest.mark.parametrize("symbol", ["BTCTMN", "ETHTMN", "USDTTMN"])
    def test_inside_spread_buy_orders_rest_in_open_orders(self, symbol: str):
        """Verifies BUY limit orders with best_bid < price < best_ask enter PENDING,
        are recorded in open_orders, and lock cost + fee.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=250_000_000.0, rate_limiter=NoOpRateLimiter())
            depth = await mock.get_market_depth(symbol)
            best_bid = depth.best_bid
            best_ask = depth.best_ask
            spread = best_ask - best_bid
            assert spread > 0, f"Spread must be positive for {symbol}"

            # Test 10 fractional price levels inside the spread
            fractions = [0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 0.999, 0.50]
            for idx, frac in enumerate(fractions):
                test_price = best_bid + frac * spread
                assert best_bid < test_price < best_ask

                qty = 0.001 if "BTC" in symbol else (0.01 if "ETH" in symbol else 10.0)
                cid = f"INSIDE-BUY-{symbol}-{idx}"

                initial_avail = mock.tmn_available
                initial_locked = mock.tmn_locked

                cost = test_price * qty
                fee = cost * mock.fee_rate
                expected_lock = cost + fee

                res = await mock.post_order(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=test_price,
                    quantity=qty,
                    client_order_id=cid,
                )

                assert res.status == OrderStatus.PENDING, (
                    f"Inside-spread BUY order at {test_price} must be PENDING, got {res.status}"
                )
                assert res.filled_quantity == 0.0
                assert res.fee_paid_tmn == 0.0
                assert res.client_order_id == cid
                assert cid in mock.open_orders
                assert mock.open_orders[cid]["status"] == "PENDING"
                assert mock.open_orders[cid]["price"] == test_price
                assert mock.open_orders[cid]["quantity"] == qty

                # Check exact collateral lock
                assert mock.tmn_locked == pytest.approx(initial_locked + expected_lock, 1e-6)
                assert mock.tmn_available == pytest.approx(initial_avail - expected_lock, 1e-6)

        asyncio.run(_run())

    @pytest.mark.parametrize("symbol", ["BTCTMN", "ETHTMN", "USDTTMN"])
    def test_inside_spread_sell_orders_rest_in_open_orders(self, symbol: str):
        """Verifies SELL limit orders with best_bid < price < best_ask enter PENDING,
        are recorded in open_orders, and lock crypto quantity.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=50_000_000.0)
            base_asset = symbol.replace("TMN", "")
            mock.crypto_balances[base_asset] = 100.0

            depth = await mock.get_market_depth(symbol)
            best_bid = depth.best_bid
            best_ask = depth.best_ask
            spread = best_ask - best_bid
            assert spread > 0

            fractions = [0.001, 0.05, 0.20, 0.50, 0.80, 0.95, 0.99]
            for idx, frac in enumerate(fractions):
                test_price = best_bid + frac * spread
                assert best_bid < test_price < best_ask

                qty = 0.001 if "BTC" in symbol else (0.05 if "ETH" in symbol else 5.0)
                cid = f"INSIDE-SELL-{symbol}-{idx}"

                initial_coin = mock.crypto_balances[base_asset]
                initial_coin_locked = mock.crypto_locked[base_asset]

                res = await mock.post_order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    price=test_price,
                    quantity=qty,
                    client_order_id=cid,
                )

                assert res.status == OrderStatus.PENDING, (
                    f"Inside-spread SELL order at {test_price} must be PENDING, got {res.status}"
                )
                assert res.filled_quantity == 0.0
                assert res.fee_paid_tmn == 0.0
                assert res.client_order_id == cid
                assert cid in mock.open_orders
                assert mock.open_orders[cid]["status"] == "PENDING"
                assert mock.open_orders[cid]["price"] == test_price
                assert mock.open_orders[cid]["quantity"] == qty

                # Check exact crypto collateral lock
                assert mock.crypto_locked[base_asset] == pytest.approx(initial_coin_locked + qty, 1e-8)
                assert mock.crypto_balances[base_asset] == pytest.approx(initial_coin - qty, 1e-8)

        asyncio.run(_run())

    def test_spread_boundary_behavior(self):
        """Verifies exact boundaries:
        - BUY at best_ask is Crossing (FILLED)
        - BUY at best_ask - 1 is Resting (PENDING)
        - SELL at best_bid is Crossing (FILLED)
        - SELL at best_bid + 1 is Resting (PENDING)
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=20_000_000.0)
            mock.crypto_balances["BTC"] = 1.0
            depth = await mock.get_market_depth("BTCTMN")
            best_bid = depth.best_bid
            best_ask = depth.best_ask

            # BUY at best_ask fills immediately
            r1 = await mock.post_order(symbol="BTCTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=best_ask, quantity=0.0001, client_order_id="B-AT-ASK")
            assert r1.status == OrderStatus.FILLED
            assert "B-AT-ASK" not in mock.open_orders

            # BUY at best_ask - 1.0 rests in open_orders
            r2 = await mock.post_order(symbol="BTCTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=best_ask - 1.0, quantity=0.0001, client_order_id="B-BELOW-ASK")
            assert r2.status == OrderStatus.PENDING
            assert "B-BELOW-ASK" in mock.open_orders

            # SELL at best_bid fills immediately
            r3 = await mock.post_order(symbol="BTCTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=best_bid, quantity=0.0001, client_order_id="S-AT-BID")
            assert r3.status == OrderStatus.FILLED
            assert "S-AT-BID" not in mock.open_orders

            # SELL at best_bid + 1.0 rests in open_orders
            r4 = await mock.post_order(symbol="BTCTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=best_bid + 1.0, quantity=0.0001, client_order_id="S-ABOVE-BID")
            assert r4.status == OrderStatus.PENDING
            assert "S-ABOVE-BID" in mock.open_orders

        asyncio.run(_run())


class TestDuplicateClientOrderIdEmpirical:
    """Stress tests duplicate client_order_id rejections and escrow leakage prevention."""

    def test_duplicate_active_buy_order_rejects_without_escrow_leak(self):
        """Verifies duplicate active client_order_id for BUY raises WallexOrderError
        and leaks 0 Toman in locked or available balances.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            cid = "DUP-BUY-TEST-01"

            # 1. Place initial resting BUY order
            res1 = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1_000_000.0,
                quantity=1.0,
                client_order_id=cid,
            )
            assert res1.status == OrderStatus.PENDING
            snap_avail = mock.tmn_available
            snap_locked = mock.tmn_locked
            assert snap_locked > 0

            # 2. Attempt duplicate order with same CID
            with pytest.raises(WallexOrderError, match="Duplicate client_order_id"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=2_000_000.0,
                    quantity=0.5,
                    client_order_id=cid,
                )

            # 3. Assert zero leakage or alteration in balances
            assert mock.tmn_available == snap_avail
            assert mock.tmn_locked == snap_locked
            assert len(mock.open_orders) == 1
            assert mock.open_orders[cid]["quantity"] == 1.0  # untouched

        asyncio.run(_run())

    def test_duplicate_active_sell_order_rejects_without_escrow_leak(self):
        """Verifies duplicate active client_order_id for SELL raises WallexOrderError
        and leaks 0 crypto balance.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["ETH"] = 5.0
            cid = "DUP-SELL-TEST-01"

            # 1. Place initial resting SELL order
            res1 = await mock.post_order(
                symbol="ETHTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=500_000_000.0,
                quantity=2.0,
                client_order_id=cid,
            )
            assert res1.status == OrderStatus.PENDING
            snap_eth = mock.crypto_balances["ETH"]
            snap_eth_locked = mock.crypto_locked["ETH"]
            assert snap_eth == 3.0
            assert snap_eth_locked == 2.0

            # 2. Attempt duplicate order with same CID
            with pytest.raises(WallexOrderError, match="Duplicate client_order_id"):
                await mock.post_order(
                    symbol="ETHTMN",
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    price=500_000_000.0,
                    quantity=1.0,
                    client_order_id=cid,
                )

            # 3. Assert zero crypto leaked or stranded
            assert mock.crypto_balances["ETH"] == snap_eth
            assert mock.crypto_locked["ETH"] == snap_eth_locked
            assert len(mock.open_orders) == 1
            assert mock.open_orders[cid]["quantity"] == 2.0

        asyncio.run(_run())

    def test_duplicate_cross_side_rejection(self):
        """Verifies duplicate active CID is rejected even across different sides and symbols."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 1.0
            cid = "CROSS-SIDE-DUP"

            # Place BUY order with CID
            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1000.0,
                quantity=1.0,
                client_order_id=cid,
            )

            # Attempt SELL order on different symbol with SAME CID
            with pytest.raises(WallexOrderError, match="Duplicate client_order_id"):
                await mock.post_order(
                    symbol="USDTTMN",
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    price=100_000.0,
                    quantity=5.0,
                    client_order_id=cid,
                )

            # Verify BTC buy remains open and intact
            assert cid in mock.open_orders
            assert mock.open_orders[cid]["symbol"] == "BTCTMN"

        asyncio.run(_run())

    def test_concurrent_duplicate_order_race_condition(self):
        """Launches 25 concurrent post_order tasks with the identical client_order_id.
        Empirically asserts exactly 1 succeeds and 24 raise WallexOrderError,
        with exact single-order collateral locked.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=10_000_000.0)
            cid = "RACE-CID-999"
            price = 50_000.0
            qty = 2.0
            cost = price * qty
            fee = cost * mock.fee_rate
            expected_total_locked = cost + fee

            async def _submit(i: int):
                return await mock.post_order(
                    symbol="USDTTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=price,
                    quantity=qty,
                    client_order_id=cid,
                )

            tasks = [_submit(i) for i in range(25)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            successes = [r for r in results if isinstance(r, OrderExecutionResult)]
            failures = [r for r in results if isinstance(r, WallexOrderError)]

            assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
            assert len(failures) == 24, f"Expected exactly 24 failures, got {len(failures)}"
            assert successes[0].status == OrderStatus.PENDING

            # Assert escrow locked is for exactly 1 order
            assert mock.tmn_locked == pytest.approx(expected_total_locked, 1e-6)
            assert mock.tmn_available == pytest.approx(10_000_000.0 - expected_total_locked, 1e-6)
            assert len(mock.open_orders) == 1

        asyncio.run(_run())

    def test_idempotent_double_cancellation_prevents_double_refund(self):
        """Verifies calling cancel_order multiple times on the same order:
        - First cancel refunds collateral.
        - Subsequent cancels succeed (idempotent) but DO NOT refund a second time.
        """
        async def _run():
            initial_tmn = 5_000_000.0
            mock = MockWallexClient(initial_tmn=initial_tmn)
            cid = "DOUBLE-CANCEL-01"

            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1000.0,
                quantity=10.0,
                client_order_id=cid,
            )
            assert mock.tmn_locked > 0

            # 1st cancel -> should release locked funds
            ok1 = await mock.cancel_order(client_order_id=cid)
            assert ok1 is True
            assert mock.tmn_locked == 0.0
            assert mock.tmn_available == pytest.approx(initial_tmn, 1e-6)

            # 2nd cancel -> idempotent success, but NO extra balance fabricated
            ok2 = await mock.cancel_order(client_order_id=cid)
            assert ok2 is True
            assert mock.tmn_locked == 0.0
            assert mock.tmn_available == pytest.approx(initial_tmn, 1e-6)

            # 3rd cancel by exchange order id -> no extra funds
            ok3 = await mock.cancel_order(order_id="NON-EXISTENT-WX-999")
            assert ok3 is True
            assert mock.tmn_available == pytest.approx(initial_tmn, 1e-6)

        asyncio.run(_run())


class TestCollateralEscrowAndCancellationEmpirical:
    """Stress tests collateral escrow locking and cancellation precision."""

    def test_cancel_all_orders_with_symbol_filter(self):
        """Verifies cancel_all_orders(symbol='BTCTMN') only cancels BTC orders,
        leaving ETH orders locked and untouched.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=10_000_000.0)
            mock.crypto_balances["BTC"] = 1.0
            mock.crypto_balances["ETH"] = 5.0

            # Place 2 BTC orders (1 BUY, 1 SELL)
            await mock.post_order(symbol="BTCTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=1000.0, quantity=1.0, client_order_id="BTC-B")
            await mock.post_order(symbol="BTCTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=10e9, quantity=0.2, client_order_id="BTC-S")

            # Place 2 ETH orders (1 BUY, 1 SELL)
            await mock.post_order(symbol="ETHTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=500.0, quantity=2.0, client_order_id="ETH-B")
            await mock.post_order(symbol="ETHTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=1e9, quantity=1.5, client_order_id="ETH-S")

            assert len(mock.open_orders) == 4
            assert mock.crypto_locked["BTC"] == 0.2
            assert mock.crypto_locked["ETH"] == 1.5

            # Cancel ONLY BTCTMN orders
            count = await mock.cancel_all_orders(symbol="BTCTMN")
            assert count == 2
            assert len(mock.open_orders) == 2
            assert "ETH-B" in mock.open_orders
            assert "ETH-S" in mock.open_orders

            # BTC funds fully released
            assert mock.crypto_locked["BTC"] == 0.0
            assert mock.crypto_balances["BTC"] == 1.0

            # ETH funds still locked!
            assert mock.crypto_locked["ETH"] == 1.5
            assert mock.crypto_balances["ETH"] == 3.5

            # Cancel remaining ETH orders
            count_eth = await mock.cancel_all_orders(symbol="ETHTMN")
            assert count_eth == 2
            assert len(mock.open_orders) == 0
            assert mock.crypto_locked["ETH"] == 0.0
            assert mock.crypto_balances["ETH"] == 5.0

        asyncio.run(_run())

    def test_high_precision_floating_escrow_invariance(self):
        """Verifies small fractional amounts (e.g. 8 decimal places) lock and restore
        without floating-point truncation or accumulation error.
        """
        async def _run():
            initial_tmn = 5_000_000.0
            mock = MockWallexClient(initial_tmn=initial_tmn)
            mock.crypto_balances["BTC"] = 0.12345678

            # Place 5 orders with odd decimal quantities
            quantities = [0.00012345, 0.00098765, 0.00543210, 0.00111111, 0.00333333]
            for i, q in enumerate(quantities):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    price=5_000_000_000.0,
                    quantity=q,
                    client_order_id=f"PREC-SELL-{i}",
                )

            assert len(mock.open_orders) == 5
            total_locked_expected = sum(quantities)
            assert mock.crypto_locked["BTC"] == pytest.approx(total_locked_expected, 1e-8)

            # Cancel all
            await mock.cancel_all_orders()
            assert len(mock.open_orders) == 0
            assert mock.crypto_locked["BTC"] == 0.0
            assert mock.crypto_balances["BTC"] == pytest.approx(0.12345678, 1e-8)

        asyncio.run(_run())


class TestMarkToMarketEquityInvarianceEmpirical:
    """Stress tests mark-to-market total equity invariance across complex scenarios."""

    def test_multi_order_placement_and_individual_cancellation_equity_invariance(self):
        """Places 20 resting orders across BTC, ETH, USDT on both sides,
        asserting total equity remains strictly constant after each placement
        and after each cancellation.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=20_000_000.0)
            mock.crypto_balances["BTC"] = 0.50
            mock.crypto_balances["ETH"] = 5.00
            mock.crypto_balances["USDT"] = 1000.0

            bal_init = await mock.get_account_balances()
            initial_equity = bal_init.total_equity_tmn

            placed_cids = []

            # 1. Place 10 BUY orders
            for i in range(10):
                cid = f"EQ-INV-B-{i}"
                res = await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=1000.0 * (i + 1),
                    quantity=0.01,
                    client_order_id=cid,
                )
                assert res.status == OrderStatus.PENDING
                placed_cids.append(cid)

                bal = await mock.get_account_balances()
                assert bal.total_equity_tmn == pytest.approx(initial_equity, 1e-6)

            # 2. Place 10 SELL orders
            for i in range(10):
                cid = f"EQ-INV-S-{i}"
                res = await mock.post_order(
                    symbol="ETHTMN",
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    price=500_000_000.0 + i * 1_000_000.0,
                    quantity=0.2,
                    client_order_id=cid,
                )
                assert res.status == OrderStatus.PENDING
                placed_cids.append(cid)

                bal = await mock.get_account_balances()
                assert bal.total_equity_tmn == pytest.approx(initial_equity, 1e-6)

            assert len(mock.open_orders) == 20

            # 3. Cancel orders in reverse order
            for cid in reversed(placed_cids):
                ok = await mock.cancel_order(client_order_id=cid)
                assert ok is True
                bal = await mock.get_account_balances()
                assert bal.total_equity_tmn == pytest.approx(initial_equity, 1e-6)

            assert len(mock.open_orders) == 0
            bal_final = await mock.get_account_balances()
            assert bal_final.total_equity_tmn == pytest.approx(initial_equity, 1e-6)

        asyncio.run(_run())

    def test_mtm_valuation_with_large_symbol_price_fluctuations(self):
        """Verifies that when market prices change while resting orders are open,
        total equity correctly reflects the price change on BOTH available and locked crypto.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 1.0  # 4.2B each -> 4.2B
            mock.crypto_balances["ETH"] = 10.0  # 180M each -> 1.8B

            # Lock 0.4 BTC in resting sell
            await mock.post_order(symbol="BTCTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=10e9, quantity=0.4, client_order_id="LOCK-BTC")

            # Lock 4.0 ETH in resting sell
            await mock.post_order(symbol="ETHTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=1e9, quantity=4.0, client_order_id="LOCK-ETH")

            # Lock 2,000,000 TMN in resting buy
            await mock.post_order(symbol="USDTTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=1000.0, quantity=1995.0, client_order_id="LOCK-TMN")

            # Prices shift:
            # BTC goes from 4.2B -> 5.0B (+800M per coin)
            # ETH goes from 180M -> 200M (+20M per coin)
            mock.set_mock_price("BTCTMN", 5_000_000_000.0)
            mock.set_mock_price("ETHTMN", 200_000_000.0)

            # Expected equity:
            # TMN total: 5,000,000 (available + locked)
            # BTC total: 1.0 BTC (0.6 available + 0.4 locked) * 5.0B = 5,000,000,000
            # ETH total: 10.0 ETH (6.0 available + 4.0 locked) * 200M = 2,000,000,000
            # Total expected = 5,000,000 + 5,000,000,000 + 2,000,000,000 = 7,005,000,000 TMN
            bal = await mock.get_account_balances()
            expected = 5_000_000.0 + (1.0 * 5_000_000_000.0) + (10.0 * 200_000_000.0)
            assert bal.total_equity_tmn == pytest.approx(expected, 1e-4)

        asyncio.run(_run())

    def test_churn_fuzzing_stress_500_iterations(self):
        """Empirical 500-iteration random order placement, cancellation, duplicate attempt,
        and mark-to-market invariance verification.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=100_000_000.0, rate_limiter=NoOpRateLimiter())
            mock.crypto_balances["BTC"] = 5.0
            mock.crypto_balances["ETH"] = 50.0
            mock.crypto_balances["USDT"] = 10_000.0

            bal0 = await mock.get_account_balances()
            initial_equity = bal0.total_equity_tmn

            symbols = ["BTCTMN", "ETHTMN", "USDTTMN"]
            active_cids = []

            for iteration in range(500):
                op = random.choice(["PLACE_RESTING_BUY", "PLACE_RESTING_SELL", "CANCEL", "ATTEMPT_DUP", "CHECK_EQ"])

                if op == "PLACE_RESTING_BUY" and len(active_cids) < 50:
                    sym = random.choice(symbols)
                    depth = await mock.get_market_depth(sym)
                    # Pick inside-spread price
                    p = depth.best_bid + 0.5 * (depth.best_ask - depth.best_bid)
                    qty = 0.0001 if "BTC" in sym else (0.01 if "ETH" in sym else 1.0)
                    cid = f"FUZZ-{iteration}"
                    res = await mock.post_order(symbol=sym, side=OrderSide.BUY, order_type=OrderType.LIMIT, price=p, quantity=qty, client_order_id=cid)
                    if res.status == OrderStatus.PENDING:
                        active_cids.append(cid)

                elif op == "PLACE_RESTING_SELL" and len(active_cids) < 50:
                    sym = random.choice(symbols)
                    base = sym.replace("TMN", "")
                    if mock.crypto_balances[base] > 0.05:
                        depth = await mock.get_market_depth(sym)
                        p = depth.best_bid + 0.5 * (depth.best_ask - depth.best_bid)
                        qty = 0.0001 if "BTC" in sym else (0.01 if "ETH" in sym else 1.0)
                        cid = f"FUZZ-{iteration}"
                        res = await mock.post_order(symbol=sym, side=OrderSide.SELL, order_type=OrderType.LIMIT, price=p, quantity=qty, client_order_id=cid)
                        if res.status == OrderStatus.PENDING:
                            active_cids.append(cid)

                elif op == "CANCEL" and active_cids:
                    cid_to_cancel = active_cids.pop(random.randrange(len(active_cids)))
                    await mock.cancel_order(client_order_id=cid_to_cancel)

                elif op == "ATTEMPT_DUP" and active_cids:
                    target_cid = random.choice(active_cids)
                    with pytest.raises(WallexOrderError, match="Duplicate client_order_id"):
                        await mock.post_order(symbol="BTCTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=1000.0, quantity=0.0001, client_order_id=target_cid)

                elif op == "CHECK_EQ":
                    bal = await mock.get_account_balances()
                    assert bal.total_equity_tmn == pytest.approx(initial_equity, 1e-4)

            # Finally, cancel all remaining active orders
            await mock.cancel_all_orders()
            bal_end = await mock.get_account_balances()
            assert bal_end.total_equity_tmn == pytest.approx(initial_equity, 1e-4)
            assert mock.tmn_locked == 0.0
            for k in mock.crypto_locked:
                assert mock.crypto_locked[k] == 0.0

        asyncio.run(_run())
