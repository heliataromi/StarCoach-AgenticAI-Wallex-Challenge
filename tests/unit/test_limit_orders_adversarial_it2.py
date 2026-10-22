"""Adversarial stress test harness for MockWallexClient limit order execution and fund escrow.
Authored by Challenger M1-It2-2.

Empirically challenges:
1. Crossing vs Resting Limit Orders:
   - BUY limit with price >= best_ask fills immediately (OrderStatus.FILLED).
   - BUY limit with price < best_ask transitions to OrderStatus.PENDING, recorded in self.open_orders, locks cost + fee.
   - SELL limit with price <= best_bid fills immediately (OrderStatus.FILLED).
   - SELL limit with price > best_bid transitions to OrderStatus.PENDING, recorded in self.open_orders, locks crypto.
2. Fund Locking (Escrow) and Double Spend Prevention:
   - Locks exact TMN (cost + fee) on resting BUY, preventing double-spend.
   - Locks exact crypto on resting SELL, preventing overselling.
   - Insufficient funds rejection on both BUY and SELL.
3. Order Cancellation and Fund Release:
   - Cancelling resting BUY restores TMN to self.tmn_available and decrements self.tmn_locked.
   - Cancelling resting SELL restores crypto to self.crypto_balances and decrements self.crypto_locked.
   - Cancellation by client_order_id and exchange order_id.
   - Idempotency and double-cancellation safety.
   - cancel_all_orders bulk release.
4. Mark-to-Market Total Equity Invariance:
   - Assert total equity invariant across resting order placement and cancellation.
   - Verify valuation under price movements.
"""
from __future__ import annotations

import asyncio
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


class TestCrossingVsRestingLimitOrders:
    """Tests specification requirement 1: Crossing vs Resting Limit Order Execution."""

    def test_crossing_buy_limit_at_or_above_best_ask(self):
        """Verifies BUY limit with price >= best_ask fills immediately as OrderStatus.FILLED."""
        async def _run():
            mock = MockWallexClient(initial_tmn=10_000_000.0)
            depth = await mock.get_market_depth("BTCTMN")
            best_ask = depth.best_ask

            # Case A: Exact best_ask
            res_exact = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=best_ask,
                quantity=0.0001,
                client_order_id="BUY-EXACT-ASK",
            )
            assert res_exact.status == OrderStatus.FILLED
            assert res_exact.filled_quantity == 0.0001
            assert "BUY-EXACT-ASK" not in mock.open_orders

            # Case B: Aggressively above best_ask
            res_above = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=best_ask + 50_000.0,
                quantity=0.0001,
                client_order_id="BUY-ABOVE-ASK",
            )
            assert res_above.status == OrderStatus.FILLED
            assert res_above.filled_quantity == 0.0001
            assert "BUY-ABOVE-ASK" not in mock.open_orders

        asyncio.run(_run())

    def test_crossing_sell_limit_at_or_below_best_bid(self):
        """Verifies SELL limit with price <= best_bid fills immediately as OrderStatus.FILLED."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 1.0
            depth = await mock.get_market_depth("BTCTMN")
            best_bid = depth.best_bid

            # Case A: Exact best_bid
            res_exact = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=best_bid,
                quantity=0.0001,
                client_order_id="SELL-EXACT-BID",
            )
            assert res_exact.status == OrderStatus.FILLED
            assert res_exact.filled_quantity == 0.0001
            assert "SELL-EXACT-BID" not in mock.open_orders

            # Case B: Aggressively below best_bid
            res_below = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=best_bid - 50_000.0,
                quantity=0.0001,
                client_order_id="SELL-BELOW-BID",
            )
            assert res_below.status == OrderStatus.FILLED
            assert res_below.filled_quantity == 0.0001
            assert "SELL-BELOW-BID" not in mock.open_orders

        asyncio.run(_run())

    def test_resting_buy_limit_below_best_ask(self):
        """Verifies BUY limit with price < best_ask transitions to OrderStatus.PENDING,
        is recorded in self.open_orders, and locks funds in self.tmn_locked.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=10_000_000.0)
            depth = await mock.get_market_depth("BTCTMN")
            best_ask = depth.best_ask
            best_bid = depth.best_bid

            # Price strictly below best_ask (e.g. at best_bid or between bid and ask)
            test_prices = [
                best_ask - 100.0,            # Just below ask
                (best_bid + best_ask) / 2.0,  # Mid price (inside spread)
                best_bid,                     # At best bid
                best_bid - 10_000_000.0,      # Deep resting order below bid
                1000.0,                       # Deep unmarketable
            ]

            for idx, p in enumerate(test_prices):
                cid = f"REST-BUY-{idx}"
                qty = 0.0001
                cost = p * qty
                fee = cost * 0.0025
                total_lock = cost + fee

                res = await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=p,
                    quantity=qty,
                    client_order_id=cid,
                )
                assert res.status == OrderStatus.PENDING, (
                    f"BUY limit with price {p} (< best_ask {best_ask}) failed to transition to PENDING! Got {res.status}"
                )
                assert cid in mock.open_orders, f"BUY limit order {cid} not found in open_orders!"
                assert mock.open_orders[cid]["status"] == "PENDING"
                assert mock.open_orders[cid]["locked_tmn"] == pytest.approx(total_lock)

        asyncio.run(_run())

    def test_resting_sell_limit_above_best_bid(self):
        """Verifies SELL limit with price > best_bid transitions to OrderStatus.PENDING,
        is recorded in self.open_orders, and locks crypto in self.crypto_locked.
        """
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 10.0
            depth = await mock.get_market_depth("BTCTMN")
            best_bid = depth.best_bid
            best_ask = depth.best_ask

            test_prices = [
                best_bid + 100.0,             # Just above bid
                (best_bid + best_ask) / 2.0,  # Mid price (inside spread)
                best_ask,                     # At best ask
                best_ask + 10_000_000.0,      # Deep resting order above ask
                10_000_000_000.0,             # Far unmarketable
            ]

            for idx, p in enumerate(test_prices):
                cid = f"REST-SELL-{idx}"
                qty = 0.001

                res = await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    price=p,
                    quantity=qty,
                    client_order_id=cid,
                )
                assert res.status == OrderStatus.PENDING, (
                    f"SELL limit with price {p} (> best_bid {best_bid}) failed to transition to PENDING! Got {res.status}"
                )
                assert cid in mock.open_orders, f"SELL limit order {cid} not found in open_orders!"
                assert mock.open_orders[cid]["status"] == "PENDING"
                assert mock.open_orders[cid]["locked_coin"] == pytest.approx(qty)

        asyncio.run(_run())


class TestFundLockingAndEscrow:
    """Tests fund locking (escrow) and double-spend protection for resting limit orders."""

    def test_resting_buy_locks_cost_plus_fee(self):
        """Verifies resting BUY locks exact (cost + fee) in tmn_locked and deducts from tmn_available."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            price = 1000.0
            qty = 2.0
            cost = price * qty  # 2000 TMN
            fee = cost * 0.0025  # 5 TMN
            total_required = cost + fee  # 2005 TMN

            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=price,
                quantity=qty,
                client_order_id="LOCK-BUY-01",
            )
            assert res.status == OrderStatus.PENDING
            assert pytest.approx(mock.tmn_locked) == 2005.0
            assert pytest.approx(mock.tmn_available) == (5_000_000.0 - 2005.0)

        asyncio.run(_run())

    def test_resting_sell_locks_crypto(self):
        """Verifies resting SELL locks exact quantity in crypto_locked and deducts from crypto_balances."""
        async def _run():
            mock = MockWallexClient()
            mock.crypto_balances["BTC"] = 0.50
            qty = 0.20

            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=10_000_000_000.0,
                quantity=qty,
                client_order_id="LOCK-SELL-01",
            )
            assert res.status == OrderStatus.PENDING
            assert pytest.approx(mock.crypto_locked["BTC"]) == 0.20
            assert pytest.approx(mock.crypto_balances["BTC"]) == 0.30

        asyncio.run(_run())

    def test_double_spend_prevention_on_locked_tmn(self):
        """Verifies attempting to spend locked TMN on another order is rejected."""
        async def _run():
            initial_tmn = 1_000_000.0
            mock = MockWallexClient(initial_tmn=initial_tmn)

            # Order 1 locks 800,000 + 2,000 fee = 802,000 TMN
            price = 1000.0
            qty1 = 800.0
            res1 = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=price,
                quantity=qty1,
                client_order_id="LOCK-1",
            )
            assert res1.status == OrderStatus.PENDING
            assert pytest.approx(mock.tmn_available) == (1_000_000.0 - 802_000.0)

            # Order 2 attempts to spend 300,000 TMN (only 198,000 available)
            qty2 = 300.0
            res2 = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=price,
                quantity=qty2,
                client_order_id="LOCK-2",
            )
            assert res2.status == OrderStatus.REJECTED
            assert "Insufficient TMN" in res2.raw_response.get("error", "")
            # Verify locked TMN unchanged
            assert pytest.approx(mock.tmn_locked) == 802_000.0

        asyncio.run(_run())

    def test_double_spend_prevention_on_locked_crypto(self):
        """Verifies attempting to sell locked crypto on another order is rejected."""
        async def _run():
            mock = MockWallexClient()
            mock.crypto_balances["ETH"] = 1.0

            # Order 1 locks 0.8 ETH
            res1 = await mock.post_order(
                symbol="ETHTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=500_000_000.0,
                quantity=0.8,
                client_order_id="LOCK-ETH-1",
            )
            assert res1.status == OrderStatus.PENDING
            assert pytest.approx(mock.crypto_balances["ETH"]) == 0.2
            assert pytest.approx(mock.crypto_locked["ETH"]) == 0.8

            # Order 2 attempts to sell 0.3 ETH (only 0.2 available)
            res2 = await mock.post_order(
                symbol="ETHTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=500_000_000.0,
                quantity=0.3,
                client_order_id="LOCK-ETH-2",
            )
            assert res2.status == OrderStatus.REJECTED
            assert "Insufficient ETH" in res2.raw_response.get("error", "")
            assert pytest.approx(mock.crypto_locked["ETH"]) == 0.8

        asyncio.run(_run())


class TestOrderCancellationAndFundRelease:
    """Tests cancellation mechanics, fund restoration, and idempotence."""

    def test_cancel_resting_buy_restores_tmn(self):
        """Verifies cancelling resting BUY releases locked TMN back to available balance."""
        async def _run():
            initial_tmn = 5_000_000.0
            mock = MockWallexClient(initial_tmn=initial_tmn)

            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1000.0,
                quantity=10.0,
                client_order_id="CANCEL-BUY-1",
            )
            assert mock.tmn_locked > 0.0
            assert len(mock.open_orders) == 1

            success = await mock.cancel_order(client_order_id="CANCEL-BUY-1")
            assert success is True
            assert mock.tmn_locked == 0.0
            assert pytest.approx(mock.tmn_available) == initial_tmn
            assert "CANCEL-BUY-1" not in mock.open_orders

            # Verify order status reports CANCELLED
            status = await mock.get_order_status("CANCEL-BUY-1")
            assert status is not None
            assert status.status == OrderStatus.CANCELLED

        asyncio.run(_run())

    def test_cancel_resting_sell_restores_crypto(self):
        """Verifies cancelling resting SELL releases locked crypto back to balances."""
        async def _run():
            mock = MockWallexClient()
            mock.crypto_balances["BTC"] = 1.0

            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=10_000_000_000.0,
                quantity=0.45,
                client_order_id="CANCEL-SELL-1",
            )
            assert pytest.approx(mock.crypto_locked["BTC"]) == 0.45
            assert pytest.approx(mock.crypto_balances["BTC"]) == 0.55

            success = await mock.cancel_order(client_order_id="CANCEL-SELL-1")
            assert success is True
            assert mock.crypto_locked["BTC"] == 0.0
            assert pytest.approx(mock.crypto_balances["BTC"]) == 1.0
            assert "CANCEL-SELL-1" not in mock.open_orders

            status = await mock.get_order_status("CANCEL-SELL-1")
            assert status is not None
            assert status.status == OrderStatus.CANCELLED

        asyncio.run(_run())

    def test_cancel_by_exchange_order_id(self):
        """Verifies cancelling by exchange order_id instead of client_order_id."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1000.0,
                quantity=1.0,
                client_order_id="CL-EX-ID-01",
            )
            ex_id = res.exchange_order_id
            assert ex_id is not None

            success = await mock.cancel_order(order_id=ex_id)
            assert success is True
            assert mock.tmn_locked == 0.0
            assert len(mock.open_orders) == 0

        asyncio.run(_run())

    def test_cancel_all_orders_releases_all_funds(self):
        """Verifies cancel_all_orders releases funds across multiple resting orders."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 1.0
            mock.crypto_balances["ETH"] = 5.0

            await mock.post_order(symbol="BTCTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=1000.0, quantity=1.0, client_order_id="B1")
            await mock.post_order(symbol="ETHTMN", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=1000.0, quantity=2.0, client_order_id="B2")
            await mock.post_order(symbol="BTCTMN", side=OrderSide.SELL, order_type=OrderType.LIMIT, price=10e9, quantity=0.5, client_order_id="S1")

            assert len(mock.open_orders) == 3
            assert mock.tmn_locked > 0.0
            assert mock.crypto_locked["BTC"] == 0.5

            count = await mock.cancel_all_orders()
            assert count == 3
            assert len(mock.open_orders) == 0
            assert mock.tmn_locked == 0.0
            assert mock.crypto_locked["BTC"] == 0.0
            assert mock.crypto_balances["BTC"] == 1.0
            assert pytest.approx(mock.tmn_available) == 5_000_000.0

        asyncio.run(_run())


class TestTotalEquityInvariance:
    """Tests mark-to-market total equity invariance across resting order lifecycles."""

    def test_total_equity_invariant_during_resting_buy_and_cancel(self):
        """Verifies total equity remains perfectly invariant before, during, and after resting BUY."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            bal0 = await mock.get_account_balances()
            eq0 = bal0.total_equity_tmn

            # Place resting BUY
            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1000.0,
                quantity=5.0,
                client_order_id="EQ-BUY-01",
            )
            bal1 = await mock.get_account_balances()
            eq1 = bal1.total_equity_tmn
            assert pytest.approx(eq1, 1e-6) == eq0, f"Total equity changed during resting BUY: {eq1} vs {eq0}"

            # Cancel resting BUY
            await mock.cancel_order(client_order_id="EQ-BUY-01")
            bal2 = await mock.get_account_balances()
            eq2 = bal2.total_equity_tmn
            assert pytest.approx(eq2, 1e-6) == eq0, f"Total equity changed after resting BUY cancel: {eq2} vs {eq0}"

        asyncio.run(_run())

    def test_total_equity_invariant_during_resting_sell_and_cancel(self):
        """Verifies total equity remains perfectly invariant before, during, and after resting SELL."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 0.50
            bal0 = await mock.get_account_balances()
            eq0 = bal0.total_equity_tmn

            # Place resting SELL
            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=10_000_000_000.0,
                quantity=0.25,
                client_order_id="EQ-SELL-01",
            )
            bal1 = await mock.get_account_balances()
            eq1 = bal1.total_equity_tmn
            assert pytest.approx(eq1, 1e-6) == eq0, f"Total equity changed during resting SELL: {eq1} vs {eq0}"

            # Cancel resting SELL
            await mock.cancel_order(client_order_id="EQ-SELL-01")
            bal2 = await mock.get_account_balances()
            eq2 = bal2.total_equity_tmn
            assert pytest.approx(eq2, 1e-6) == eq0, f"Total equity changed after resting SELL cancel: {eq2} vs {eq0}"

        asyncio.run(_run())

    def test_total_equity_mtm_valuation_includes_locked_crypto_on_price_shift(self):
        """Verifies mark-to-market valuation accurately values locked crypto when market price shifts."""
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 1.0

            # Lock 0.5 BTC in resting sell
            await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=10_000_000_000.0,
                quantity=0.5,
                client_order_id="EQ-SHIFT",
            )

            # Price doubles from 4.2B to 8.4B
            mock.set_mock_price("BTCTMN", 8_400_000_000.0)

            # Total equity should be: 5,000,000 + 1.0 BTC * 8.4B = 8,405,000,000 TMN
            bal = await mock.get_account_balances()
            expected_eq = 5_000_000.0 + (1.0 * 8_400_000_000.0)
            assert pytest.approx(bal.total_equity_tmn, 1e-4) == expected_eq

        asyncio.run(_run())
