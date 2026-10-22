"""Unit tests for WallexClient, MockWallexClient, and BaseWallexClient.
Uses native httpx.MockTransport for 100% deterministic, offline verification
without network calls or external live credentials.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import httpx
import pytest

from src.wallex.client import BaseWallexClient, WallexClient
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
    WallexAPIError,
    WallexAuthError,
    WallexClientError,
    WallexNetworkError,
    WallexOrderError,
    WallexRateLimitError,
)
from src.wallex.rate_limiter import TokenBucketRateLimiter


# ==============================================================================
# In-Memory Mock Transport Router
# ==============================================================================

class MockTransportRouter:
    """Configurable in-memory mock transport for Wallex JSON-RPC 2.0 MCP calls."""

    def __init__(self) -> None:
        self.tool_routes: Dict[str, Any] = {}
        self.call_history: List[httpx.Request] = []
        self.simulate_status: Optional[int] = None
        self.simulate_headers: Dict[str, str] = {}
        self.fail_count: int = 0
        self.succeed_after: int = 0
        self.raise_connect_error: bool = False
        self.connect_error_after: int = 0
        self.connect_error_count: int = 0

    def set_tool(self, tool_name: str, result_data: Any) -> None:
        self.tool_routes[tool_name] = result_data

    def simulate_transient_status(self, status: int, succeed_after: int, headers: Optional[Dict[str, str]] = None) -> None:
        self.simulate_status = status
        self.succeed_after = succeed_after
        self.simulate_headers = headers or {}
        self.fail_count = 0

    def simulate_transient_connect_error(self, succeed_after: int) -> None:
        self.raise_connect_error = True
        self.connect_error_after = succeed_after
        self.connect_error_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.call_history.append(request)

        # Transient network connect error simulation
        if self.raise_connect_error and self.connect_error_count < self.connect_error_after:
            self.connect_error_count += 1
            raise httpx.ConnectError("Simulated connection dropped", request=request)

        # Transient HTTP status simulation (e.g. 429)
        if self.simulate_status and self.fail_count < self.succeed_after:
            self.fail_count += 1
            return httpx.Response(
                self.simulate_status,
                headers=self.simulate_headers,
                json={"error": "Simulated transient failure"},
                request=request,
            )

        # JSON-RPC 2.0 MCP routing
        try:
            body = json.loads(request.content.decode("utf-8"))
        except Exception:
            return httpx.Response(400, text="Invalid JSON", request=request)

        tool_name = body.get("params", {}).get("name")
        rpc_id = body.get("id", 1)

        if tool_name in self.tool_routes:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": self.tool_routes[tool_name],
                },
                request=request,
            )

        return httpx.Response(
            400,
            json={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Method '{tool_name}' not found"},
            },
            request=request,
        )


# ==============================================================================
# Subsystem B: Production WallexClient Tests (MockTransport)
# ==============================================================================

def test_wallex_client_jsonrpc_serialization():
    router = MockTransportRouter()
    router.set_tool("wallex_basic_get_market_depth", {
        "bids": [[4_200_000_000.0, 0.5]],
        "asks": [[4_210_000_000.0, 0.4]],
        "timestamp": 1700000000000.0,
    })
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(api_key="test_secret_key_1234", transport=transport)

    async def _test():
        snapshot = await client.get_market_depth("BTCTMN")
        assert len(router.call_history) == 1
        req = router.call_history[0]
        assert req.url.path == "/mcp"
        assert req.method == "POST"

        body = json.loads(req.content.decode("utf-8"))
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "wallex_basic_get_market_depth"
        assert body["params"]["arguments"] == {"symbol": "BTCTMN"}

    asyncio.run(_test())


def test_wallex_client_auth_headers_and_security():
    router = MockTransportRouter()
    router.set_tool("wallex_basic_get_markets", [{"symbol": "BTCTMN"}])
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(api_key="wallex_super_secret_token_9999", transport=transport)

    # Verify secret masking in repr
    client_repr = repr(client)
    assert "9999" in client_repr
    assert "super_secret" not in client_repr

    async def _test():
        await client.get_markets()
        assert len(router.call_history) == 1
        req = router.call_history[0]
        assert req.headers.get("Authorization") == "Bearer wallex_super_secret_token_9999"
        assert req.headers.get("x-api-key") == "wallex_super_secret_token_9999"

    asyncio.run(_test())


def test_wallex_client_get_market_depth_and_snapshot_parsing():
    router = MockTransportRouter()
    # 2 bids: 4200M (vol 0.6), 4190M (vol 1.4) -> Total Bid Vol = 2.0
    # 2 asks: 4210M (vol 0.4), 4220M (vol 0.6) -> Total Ask Vol = 1.0
    now_ms = time.time() * 1000.0
    router.set_tool("wallex_basic_get_market_depth", {
        "bids": [[4_200_000_000.0, 0.6], [4_190_000_000.0, 1.4]],
        "asks": [[4_210_000_000.0, 0.4], [4_220_000_000.0, 0.6]],
        "timestamp": now_ms,
    })
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport)

    async def _test():
        snapshot = await client.get_market_depth("BTCTMN")
        assert isinstance(snapshot, MarketDataSnapshot)
        assert snapshot.symbol == "BTCTMN"
        assert snapshot.best_bid == 4_200_000_000.0
        assert snapshot.best_ask == 4_210_000_000.0
        # Spread % = (4210 - 4200) / 4200 = 10 / 4200 = 0.00238095...
        expected_spread = (4_210_000_000.0 - 4_200_000_000.0) / 4_200_000_000.0
        assert pytest.approx(snapshot.spread_pct, 1e-6) == expected_spread
        # Imbalance = (2.0 - 1.0) / (2.0 + 1.0) = 1.0 / 3.0 = 0.333333...
        assert pytest.approx(snapshot.imbalance_ratio, 1e-4) == (1.0 / 3.0)
        assert snapshot.is_stale is False

    asyncio.run(_test())


def test_wallex_client_get_candles_parsing():
    router = MockTransportRouter()
    mock_candles = [
        {"timestamp": 1700000000000, "open": 4200000000, "high": 4250000000, "low": 4180000000, "close": 4220000000, "volume": 12.5}
    ]
    router.set_tool("wallex_basic_candle_history", {"candles": mock_candles})
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport)

    async def _test():
        candles = await client.get_candles("BTCTMN", resolution="15")
        assert len(candles) == 1
        assert candles[0]["open"] == 4200000000
        assert candles[0]["volume"] == 12.5

    asyncio.run(_test())


def test_wallex_client_get_account_balances():
    router = MockTransportRouter()
    router.set_tool("wallex_credit_sub_account_balances_details", {
        "available": 4_850_000.0,
        "locked": 150_000.0,
        "assets": {"BTC": 0.00025, "ETH": 0.0},
        "total_equity": 5_900_000.0,
        "timestamp": 1700000500.0,
    })
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport)

    async def _test():
        bal = await client.get_account_balances()
        assert isinstance(bal, AccountBalance)
        assert bal.toman_available == 4_850_000.0
        assert bal.toman_locked == 150_000.0
        assert bal.crypto_balances["BTC"] == 0.00025
        assert bal.total_equity_tmn == 5_900_000.0
        assert bal.total_toman == 5_000_000.0

    asyncio.run(_test())


def test_wallex_client_post_order_limit():
    router = MockTransportRouter()
    router.set_tool("wallex_spot_post_account_orders", {
        "order_id": "WX-998811",
        "status": "PENDING",
        "filled_quantity": 0.0,
        "average_price": 4_200_000_000.0,
        "fee": 0.0,
    })
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport)

    async def _test():
        req = OrderRequest(
            client_order_id="CL-LIMIT-001",
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=4_200_000_000.0,
            quantity=0.0001,
        )
        res = await client.post_order(order=req)
        assert isinstance(res, OrderExecutionResult)
        assert res.client_order_id == "CL-LIMIT-001"
        assert res.exchange_order_id == "WX-998811"
        assert res.status == OrderStatus.PENDING

    asyncio.run(_test())


def test_wallex_client_cancel_order_priority_zero():
    router = MockTransportRouter()
    router.set_tool("wallex_spot_delete_account_orders", {"status": "SUCCESS"})
    transport = httpx.MockTransport(router.handler)

    rate_limiter = TokenBucketRateLimiter(capacity=30.0, refill_rate=3.0)
    client = WallexClient(transport=transport, rate_limiter=rate_limiter)

    async def _test():
        ok = await client.cancel_order(order_id="WX-998811", priority=0)
        assert ok is True
        assert len(router.call_history) == 1

    asyncio.run(_test())


def test_wallex_client_cancel_all_orders():
    router = MockTransportRouter()
    router.set_tool("wallex_spot_get_open_order", {
        "orders": [
            {"id": "ORD-1", "symbol": "BTCTMN"},
            {"id": "ORD-2", "symbol": "BTCTMN"},
        ]
    })
    router.set_tool("wallex_spot_delete_account_orders", {"status": "SUCCESS"})
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport)

    async def _test():
        cancelled = await client.cancel_all_orders(symbol="BTCTMN")
        assert cancelled == 2

    asyncio.run(_test())


def test_wallex_client_http_429_backoff_and_recovery():
    router = MockTransportRouter()
    router.set_tool("wallex_basic_get_markets", [{"symbol": "BTCTMN"}])
    # Fail attempts 1 and 2 with 429 (Retry-After: 0.05s), succeed on attempt 3
    router.simulate_transient_status(status=429, succeed_after=2, headers={"Retry-After": "0.05"})
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport, max_retries=4)

    async def _test():
        markets = await client.get_markets()
        assert len(markets) == 1
        assert len(router.call_history) == 3

    asyncio.run(_test())


def test_wallex_client_http_429_max_retries_exhaustion():
    router = MockTransportRouter()
    # Continuous 429
    router.simulate_transient_status(status=429, succeed_after=10, headers={"Retry-After": "0.01"})
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport, max_retries=3)

    async def _test():
        with pytest.raises(WallexRateLimitError):
            await client.get_markets()
        assert len(router.call_history) == 3

    asyncio.run(_test())


def test_wallex_client_http_401_auth_error_no_retry():
    router = MockTransportRouter()
    router.simulate_transient_status(status=401, succeed_after=10)
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport, max_retries=4)

    async def _test():
        with pytest.raises(WallexAuthError):
            await client.get_markets()
        # Fatal auth failure must fail on 1st attempt without retrying
        assert len(router.call_history) == 1

    asyncio.run(_test())


def test_wallex_client_transient_network_error_retry():
    router = MockTransportRouter()
    router.set_tool("wallex_basic_get_markets", [{"symbol": "BTCTMN"}])
    router.simulate_transient_connect_error(succeed_after=1)
    transport = httpx.MockTransport(router.handler)
    client = WallexClient(transport=transport, max_retries=3)

    async def _test():
        markets = await client.get_markets()
        assert len(markets) == 1
        assert len(router.call_history) == 2

    asyncio.run(_test())


def test_wallex_client_context_manager():
    router = MockTransportRouter()
    transport = httpx.MockTransport(router.handler)

    async def _test():
        async with WallexClient(transport=transport) as client:
            assert client._client is not None
            assert not client._client.is_closed
        assert client._client.is_closed

    asyncio.run(_test())


# ==============================================================================
# Subsystem C: MockWallexClient Verification
# ==============================================================================

def test_mock_client_interface_compliance():
    assert issubclass(MockWallexClient, BaseWallexClient)
    assert issubclass(WallexClient, BaseWallexClient)


def test_mock_client_initial_balances():
    async def _test():
        mock = MockWallexClient()
        bal = await mock.get_account_balances()
        assert bal.toman_available == 5_000_000.0
        assert bal.toman_locked == 0.0
        assert bal.crypto_balances == {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0}
        assert bal.total_equity_tmn == 5_000_000.0

    asyncio.run(_test())


def test_mock_client_market_buy_execution():
    async def _test():
        mock = MockWallexClient()
        # Price 4,200,000,000, Quantity 0.0002 -> Cost: 840,000 TMN, Fee (0.25%): 2,100 TMN -> Total: 842,100 TMN
        res = await mock.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            price=4_200_000_000.0,
            quantity=0.0002,
            client_order_id="MOCK-BUY-001",
        )
        assert res.status == OrderStatus.FILLED
        assert res.filled_quantity == 0.0002
        assert res.fee_paid_tmn == 2100.0

        bal = await mock.get_account_balances()
        assert pytest.approx(bal.toman_available, 1e-2) == (5_000_000.0 - 842_100.0)
        assert pytest.approx(bal.crypto_balances["BTC"], 1e-8) == 0.0002

    asyncio.run(_test())


def test_mock_client_market_sell_execution():
    async def _test():
        mock = MockWallexClient()
        mock.crypto_balances["BTC"] = 0.0002
        # Sell 0.0002 at 4,200,000,000 -> Gross revenue: 840,000 TMN, Fee (0.25%): 2,100 TMN -> Net: 837,900 TMN
        res = await mock.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=4_200_000_000.0,
            quantity=0.0002,
            client_order_id="MOCK-SELL-001",
        )
        assert res.status == OrderStatus.FILLED
        assert res.filled_quantity == 0.0002

        bal = await mock.get_account_balances()
        assert pytest.approx(bal.toman_available, 1e-2) == (5_000_000.0 + 837_900.0)
        assert bal.crypto_balances["BTC"] == 0.0

    asyncio.run(_test())


def test_mock_client_limit_order_lock_and_cancel():
    async def _test():
        mock = MockWallexClient()
        # Post Limit Buy below market
        res = await mock.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=4_000_000_000.0,
            quantity=0.0001,
            client_order_id="MOCK-LIMIT-001",
        )
        # Cost: 400,000 TMN + 1,000 fee = 401,000 TMN
        assert res.status in (OrderStatus.PENDING, OrderStatus.FILLED)

        # Cancel order
        cancelled = await mock.cancel_order(client_order_id="MOCK-LIMIT-001")
        assert cancelled is True

    asyncio.run(_test())


def test_mock_client_synthetic_depth_spread_and_imbalance():
    async def _test():
        mock = MockWallexClient()
        snapshot = await mock.get_market_depth("BTCTMN")
        assert isinstance(snapshot, MarketDataSnapshot)
        assert len(snapshot.bids) == 10
        assert len(snapshot.asks) == 10
        assert snapshot.best_bid > 0
        assert snapshot.best_ask > snapshot.best_bid
        assert snapshot.spread_pct > 0.0
        assert -1.0 <= snapshot.imbalance_ratio <= 1.0

    asyncio.run(_test())


def test_mock_client_fault_injection():
    async def _test():
        mock = MockWallexClient()

        # 1. Rate limit failure
        mock.inject_rate_limit_failure(True)
        with pytest.raises(WallexRateLimitError):
            await mock.get_markets()
        mock.inject_rate_limit_failure(False)

        # 2. Network failure
        mock.inject_network_failure(True)
        with pytest.raises(WallexNetworkError):
            await mock.get_markets()
        mock.inject_network_failure(False)

        # 3. Stale data injection
        mock.inject_stale_data(True)
        snapshot = await mock.get_market_depth("BTCTMN")
        assert snapshot.is_stale is True

    asyncio.run(_test())


# ==============================================================================
# Subsystem D: Edge Cases and Defensive Invariants
# ==============================================================================

def test_edge_case_malformed_json_response():
    def bad_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>502 Bad Gateway</html>", request=request)

    transport = httpx.MockTransport(bad_json_handler)
    client = WallexClient(transport=transport)

    async def _test():
        with pytest.raises(WallexAPIError) as exc_info:
            await client.get_markets()
        assert exc_info.value.code == "INVALID_RESPONSE"

    asyncio.run(_test())


def test_edge_case_json_rpc_error_object():
    def rpc_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32602, "message": "Invalid params for tool"},
            },
            request=request,
        )

    transport = httpx.MockTransport(rpc_error_handler)
    client = WallexClient(transport=transport)

    async def _test():
        with pytest.raises(WallexClientError) as exc_info:
            await client.get_market_depth("BTCTMN")
        assert exc_info.value.code == -32602

    asyncio.run(_test())


def test_edge_case_empty_orderbook():
    ob = OrderBook(
        symbol="BTCTMN",
        timestamp=time.time() * 1000.0,
        bids=[],
        asks=[],
    )
    assert ob.best_bid is None
    assert ob.best_ask is None
    assert ob.spread_pct == 0.0
    assert ob.calculate_imbalance() == 0.0

    snapshot = MarketDataSnapshot.from_orderbook(ob)
    assert snapshot.best_bid == 0.0
    assert snapshot.best_ask == 0.0
    assert snapshot.imbalance_ratio == 0.0


def test_edge_case_crossed_orderbook():
    # Corrupt feed: best_bid > best_ask
    ob = OrderBook(
        symbol="BTCTMN",
        timestamp=time.time() * 1000.0,
        bids=[(4_300_000_000.0, 1.0)],
        asks=[(4_200_000_000.0, 1.0)],
    )
    snapshot = MarketDataSnapshot.from_orderbook(ob)
    # Crossed orderbook is flagged as corrupted / stale
    assert snapshot.is_stale is True


def test_edge_case_stale_timestamp_detection():
    old_time = (time.time() - 3.0) * 1000.0  # 3 seconds ago (>1500ms)
    ob = OrderBook(
        symbol="BTCTMN",
        timestamp=old_time,
        bids=[(4_200_000_000.0, 1.0)],
        asks=[(4_210_000_000.0, 1.0)],
    )
    snapshot = MarketDataSnapshot.from_orderbook(ob, max_age_ms=1500.0)
    assert snapshot.is_stale is True


def test_edge_case_insufficient_balance_rejection():
    async def _test():
        mock = MockWallexClient()
        # Available is 5,000,000 TMN; attempt to buy 6,000,000 TMN worth of BTC
        res = await mock.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            price=4_200_000_000.0,
            quantity=0.002,  # 8,400,000 TMN > 5,000,000 TMN
            client_order_id="REJECT-001",
        )
        assert res.status == OrderStatus.REJECTED
        assert "Insufficient" in str(res.raw_response)

    asyncio.run(_test())


# ==============================================================================
# Subsystem D: Remediations Verification (Iteration 2)
# ==============================================================================

class TestMockClientInputValidation:
    """Verifies input validation invariants and parity between MockWallexClient and WallexClient."""

    def test_mock_client_rejects_negative_quantity(self):
        mock = MockWallexClient()
        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order("BTCTMN", OrderSide.BUY, OrderType.LIMIT, price=4.2e9, quantity=-0.0001)
        asyncio.run(_run())

    def test_mock_client_rejects_zero_quantity(self):
        mock = MockWallexClient()
        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order("BTCTMN", OrderSide.BUY, OrderType.LIMIT, price=4.2e9, quantity=0.0)
        asyncio.run(_run())

    def test_mock_client_rejects_nan_quantity(self):
        mock = MockWallexClient()
        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order("BTCTMN", OrderSide.BUY, OrderType.LIMIT, price=4.2e9, quantity=float("nan"))
        asyncio.run(_run())

    def test_mock_client_rejects_inf_quantity(self):
        mock = MockWallexClient()
        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order quantity"):
                await mock.post_order("BTCTMN", OrderSide.BUY, OrderType.LIMIT, price=4.2e9, quantity=float("inf"))
        asyncio.run(_run())

    def test_mock_client_rejects_zero_limit_price(self):
        mock = MockWallexClient()
        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid limit order price"):
                await mock.post_order("BTCTMN", OrderSide.BUY, OrderType.LIMIT, price=0.0, quantity=0.0001)
        asyncio.run(_run())

    def test_mock_client_rejects_negative_price(self):
        mock = MockWallexClient()
        async def _run():
            with pytest.raises(WallexOrderError, match="Invalid order price"):
                await mock.post_order("BTCTMN", OrderSide.BUY, OrderType.MARKET, price=-500.0, quantity=0.0001)
        asyncio.run(_run())

    def test_mock_client_market_order_resolves_market_price_when_unspecified(self):
        mock = MockWallexClient(initial_tmn=5_000_000.0)
        async def _run():
            res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.0001,
            )
            assert res.status == OrderStatus.FILLED
            assert res.average_fill_price > 4_000_000_000.0
            assert mock.tmn_available < 5_000_000.0
        asyncio.run(_run())


class TestMockClientLimitOrderExecution:
    """Verifies crossing vs resting limit order state transitions, escrow locking, and cancellations."""

    def test_unmarketable_limit_orders_rest_and_cancel(self):
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 0.50

            # 1. Unmarketable BUY (Resting)
            buy_res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=1000.0,
                quantity=0.01,
                client_order_id="TEST-REST-BUY",
            )
            assert buy_res.status == OrderStatus.PENDING
            assert len(await mock.get_open_orders()) == 1
            assert mock.tmn_locked > 0.0

            # 2. Unmarketable SELL (Resting)
            sell_res = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=5_000_000_000.0,
                quantity=0.20,
                client_order_id="TEST-REST-SELL",
            )
            assert sell_res.status == OrderStatus.PENDING
            assert mock.crypto_balances["BTC"] == 0.30
            assert mock.crypto_locked["BTC"] == 0.20
            assert len(await mock.get_open_orders()) == 2

            # 3. Double spend prevention
            double_spend = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=5_000_000_000.0,
                quantity=0.35,
            )
            assert double_spend.status == OrderStatus.REJECTED

            # 4. Cancel resting BUY and unlock TMN
            assert await mock.cancel_order(client_order_id="TEST-REST-BUY") is True
            assert mock.tmn_locked == 0.0

            # 5. Cancel resting SELL and unlock BTC
            assert await mock.cancel_order(client_order_id="TEST-REST-SELL") is True
            assert mock.crypto_balances["BTC"] == 0.50
            assert mock.crypto_locked["BTC"] == 0.0
            assert len(await mock.get_open_orders()) == 0

            # 6. Status of cancelled order
            status = await mock.get_order_status("TEST-REST-BUY")
            assert status is not None
            assert status.status == OrderStatus.CANCELLED

        asyncio.run(_run())

    def test_crossing_limit_orders_fill_immediately(self):
        async def _run():
            mock = MockWallexClient(initial_tmn=5_000_000.0)
            mock.crypto_balances["BTC"] = 0.01
            depth = await mock.get_market_depth("BTCTMN")

            # Crossing BUY: price >= best_ask
            cross_buy = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=depth.best_ask + 1000.0,
                quantity=0.0001,
            )
            assert cross_buy.status == OrderStatus.FILLED
            assert cross_buy.filled_quantity == 0.0001

            # Crossing SELL: price <= best_bid
            cross_sell = await mock.post_order(
                symbol="BTCTMN",
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=depth.best_bid - 1000.0,
                quantity=0.0001,
            )
            assert cross_sell.status == OrderStatus.FILLED
            assert cross_sell.filled_quantity == 0.0001

        asyncio.run(_run())


class TestRetryAfterParsing:
    """Verifies WallexClient._parse_retry_after handles diverse header formats."""

    def test_float_and_integer_seconds(self):
        assert WallexClient._parse_retry_after("0.05") == 0.05
        assert WallexClient._parse_retry_after("1.5") == 1.5
        assert WallexClient._parse_retry_after(" 120 ") == 120.0
        assert WallexClient._parse_retry_after("0") == 0.0

    def test_invalid_headers_return_none(self):
        assert WallexClient._parse_retry_after("invalid") is None
        assert WallexClient._parse_retry_after("") is None
        assert WallexClient._parse_retry_after(None) is None

