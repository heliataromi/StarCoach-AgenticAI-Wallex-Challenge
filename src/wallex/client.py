"""Production Wallex REST & JSON-RPC 2.0 MCP Client and Base Client ABC.
Implements persistent httpx.AsyncClient connection pooling, token bucket rate limiter
integration with priority bypass, automated exponential backoff on HTTP 429,
and resilient error handling.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from src.wallex.models import (
    AccountBalance,
    Candle,
    CorruptMarketDataError,
    DepthLevel,
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
    WallexInsufficientFundsError,
    WallexNetworkError,
    WallexOrderError,
    WallexRateLimitError,
)
from src.wallex.rate_limiter import TokenBucketRateLimiter


# ==============================================================================
# 1. Base Wallex Client Abstract Base Class
# ==============================================================================

class BaseWallexClient(ABC):
    """Abstract Base Class establishing the canonical contract for Wallex API clients."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish persistent connection and discover active sub-account."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Gracefully release connection pools and network resources."""
        pass

    async def __aenter__(self) -> BaseWallexClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    @abstractmethod
    async def get_markets(self) -> List[Dict[str, Any]]:
        """Fetch list of all supported market pairs and metadata."""
        pass

    @abstractmethod
    async def get_market_depth(self, symbol: str) -> MarketDataSnapshot:
        """Fetch top-10 bid/ask depth for a symbol (e.g. 'BTCTMN') and return MarketDataSnapshot."""
        pass

    async def get_market_snapshot(self, symbol: str) -> MarketDataSnapshot:
        """Convenience method returning parsed MarketDataSnapshot."""
        return await self.get_market_depth(symbol)

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        resolution: str = "15",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch historical OHLCV candle records."""
        pass

    @abstractmethod
    async def get_account_balances(self) -> AccountBalance:
        """Fetch real-time Toman and asset balances on the credit sub-account."""
        pass

    async def get_account_balance(self) -> AccountBalance:
        """Alias for get_account_balances() for test contract compatibility."""
        return await self.get_account_balances()

    @abstractmethod
    async def get_account_fees(self) -> Dict[str, float]:
        """Fetch maker and taker fee rates (e.g. {'maker': 0.0025, 'taker': 0.0025})."""
        pass

    @abstractmethod
    async def post_order(
        self,
        order: Optional[Union[OrderRequest, str]] = None,
        symbol: Optional[str] = None,
        side: Optional[Union[OrderSide, str]] = None,
        order_type: Optional[Union[OrderType, str]] = None,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        client_order_id: Optional[str] = None,
        priority: int = 1,
    ) -> OrderExecutionResult:
        """Dispatch a spot credit order (LIMIT or MARKET) to the exchange."""
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None, priority: int = 1) -> List[Dict[str, Any]]:
        """Query currently active/pending orders."""
        pass

    @abstractmethod
    async def get_order_status(self, client_order_id: str) -> Optional[OrderExecutionResult]:
        """Query execution status of an order by client_order_id."""
        pass

    @abstractmethod
    async def cancel_order(
        self,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        priority: int = 0,
    ) -> bool:
        """Cancel an open order (default priority=0 for emergency bypass)."""
        pass

    @abstractmethod
    async def cancel_all_orders(self, symbol: Optional[str] = None, priority: int = 0) -> int:
        """Cancel all open orders for a symbol or account."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Perform a low-overhead health check against Wallex services."""
        pass


# ==============================================================================
# 2. Production Wallex Client (JSON-RPC 2.0 MCP + REST)
# ==============================================================================

class WallexClient(BaseWallexClient):
    """
    Production Wallex API & MCP Client implementing JSON-RPC 2.0 at https://api.wallex.ir/mcp.
    Features:
    - Persistent httpx.AsyncClient connection pooling (keepalive, TCP reuse).
    - Integrated TokenBucketRateLimiter with Priority 0 bypass for stop-loss and cancels.
    - Automated exponential backoff ($1.5^n$ + jitter) on HTTP 429.
    - Automated reconnection on socket drops or network timeouts.
    - Credential masking in logs and string representations.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.wallex.ir",
        mcp_endpoint: str = "https://api.wallex.ir/mcp",
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
        sub_account_id: Optional[str] = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 4,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key = api_key or os.getenv("WALLEX_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._mcp_endpoint = mcp_endpoint
        self._rate_limiter = rate_limiter
        self._sub_account_id = sub_account_id
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._transport = transport

        self._client: Optional[httpx.AsyncClient] = None
        self._rpc_id_counter: int = 0
        self._closed: bool = False

    def __repr__(self) -> str:
        masked_key = f"...{self._api_key[-4:]}" if self._api_key and len(self._api_key) >= 4 else "None"
        return f"WallexClient(sub_account_id={self._sub_account_id}, key={masked_key})"

    def _next_rpc_id(self) -> int:
        self._rpc_id_counter += 1
        return self._rpc_id_counter

    async def connect(self) -> None:
        """Initializes underlying httpx.AsyncClient if not active."""
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0)
            timeout = httpx.Timeout(connect=3.0, read=self._timeout_seconds, write=3.0, pool=2.0)
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                limits=limits,
                timeout=timeout,
                transport=self._transport,
            )
            self._closed = False

    async def close(self) -> None:
        """Gracefully closes HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._closed = True

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "StarCoach-TradingEngine/1.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
            headers["x-api-key"] = self._api_key
        return headers

    def _unwrap_mcp_response(self, response_data: Any) -> Any:
        """Unwraps JSON-RPC result or MCP content envelope."""
        if not isinstance(response_data, dict):
            return response_data

        if "error" in response_data and response_data["error"]:
            err = response_data["error"]
            err_code = err.get("code") if isinstance(err, dict) else None
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            raise WallexClientError(f"Wallex JSON-RPC Error: {err_msg}", code=err_code, raw_response=response_data)

        result = response_data.get("result", response_data)
        if isinstance(result, dict) and "content" in result and isinstance(result["content"], list):
            content_list = result["content"]
            if content_list and isinstance(content_list[0], dict) and content_list[0].get("type") == "text":
                text_content = content_list[0].get("text", "")
                try:
                    return json.loads(text_content)
                except Exception:
                    return text_content
        return result

    @staticmethod
    def _parse_retry_after(header_value: Optional[str], max_delay: float = 300.0) -> Optional[float]:
        """
        Parses HTTP Retry-After header supporting float seconds, integer seconds,
        Unix epoch timestamps, and RFC 7231 HTTP-date formats.
        Sanitizes against NaN, negative values, and caps excessive/infinite delays to max_delay (default 300.0s).
        Returns non-negative float delay in seconds, or None if unparseable/NaN.
        """
        if not header_value:
            return None
        clean_val = str(header_value).strip()
        if not clean_val:
            return None

        # 1. Float or integer seconds / epoch timestamp
        try:
            val = float(clean_val)
            if math.isnan(val):
                return None
            if val <= 0.0:
                return 0.0
            if math.isinf(val):
                return max_delay

            if val > 1e8:  # Future epoch timestamp
                delay = val - time.time()
                if math.isnan(delay) or delay <= 0.0:
                    return 0.0
                if math.isinf(delay) or delay > max_delay:
                    return max_delay
                return delay

            return min(val, max_delay)
        except (ValueError, TypeError):
            pass

        # 2. HTTP-Date (RFC 7231 / RFC 1123)
        try:
            import datetime
            import email.utils
            dt = email.utils.parsedate_to_datetime(clean_val)
            if dt is not None:
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                diff = (dt - now_dt).total_seconds()
                if math.isnan(diff) or diff <= 0.0:
                    return 0.0
                if math.isinf(diff) or diff > max_delay:
                    return max_delay
                return diff
        except Exception:
            pass

        return None

    async def _execute_with_retry(
        self,
        request_func: Any,
        priority: int = 1,
    ) -> Any:
        """Executes an HTTP request with token bucket rate limiting and 429 exponential backoff."""
        await self.connect()
        assert self._client is not None

        for attempt in range(self._max_retries):
            # Acquire tokens from rate limiter
            if self._rate_limiter is not None:
                await self._rate_limiter.acquire(weight=1.0, priority=priority)

            try:
                response = await request_func(self._client)
                status = response.status_code

                if status == 200:
                    try:
                        data = response.json()
                    except json.JSONDecodeError as jde:
                        raise WallexAPIError(f"Malformed JSON response from Wallex: {jde}", code="INVALID_RESPONSE")
                    return self._unwrap_mcp_response(data)

                if status in (401, 403):
                    raise WallexAuthError(f"HTTP {status}: Authentication failed for Wallex API", code=status)

                if status == 429:
                    if attempt == self._max_retries - 1:
                        raise WallexRateLimitError(f"HTTP 429: Rate limit exceeded after {self._max_retries} retries", code=429)

                    retry_header = response.headers.get("Retry-After") or response.headers.get("x-ratelimit-reset")
                    retry_sec = self._parse_retry_after(retry_header)

                    if self._rate_limiter is not None:
                        await self._rate_limiter.handle_429(retry_after=retry_sec, attempt=attempt + 1)
                    else:
                        delay = retry_sec or min(10.0, (1.5 ** (attempt + 1)) + random.uniform(0.1, 0.5))
                        if math.isnan(delay) or delay <= 0:
                            delay = min(10.0, (1.5 ** (attempt + 1)) + random.uniform(0.1, 0.5))
                        elif math.isinf(delay) or delay > 300.0:
                            delay = 300.0
                        await asyncio.sleep(delay)
                    continue

                if status >= 500:
                    if attempt == self._max_retries - 1:
                        raise WallexNetworkError(f"HTTP {status} exchange server error", code=status)
                    await asyncio.sleep(0.1 * (2 ** attempt))
                    continue

                # Unhandled 4xx error
                raise WallexAPIError(f"HTTP {status}: {response.text}", code=status)

            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as net_err:
                if attempt == self._max_retries - 1:
                    raise WallexNetworkError(f"Network error communicating with Wallex: {net_err}")
                await asyncio.sleep(0.1 * (2 ** attempt))
            except (WallexAuthError, WallexRateLimitError, WallexAPIError):
                raise

        raise WallexNetworkError(f"Failed to execute request after {self._max_retries} attempts")

    async def _call_mcp_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        priority: int = 1,
    ) -> Any:
        """Dispatches a JSON-RPC 2.0 tool call to the Wallex MCP endpoint."""
        rpc_id = self._next_rpc_id()
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        async def _do_post(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                self._mcp_endpoint,
                headers=self._get_headers(),
                json=payload,
            )

        return await self._execute_with_retry(_do_post, priority=priority)

    # --------------------------------------------------------------------------
    # Market Data Methods
    # --------------------------------------------------------------------------

    async def get_markets(self) -> List[Dict[str, Any]]:
        """Fetch list of all supported markets."""
        res = await self._call_mcp_tool("wallex_basic_get_markets", {}, priority=2)
        if isinstance(res, dict) and "markets" in res:
            return res["markets"]
        if isinstance(res, list):
            return res
        return []

    async def get_market_depth(self, symbol: str) -> MarketDataSnapshot:
        """Fetch top-10 bid/ask depth for a symbol and return parsed MarketDataSnapshot."""
        raw = await self._call_mcp_tool("wallex_basic_get_market_depth", {"symbol": symbol}, priority=2)
        
        # Parse bids and asks from raw payload
        raw_bids = raw.get("bids", []) if isinstance(raw, dict) else []
        raw_asks = raw.get("asks", []) if isinstance(raw, dict) else []
        timestamp = raw.get("timestamp", time.time() * 1000.0) if isinstance(raw, dict) else (time.time() * 1000.0)

        # Standardize tuples of (price, volume)
        bids: List[Tuple[float, float]] = []
        for b in raw_bids:
            if isinstance(b, (list, tuple)) and len(b) >= 2:
                bids.append((float(b[0]), float(b[1])))
            elif isinstance(b, dict):
                p = float(b.get("price", b.get("rate", 0.0)))
                q = float(b.get("quantity", b.get("volume", b.get("amount", 0.0))))
                bids.append((p, q))

        asks: List[Tuple[float, float]] = []
        for a in raw_asks:
            if isinstance(a, (list, tuple)) and len(a) >= 2:
                asks.append((float(a[0]), float(a[1])))
            elif isinstance(a, dict):
                p = float(a.get("price", a.get("rate", 0.0)))
                q = float(a.get("quantity", a.get("volume", a.get("amount", 0.0))))
                asks.append((p, q))

        # Sort bids descending, asks ascending, then truncate to top-10
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        bids = bids[:10]
        asks = asks[:10]

        orderbook = OrderBook(
            symbol=symbol,
            timestamp=float(timestamp),
            bids=bids,
            asks=asks,
        )

        return MarketDataSnapshot.from_orderbook(orderbook)

    async def get_candles(
        self,
        symbol: str,
        resolution: str = "15",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch historical OHLCV candles."""
        args: Dict[str, Any] = {"symbol": symbol, "resolution": resolution}
        if from_ts is not None:
            args["from"] = from_ts
        if to_ts is not None:
            args["to"] = to_ts

        res = await self._call_mcp_tool("wallex_basic_candle_history", args, priority=2)
        if isinstance(res, dict) and "candles" in res:
            return res["candles"]
        if isinstance(res, list):
            return res
        return []

    # --------------------------------------------------------------------------
    # Account & Fee Methods
    # --------------------------------------------------------------------------

    async def get_account_balances(self) -> AccountBalance:
        """Fetch real-time Toman and asset balances on the credit sub-account."""
        args: Dict[str, Any] = {}
        if self._sub_account_id:
            args["sub_account_id"] = self._sub_account_id

        raw = await self._call_mcp_tool("wallex_credit_sub_account_balances_details", args, priority=2)

        available_tmn = float(raw.get("available", raw.get("toman_available", 0.0))) if isinstance(raw, dict) else 0.0
        locked_tmn = float(raw.get("locked", raw.get("toman_locked", 0.0))) if isinstance(raw, dict) else 0.0
        assets = raw.get("assets", raw.get("crypto_balances", {})) if isinstance(raw, dict) else {}
        total_equity = float(raw.get("total_equity_tmn", raw.get("total_equity", available_tmn + locked_tmn))) if isinstance(raw, dict) else 0.0
        ts = float(raw.get("timestamp", time.time())) if isinstance(raw, dict) else time.time()

        crypto_balances = {k: float(v) for k, v in assets.items()} if isinstance(assets, dict) else {}

        return AccountBalance(
            toman_available=available_tmn,
            toman_locked=locked_tmn,
            crypto_balances=crypto_balances,
            total_equity_tmn=total_equity,
            timestamp=ts,
        )

    async def get_account_fees(self) -> Dict[str, float]:
        """Fetch maker and taker fee rates."""
        raw = await self._call_mcp_tool("wallex_basic_get_account_fees", {}, priority=2)
        maker = 0.0025
        taker = 0.0025
        if isinstance(raw, dict):
            maker = float(raw.get("maker", raw.get("maker_fee", 0.0025)))
            taker = float(raw.get("taker", raw.get("taker_fee", 0.0025)))
        return {"maker": maker, "taker": taker}

    # --------------------------------------------------------------------------
    # Execution & Order Routing Methods
    # --------------------------------------------------------------------------

    async def post_order(
        self,
        order: Optional[Union[OrderRequest, str]] = None,
        symbol: Optional[str] = None,
        side: Optional[Union[OrderSide, str]] = None,
        order_type: Optional[Union[OrderType, str]] = None,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        client_order_id: Optional[str] = None,
        priority: int = 1,
    ) -> OrderExecutionResult:
        """Dispatches an order to the Wallex exchange."""
        # Unify arguments whether passed as OrderRequest or kwargs
        if isinstance(order, OrderRequest):
            req_symbol = order.symbol
            req_side = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
            req_type = order.order_type.value if isinstance(order.order_type, OrderType) else str(order.order_type)
            req_price = float(order.price)
            req_qty = float(order.quantity)
            req_cid = str(order.client_order_id)
        elif isinstance(order, str):
            if isinstance(symbol, (OrderSide, str)) and (isinstance(symbol, OrderSide) or str(symbol).upper() in ("BUY", "SELL")):
                req_symbol = order
                req_side = symbol.value if isinstance(symbol, OrderSide) else str(symbol)
                req_type = side.value if isinstance(side, OrderType) else (str(side) if side is not None else (order_type.value if isinstance(order_type, OrderType) else str(order_type or "LIMIT")))
                req_price = float(price or 0.0)
                req_qty = float(quantity or 0.0)
                req_cid = str(client_order_id or f"cl-{int(time.time()*1000)}")
            else:
                req_symbol = order
                req_side = side.value if isinstance(side, OrderSide) else str(side)
                req_type = order_type.value if isinstance(order_type, OrderType) else str(order_type)
                req_price = float(price or 0.0)
                req_qty = float(quantity or 0.0)
                req_cid = str(client_order_id or f"cl-{int(time.time()*1000)}")
        else:
            req_symbol = str(symbol)
            req_side = side.value if isinstance(side, OrderSide) else str(side)
            req_type = order_type.value if isinstance(order_type, OrderType) else str(order_type)
            req_price = float(price or 0.0)
            req_qty = float(quantity or 0.0)
            req_cid = str(client_order_id or f"cl-{int(time.time()*1000)}")

        # Validation
        if req_qty <= 0:
            raise WallexOrderError(f"Invalid order quantity: {req_qty}")
        if req_type == "LIMIT" and req_price <= 0:
            raise WallexOrderError(f"Invalid limit order price: {req_price}")

        arguments = {
            "symbol": req_symbol,
            "side": req_side.upper(),
            "type": req_type.upper(),
            "price": req_price,
            "quantity": req_qty,
            "client_id": req_cid,
        }

        raw = await self._call_mcp_tool("wallex_spot_post_account_orders", arguments, priority=priority)

        # Parse execution result
        status_str = raw.get("status", "PENDING") if isinstance(raw, dict) else "PENDING"
        try:
            status_enum = OrderStatus(status_str)
        except ValueError:
            status_enum = OrderStatus.PENDING

        exchange_id = raw.get("order_id", raw.get("id")) if isinstance(raw, dict) else None
        filled_qty = float(raw.get("filled_quantity", req_qty if status_enum == OrderStatus.FILLED else 0.0)) if isinstance(raw, dict) else 0.0
        avg_price = float(raw.get("average_price", req_price)) if isinstance(raw, dict) else req_price
        fee_paid = float(raw.get("fee", 0.0)) if isinstance(raw, dict) else 0.0

        return OrderExecutionResult(
            client_order_id=req_cid,
            exchange_order_id=str(exchange_id) if exchange_id else None,
            symbol=req_symbol,
            status=status_enum,
            filled_quantity=filled_qty,
            average_fill_price=avg_price,
            fee_paid_tmn=fee_paid,
            executed_at=time.time(),
            raw_response=raw if isinstance(raw, dict) else {"raw": raw},
        )

    async def get_open_orders(self, symbol: Optional[str] = None, priority: int = 1) -> List[Dict[str, Any]]:
        """Query active open orders."""
        args: Dict[str, Any] = {}
        if symbol:
            args["symbol"] = symbol
        res = await self._call_mcp_tool("wallex_spot_get_open_order", args, priority=priority)
        if isinstance(res, dict) and "orders" in res:
            return res["orders"]
        if isinstance(res, list):
            return res
        return []

    async def get_order_status(self, client_order_id: str) -> Optional[OrderExecutionResult]:
        """Query order status by client_order_id."""
        raw = await self._call_mcp_tool("wallex_spot_get_orders_client", {"client_id": client_order_id}, priority=1)
        if not raw or not isinstance(raw, dict):
            return None

        status_str = raw.get("status", "PENDING")
        try:
            status_enum = OrderStatus(status_str)
        except ValueError:
            status_enum = OrderStatus.PENDING

        return OrderExecutionResult(
            client_order_id=client_order_id,
            exchange_order_id=str(raw.get("order_id", raw.get("id"))),
            symbol=raw.get("symbol", ""),
            status=status_enum,
            filled_quantity=float(raw.get("filled_quantity", 0.0)),
            average_fill_price=float(raw.get("average_price", 0.0)),
            fee_paid_tmn=float(raw.get("fee", 0.0)),
            executed_at=float(raw.get("timestamp", time.time())),
            raw_response=raw,
        )

    async def cancel_order(
        self,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        priority: int = 0,
    ) -> bool:
        """Cancel an open order (default priority=0 for emergency bypass)."""
        args: Dict[str, Any] = {}
        if order_id:
            args["order_id"] = order_id
            tool = "wallex_spot_delete_account_orders"
        elif client_order_id:
            args["client_id"] = client_order_id
            tool = "wallex_spot_delete_orders_client"
        else:
            raise WallexOrderError("Must provide either order_id or client_order_id to cancel")

        res = await self._call_mcp_tool(tool, args, priority=priority)
        if isinstance(res, dict) and res.get("status") in ("CANCELLED", "SUCCESS", True):
            return True
        return bool(res)

    async def cancel_all_orders(self, symbol: Optional[str] = None, priority: int = 0) -> int:
        """Cancel all open orders for a symbol or account."""
        open_orders = await self.get_open_orders(symbol=symbol, priority=priority)
        cancelled_count = 0
        for o in open_orders:
            oid = o.get("order_id", o.get("id"))
            cid = o.get("client_order_id", o.get("client_id"))
            if oid:
                success = await self.cancel_order(order_id=str(oid), priority=priority)
            elif cid:
                success = await self.cancel_order(client_order_id=str(cid), priority=priority)
            else:
                success = False
            if success:
                cancelled_count += 1
        return cancelled_count

    async def health_check(self) -> bool:
        """Lightweight health check against Wallex services."""
        try:
            markets = await self.get_markets()
            return len(markets) > 0
        except Exception:
            return False
