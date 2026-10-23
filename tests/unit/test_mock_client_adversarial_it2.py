"""Adversarial stress test harness for MockWallexClient.post_order input validation.
Authored by Challenger M1-It2-1.

Empirically challenges:
1. Negative quantities on BUY and SELL (verifying WallexOrderError and zero balance fabrication).
2. Zero quantities (0.0 and -0.0) on BUY and SELL (verifying WallexOrderError).
3. Negative prices and zero limit prices (verifying WallexOrderError).
4. NaN and Inf quantities and prices (verifying WallexOrderError).
5. Market order execution price resolution from top-of-book depth (asks[0][0] for BUY, bids[0][0] for SELL).
6. Pathological inputs: empty book fallback, unknown symbol resolution failure, invalid side/type/symbol.
"""
from __future__ import annotations

import asyncio
import math
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


class TestAdversarialNegativeQuantities:
    """Stress tests negative quantities across all order sides, types, and parameter formats."""

    @pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL, "BUY", "SELL", "buy", "sell"])
    @pytest.mark.parametrize("order_type", [OrderType.LIMIT, OrderType.MARKET, "LIMIT", "MARKET"])
    @pytest.mark.parametrize("bad_qty", [-0.0001, -1.0, -1e-8, -1e-18, -1e10, float("-inf")])
    def test_negative_quantity_raises_order_error_and_preserves_balances(self, side, order_type, bad_qty):
        """Verifies that any negative quantity raises WallexOrderError and fabricates NO balance or crypto."""
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        mock.crypto_balances["BTC"] = 0.50
        initial_tmn_avail = mock.tmn_available
        initial_tmn_locked = mock.tmn_locked
        initial_btc = mock.crypto_balances["BTC"]
        initial_btc_locked = mock.crypto_locked["BTC"]
        initial_exec_count = len(mock.executed_orders)
        initial_open_count = len(mock.open_orders)

        price = 4_200_000_000.0

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=side,
                    order_type=order_type,
                    price=price,
                    quantity=bad_qty,
                    client_order_id="ADVERSARIAL-NEG-QTY",
                )

        asyncio.run(_run())

        # Assert absolute balance and ledger invariants: NO MONEY OR CRYPTO FABRICATED
        assert mock.tmn_available == initial_tmn_avail
        assert mock.tmn_locked == initial_tmn_locked
        assert mock.crypto_balances["BTC"] == initial_btc
        assert mock.crypto_locked["BTC"] == initial_btc_locked
        assert len(mock.executed_orders) == initial_exec_count
        assert len(mock.open_orders) == initial_open_count

    def test_negative_quantity_via_order_request_dataclass(self):
        """Verifies negative quantity wrapped in OrderRequest dataclass raises WallexOrderError."""
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        req = OrderRequest(
            client_order_id="REQ-NEG-QTY",
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=0.0,
            quantity=-0.005,
        )

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order(order=req)

        asyncio.run(_run())
        assert mock.tmn_available == 5_000_000.0
        assert mock.crypto_balances["BTC"] == 0.0


class TestAdversarialZeroQuantities:
    """Stress tests zero quantities (0.0, -0.0) across all order sides and routing types."""

    @pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
    @pytest.mark.parametrize("order_type", [OrderType.LIMIT, OrderType.MARKET])
    @pytest.mark.parametrize("zero_qty", [0.0, -0.0, 0])
    def test_zero_quantity_raises_order_error(self, side, order_type, zero_qty):
        """Verifies that quantity <= 0 strictly raises WallexOrderError."""
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        initial_tmn = mock.tmn_available

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=side,
                    order_type=order_type,
                    price=4_200_000_000.0,
                    quantity=zero_qty,
                    client_order_id="ZERO-QTY-ORD",
                )

        asyncio.run(_run())
        assert mock.tmn_available == initial_tmn
        assert len(mock.open_orders) == 0
        assert len(mock.executed_orders) == 0

    def test_zero_quantity_via_order_request(self):
        """Verifies OrderRequest with quantity=0.0 is rejected."""
        mock = MockWallexClient()
        req = OrderRequest(
            client_order_id="REQ-ZERO",
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=4_200_000_000.0,
            quantity=0.0,
        )

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order(order=req)

        asyncio.run(_run())


class TestAdversarialNegativeAndZeroPrices:
    """Stress tests negative prices and zero limit prices."""

    @pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
    @pytest.mark.parametrize("bad_price", [-0.001, -500.0, -1e9, float("-inf")])
    def test_negative_prices_raise_order_error(self, side, bad_price):
        """Verifies negative prices raise WallexOrderError on both LIMIT and MARKET."""
        mock = MockWallexClient()

        async def _run_limit():
            with pytest.raises(WallexOrderError, match="Invalid order price"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=side,
                    order_type=OrderType.LIMIT,
                    price=bad_price,
                    quantity=0.001,
                )

        async def _run_market():
            with pytest.raises(WallexOrderError, match="Invalid order price"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=side,
                    order_type=OrderType.MARKET,
                    price=bad_price,
                    quantity=0.001,
                )

        asyncio.run(_run_limit())
        asyncio.run(_run_market())

    @pytest.mark.parametrize("zero_price", [0.0, -0.0, 0])
    @pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
    def test_zero_limit_price_raises_order_error(self, zero_price, side):
        """Verifies limit orders with price <= 0 raise WallexOrderError."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid limit order price"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=side,
                    order_type=OrderType.LIMIT,
                    price=zero_price,
                    quantity=0.001,
                )

        asyncio.run(_run())

    def test_limit_order_with_unspecified_price_raises_order_error(self):
        """Verifies limit order with price=None defaults to 0.0 and raises WallexOrderError."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid limit order price"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=None,
                    quantity=0.001,
                )

        asyncio.run(_run())


class TestAdversarialNaNAndInf:
    """Stress tests IEEE 754 floating point singularities (NaN, +Inf, -Inf)."""

    @pytest.mark.parametrize("bad_val", [float("nan"), float("inf"), float("-inf")])
    def test_nan_and_inf_quantity_rejected(self, bad_val):
        """Verifies NaN and Inf quantities raise WallexOrderError."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=4_200_000_000.0,
                    quantity=bad_val,
                )

        asyncio.run(_run())

    @pytest.mark.parametrize("bad_val", [float("nan"), float("inf"), float("-inf")])
    def test_nan_and_inf_price_rejected(self, bad_val):
        """Verifies NaN and Inf prices raise WallexOrderError."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order price"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=bad_val,
                    quantity=0.001,
                )

        asyncio.run(_run())

    def test_nan_via_order_request_dataclass(self):
        """Verifies OrderRequest carrying NaN is rejected at post_order entry."""
        mock = MockWallexClient()
        req_nan_qty = OrderRequest(
            client_order_id="NAN-QTY",
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            price=0.0,
            quantity=float("nan"),
        )
        req_nan_price = OrderRequest(
            client_order_id="NAN-PRICE",
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=float("nan"),
            quantity=0.001,
        )

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order(order=req_nan_qty)
            with pytest.raises(WallexOrderError, match="Invalid order price"):
                await mock.post_order(order=req_nan_price)

        asyncio.run(_run())


class TestMarketOrderTopDepthResolution:
    """Stress tests MARKET order execution price resolution from top-of-book depth."""

    def test_buy_market_order_resolves_from_asks_top(self):
        """Verifies BUY MARKET order without price resolves to asks[0][0]."""
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        expected_top_ask = mock.orderbooks["BTCTMN"]["asks"][0][0]
        assert expected_top_ask > 4_200_000_000.0

        async def _run():
            # Price unspecified (None)
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.0001,
                client_order_id="MKT-BUY-NONE",
            )
            assert res.status == OrderStatus.FILLED
            assert res.average_fill_price == expected_top_ask
            assert res.filled_quantity == 0.0001
            expected_fee = (expected_top_ask * 0.0001) * 0.0025
            assert pytest.approx(res.fee_paid_tmn, rel=1e-5) == expected_fee

            # Price explicitly 0.0
            res2 = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=0.0,
                quantity=0.0001,
                client_order_id="MKT-BUY-ZERO",
            )
            assert res2.status == OrderStatus.FILLED
            assert res2.average_fill_price == expected_top_ask

        asyncio.run(_run())

    def test_sell_market_order_resolves_from_bids_top(self):
        """Verifies SELL MARKET order without price resolves to bids[0][0]."""
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        mock.crypto_balances["BTC"] = 0.001
        expected_top_bid = mock.orderbooks["BTCTMN"]["bids"][0][0]
        assert expected_top_bid < 4_200_000_000.0

        async def _run():
            # Price unspecified (None)
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=0.0001,
                client_order_id="MKT-SELL-NONE",
            )
            assert res.status == OrderStatus.FILLED
            assert res.average_fill_price == expected_top_bid
            assert res.filled_quantity == 0.0001

            # Price explicitly 0.0
            res2 = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                price=0.0,
                quantity=0.0001,
                client_order_id="MKT-SELL-ZERO",
            )
            assert res2.status == OrderStatus.FILLED
            assert res2.average_fill_price == expected_top_bid

        asyncio.run(_run())

    def test_custom_orderbook_resolution(self):
        """Verifies top-of-book resolution under custom synthetic orderbooks."""
        mock = MockWallexClient(initial_tmn=10_000_000.0)
        mock.crypto_balances["ETH"] = 1.0

        custom_bids = [(175_000_000.0, 2.0), (174_000_000.0, 5.0)]
        custom_asks = [(185_000_000.0, 1.5), (186_000_000.0, 3.0)]
        mock.set_orderbook("ETHTMN", bids=custom_bids, asks=custom_asks)

        async def _run():
            # BUY must hit 185,000,000.0
            buy_res = await mock.post_order(
                symbol="ETHTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.01,
            )
            assert buy_res.status == OrderStatus.FILLED
            assert buy_res.average_fill_price == 185_000_000.0

            # SELL must hit 175,000,000.0
            sell_res = await mock.post_order(
                symbol="ETHTMN",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=0.01,
            )
            assert sell_res.status == OrderStatus.FILLED
            assert sell_res.average_fill_price == 175_000_000.0

        asyncio.run(_run())

    def test_fallback_to_mock_prices_when_book_empty(self):
        """Verifies market order falls back to mock_prices if orderbook is empty."""
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        mock.orderbooks["BTCTMN"] = {"bids": [], "asks": []}
        mock.mock_prices["BTCTMN"] = 4_150_000_000.0

        async def _run():
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.0001,
            )
            assert res.status == OrderStatus.FILLED
            assert res.average_fill_price == 4_150_000_000.0

        asyncio.run(_run())

    def test_unresolvable_market_price_raises_order_error(self):
        """Verifies WallexOrderError is raised if neither orderbook nor mock_prices can provide a price."""
        mock = MockWallexClient()
        mock.orderbooks["BTCTMN"] = {"bids": [], "asks": []}
        mock.mock_prices["BTCTMN"] = 0.0

        async def _run():
            with pytest.raises(WallexOrderError, match="Cannot resolve market execution price"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=0.0001,
                )

        asyncio.run(_run())


class TestPathologicalInputsAndTypes:
    """Stress tests boundary inputs: invalid side, invalid order_type, invalid symbol."""

    @pytest.mark.parametrize("bad_side", ["HOLD", "SHORT", "BID", "ASK", "INVALID", "", 123])
    def test_invalid_order_side_rejected(self, bad_side):
        """Verifies only BUY and SELL are accepted."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order side"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=bad_side,
                    order_type=OrderType.LIMIT,
                    price=4.2e9,
                    quantity=0.001,
                )

        asyncio.run(_run())

    @pytest.mark.parametrize("bad_type", ["STOP", "TRAILING", "ICEBERG", "FOO", ""])
    def test_invalid_order_type_rejected(self, bad_type):
        """Verifies only LIMIT, MARKET, STOP_LIMIT are accepted."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order type"):
                await mock.post_order(
                    symbol="BTCTMN",
                    side=OrderSide.BUY,
                    order_type=bad_type,
                    price=4.2e9,
                    quantity=0.001,
                )

        asyncio.run(_run())

    def test_empty_symbol_rejected(self):
        """Verifies empty string symbol is rejected with WallexOrderError."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order symbol"):
                await mock.post_order(
                    symbol="",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=0.001,
                )

        asyncio.run(_run())

    def test_order_request_none_symbol_rejected(self):
        """Verifies OrderRequest with symbol=None is rejected with WallexOrderError."""
        mock = MockWallexClient()
        req = OrderRequest(
            client_order_id="NONE-SYM",
            symbol=None,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=1000.0,
            quantity=0.001,
        )

        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order symbol"):
                await mock.post_order(order=req)

        asyncio.run(_run())

    def test_kwarg_none_symbol_market_order_rejected(self):
        """Verifies kwarg symbol=None on MARKET order fails at market execution price resolution."""
        mock = MockWallexClient()

        async def _run():
            with pytest.raises(WallexOrderError, match="Cannot resolve market execution price"):
                await mock.post_order(
                    symbol=None,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=0.001,
                )

        asyncio.run(_run())

