"""High-fidelity in-memory Mock Wallex Client for 100% deterministic offline testing,
backtesting, and shadow paper-trading without live network or external credentials.
Preloaded with 5,000,000 TMN dedicated credit account balance, synthetic depth,
realistic fee deductions, and fault injection knobs.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.wallex.client import BaseWallexClient
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


class MockWallexClient(BaseWallexClient):
    """
    High-fidelity stateful in-memory Wallex client.
    Implements BaseWallexClient interface identically to production client.
    """

    def __init__(
        self,
        api_key: str = "mock-api-key-test",
        sub_account_id: str = "hackathon-sub-5m",
        clock: Optional[Any] = None,
        rate_limiter: Optional[Any] = None,
        initial_tmn: float = 5_000_000.0,
    ) -> None:
        self.api_key = api_key
        self.sub_account_id = sub_account_id
        self._clock = clock
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            capacity=30.0,
            refill_rate=3.0,
            clock=clock,
        )

        # Dedicated Account Balance State (5,000,000 TMN default)
        self.initial_tmn = float(initial_tmn)
        self.tmn_available = float(initial_tmn)
        self.tmn_locked = 0.0
        self.crypto_balances: Dict[str, float] = {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0}
        self.crypto_locked: Dict[str, float] = {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0}

        # Pricing and Order Repositories
        self.mock_prices: Dict[str, float] = {
            "BTCTMN": 4_200_000_000.0,
            "ETHTMN": 180_000_000.0,
            "USDTTMN": 65_000.0,
        }
        self.orderbooks: Dict[str, Dict[str, Any]] = self._init_default_orderbooks()
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.executed_orders: List[Dict[str, Any]] = []
        self.cancelled_orders: List[Dict[str, Any]] = []
        self._order_counter: int = 1000

        # Fault Injection Knobs
        self.fault_injection: Dict[str, Any] = {
            "rate_limit": False,
            "network_error": False,
            "stale_data": False,
            "simulated_latency": 0.0,
        }

        self.fee_rate: float = 0.0025  # 0.25% maker / taker fee

    def _now(self) -> float:
        """Returns current time in seconds from clock or system time."""
        if self._clock is not None:
            if hasattr(self._clock, "time") and callable(getattr(self._clock, "time")):
                return self._clock.time()
            if callable(self._clock):
                return self._clock()
        return time.time()

    def _init_default_orderbooks(self) -> Dict[str, Dict[str, Any]]:
        """Generates realistic 10-level default orderbooks for permitted hackathon pairs."""
        obs: Dict[str, Dict[str, Any]] = {}
        for sym, mid_p in self.mock_prices.items():
            bids = [(mid_p * (1.0 - 0.001 * (i + 1)), 0.05 * (i + 1)) for i in range(10)]
            asks = [(mid_p * (1.0 + 0.001 * (i + 1)), 0.04 * (i + 1)) for i in range(10)]
            obs[sym] = {"bids": bids, "asks": asks}
        return obs

    def _get_market_execution_price(self, symbol: str, side: str) -> float:
        """Resolves best counterparty market price from current orderbook or mock prices."""
        ob = self.orderbooks.get(symbol)
        if ob:
            if side == "BUY" and ob.get("asks"):
                return float(ob["asks"][0][0])
            elif side == "SELL" and ob.get("bids"):
                return float(ob["bids"][0][0])
        return float(self.mock_prices.get(symbol, 0.0))

    # --------------------------------------------------------------------------
    # Fault Injection & Simulation Control API
    # --------------------------------------------------------------------------

    def inject_rate_limit_failure(self, enable: bool = True) -> None:
        self.fault_injection["rate_limit"] = enable

    def inject_network_failure(self, enable: bool = True) -> None:
        self.fault_injection["network_error"] = enable

    def inject_stale_data(self, enable: bool = True) -> None:
        self.fault_injection["stale_data"] = enable

    def set_simulated_latency(self, latency_seconds: float) -> None:
        self.fault_injection["simulated_latency"] = max(0.0, float(latency_seconds))

    def set_mock_price(self, symbol: str, price: float) -> None:
        self.mock_prices[symbol] = float(price)
        mid_p = float(price)
        bids = [(mid_p * (1.0 - 0.001 * (i + 1)), 0.05 * (i + 1)) for i in range(10)]
        asks = [(mid_p * (1.0 + 0.001 * (i + 1)), 0.04 * (i + 1)) for i in range(10)]
        self.orderbooks[symbol] = {"bids": bids, "asks": asks}

    def set_orderbook(self, symbol: str, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> None:
        self.orderbooks[symbol] = {"bids": list(bids), "asks": list(asks)}
        if bids:
            self.mock_prices[symbol] = bids[0][0]

    async def _check_faults_and_rate_limit(self, priority: int = 1) -> None:
        if self.fault_injection["simulated_latency"] > 0:
            await asyncio.sleep(self.fault_injection["simulated_latency"])

        if self.fault_injection["network_error"]:
            raise WallexNetworkError("Simulated Wallex network disconnection / timeout")

        if self.fault_injection["rate_limit"]:
            raise WallexRateLimitError("Simulated HTTP 429: Too Many Requests")

        if self.rate_limiter is not None:
            # If rate limiter has try_acquire or acquire
            if hasattr(self.rate_limiter, "acquire"):
                res = self.rate_limiter.acquire(priority=priority)
                if asyncio.iscoroutine(res):
                    allowed = await res
                else:
                    allowed = bool(res)
                if not allowed:
                    raise WallexRateLimitError("HTTP 429: Token bucket exhausted")

    # --------------------------------------------------------------------------
    # BaseWallexClient Interface Implementation
    # --------------------------------------------------------------------------

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_markets(self) -> List[Dict[str, Any]]:
        await self._check_faults_and_rate_limit(priority=2)
        return [
            {"symbol": "BTCTMN", "base": "BTC", "quote": "TMN", "is_active": True},
            {"symbol": "ETHTMN", "base": "ETH", "quote": "TMN", "is_active": True},
            {"symbol": "USDTTMN", "base": "USDT", "quote": "TMN", "is_active": True},
        ]

    async def get_market_depth(self, symbol: str) -> MarketDataSnapshot:
        await self._check_faults_and_rate_limit(priority=2)
        ob = self.orderbooks.get(symbol)
        if not ob:
            raise WallexOrderError(f"Unknown symbol: {symbol}")

        bids = list(ob["bids"])
        asks = list(ob["asks"])

        now_sec = self._now()
        if self.fault_injection["stale_data"]:
            ts_ms = (now_sec - 3.0) * 1000.0  # 3 seconds ago (>1500ms)
        else:
            ts_ms = now_sec * 1000.0

        orderbook = OrderBook(
            symbol=symbol,
            timestamp=ts_ms,
            bids=bids,
            asks=asks,
        )

        return MarketDataSnapshot.from_orderbook(
            orderbook,
            max_age_ms=1500.0,
            current_time_ms=now_sec * 1000.0,
        )

    async def get_candles(
        self,
        symbol: str,
        resolution: str = "15",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        await self._check_faults_and_rate_limit(priority=2)
        mid_p = self.mock_prices.get(symbol, 4_200_000_000.0)
        now_ts = int(self._now() * 1000)
        interval_ms = int(resolution) * 60 * 1000 if resolution.isdigit() else 15 * 60 * 1000
        candles = []
        for i in range(20):
            ts = now_ts - (20 - i) * interval_ms
            c_open = mid_p * (1.0 + 0.0002 * (i % 3 - 1))
            c_close = mid_p * (1.0 + 0.0003 * ((i + 1) % 3 - 1))
            c_high = max(c_open, c_close) * 1.002
            c_low = min(c_open, c_close) * 0.998
            c_vol = 1.25 * (i + 1)
            candles.append({
                "timestamp": ts,
                "open": c_open,
                "high": c_high,
                "low": c_low,
                "close": c_close,
                "volume": c_vol,
                "resolution": resolution,
            })
        return candles

    async def get_account_balances(self) -> AccountBalance:
        await self._check_faults_and_rate_limit(priority=2)

        btc_p = self.mock_prices.get("BTCTMN", 4_200_000_000.0)
        eth_p = self.mock_prices.get("ETHTMN", 180_000_000.0)
        usdt_p = self.mock_prices.get("USDTTMN", 65_000.0)

        btc_total = self.crypto_balances.get("BTC", 0.0) + self.crypto_locked.get("BTC", 0.0)
        eth_total = self.crypto_balances.get("ETH", 0.0) + self.crypto_locked.get("ETH", 0.0)
        usdt_total = self.crypto_balances.get("USDT", 0.0) + self.crypto_locked.get("USDT", 0.0)

        crypto_val = (
            btc_total * btc_p
            + eth_total * eth_p
            + usdt_total * usdt_p
        )
        total_equity = self.tmn_available + self.tmn_locked + crypto_val

        return AccountBalance(
            toman_available=self.tmn_available,
            toman_locked=self.tmn_locked,
            crypto_balances=dict(self.crypto_balances),
            total_equity_tmn=total_equity,
            timestamp=self._now(),
        )

    async def get_account_balance(self) -> AccountBalance:
        """Alias for get_account_balances() for test contract compatibility."""
        return await self.get_account_balances()

    async def get_account_fees(self) -> Dict[str, float]:
        await self._check_faults_and_rate_limit(priority=2)
        return {"maker": self.fee_rate, "taker": self.fee_rate}

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
        await self._check_faults_and_rate_limit(priority=priority)

        # Normalize arguments
        if isinstance(order, OrderRequest):
            req_symbol = order.symbol
            req_side = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
            req_type = order.order_type.value if isinstance(order.order_type, OrderType) else str(order.order_type)
            req_price = float(order.price)
            req_qty = float(order.quantity)
            req_cid = str(order.client_order_id)
        elif isinstance(order, str):
            # Check if positional arguments were used: post_order("BTCTMN", OrderSide.BUY, OrderType.LIMIT, ...)
            if isinstance(symbol, (OrderSide, str)) and (isinstance(symbol, OrderSide) or str(symbol).upper() in ("BUY", "SELL")):
                req_symbol = order
                req_side = symbol.value if isinstance(symbol, OrderSide) else str(symbol)
                req_type = side.value if isinstance(side, OrderType) else (str(side) if side is not None else (order_type.value if isinstance(order_type, OrderType) else str(order_type or "LIMIT")))
                req_price = float(price or 0.0)
                req_qty = float(quantity or 0.0)
                req_cid = str(client_order_id or f"mock-cl-{int(self._now()*1000)}")
            else:
                req_symbol = order
                req_side = side.value if isinstance(side, OrderSide) else str(side)
                req_type = order_type.value if isinstance(order_type, OrderType) else str(order_type)
                req_price = float(price or 0.0)
                req_qty = float(quantity or 0.0)
                req_cid = str(client_order_id or f"mock-cl-{int(self._now()*1000)}")
        else:
            req_symbol = str(symbol)
            req_side = side.value if isinstance(side, OrderSide) else str(side)
            req_type = order_type.value if isinstance(order_type, OrderType) else str(order_type)
            req_price = float(price or 0.0)
            req_qty = float(quantity or 0.0)
            req_cid = str(client_order_id or f"mock-cl-{int(self._now()*1000)}")

        req_side = req_side.upper()
        req_type = req_type.upper()

        # 1. Structural Input Validation (Parity with WallexClient invariants)
        if not req_symbol or not isinstance(req_symbol, str):
            raise WallexOrderError(f"Invalid order symbol: {req_symbol}")

        if req_side not in ("BUY", "SELL"):
            raise WallexOrderError(f"Invalid order side: {req_side}")

        if req_type not in ("LIMIT", "MARKET", "STOP_LIMIT"):
            raise WallexOrderError(f"Invalid order type: {req_type}")

        if math.isnan(req_qty) or math.isinf(req_qty) or req_qty <= 0:
            raise WallexOrderError(f"Invalid order quantity: {req_qty}")

        if math.isnan(req_price) or math.isinf(req_price) or req_price < 0:
            raise WallexOrderError(f"Invalid order price: {req_price}")

        if req_type == "LIMIT" and req_price <= 0:
            raise WallexOrderError(f"Invalid limit order price: {req_price}")

        # Resolve market order execution price when unspecified (price <= 0)
        if req_type == "MARKET" and req_price <= 0:
            resolved_price = self._get_market_execution_price(req_symbol, req_side)
            if resolved_price <= 0:
                raise WallexOrderError(f"Cannot resolve market execution price for {req_symbol}")
            req_price = resolved_price

        base_asset = req_symbol.replace("TMN", "").replace("_TMN", "").upper()
        cost_tmn = req_price * req_qty
        fee_paid = cost_tmn * self.fee_rate

        self._order_counter += 1
        exchange_order_id = f"WX-{int(self._now() * 1000)}-{self._order_counter}"

        # 2. Validation & Balance checks
        if req_side == "BUY":
            if (cost_tmn + fee_paid) > (self.tmn_available + 1e-6):
                return OrderExecutionResult(
                    client_order_id=req_cid,
                    exchange_order_id=None,
                    symbol=req_symbol,
                    status=OrderStatus.REJECTED,
                    filled_quantity=0.0,
                    average_fill_price=0.0,
                    fee_paid_tmn=0.0,
                    executed_at=self._now(),
                    raw_response={"error": "Insufficient TMN available balance"},
                )

        elif req_side == "SELL":
            curr_coin = self.crypto_balances.get(base_asset, 0.0)
            if req_qty > (curr_coin + 1e-9):
                return OrderExecutionResult(
                    client_order_id=req_cid,
                    exchange_order_id=None,
                    symbol=req_symbol,
                    status=OrderStatus.REJECTED,
                    filled_quantity=0.0,
                    average_fill_price=0.0,
                    fee_paid_tmn=0.0,
                    executed_at=self._now(),
                    raw_response={"error": f"Insufficient {base_asset} balance"},
                )

        # 3. Execution Logic: Crossing vs Resting Determination
        ob = self.orderbooks.get(req_symbol, {})
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        best_bid = float(bids[0][0]) if bids else None
        best_ask = float(asks[0][0]) if asks else None
        mid_p = float(self.mock_prices.get(req_symbol, (best_bid + best_ask) / 2.0 if (best_bid and best_ask) else req_price))

        is_crossing = False
        if req_type == "MARKET":
            is_crossing = True
        elif req_type == "LIMIT":
            if req_side == "BUY":
                if best_ask is not None:
                    is_crossing = (req_price >= best_ask)
                else:
                    is_crossing = (req_price >= mid_p) if mid_p > 0 else False
            elif req_side == "SELL":
                if best_bid is not None:
                    is_crossing = (req_price <= best_bid)
                else:
                    is_crossing = (req_price <= mid_p) if mid_p > 0 else False

        # Branch A: Crossing / Market Orders Fill Immediately
        if is_crossing:
            if req_side == "BUY":
                self.tmn_available -= (cost_tmn + fee_paid)
                self.crypto_balances[base_asset] = self.crypto_balances.get(base_asset, 0.0) + req_qty
            else:
                self.crypto_balances[base_asset] = max(0.0, self.crypto_balances.get(base_asset, 0.0) - req_qty)
                self.tmn_available += (cost_tmn - fee_paid)

            status = OrderStatus.FILLED
            filled_qty = req_qty
            avg_price = req_price

            trade_record = {
                "order_id": exchange_order_id,
                "client_id": req_cid,
                "symbol": req_symbol,
                "side": req_side,
                "price": req_price,
                "quantity": req_qty,
                "cost_tmn": cost_tmn,
                "fee_tmn": fee_paid,
                "timestamp": self._now(),
            }
            self.executed_orders.append(trade_record)

            return OrderExecutionResult(
                client_order_id=req_cid,
                exchange_order_id=exchange_order_id,
                symbol=req_symbol,
                status=status,
                filled_quantity=filled_qty,
                average_fill_price=avg_price,
                fee_paid_tmn=fee_paid,
                executed_at=self._now(),
                raw_response={"status": "FILLED", "order_id": exchange_order_id},
            )

        # Branch B: Resting LIMIT Orders (Lock Funds / Assets & Record in open_orders)
        if req_side == "BUY":
            self.tmn_available -= (cost_tmn + fee_paid)
            self.tmn_locked += (cost_tmn + fee_paid)
            locked_tmn = cost_tmn + fee_paid
            locked_coin = 0.0
        else:
            self.crypto_balances[base_asset] = max(0.0, self.crypto_balances.get(base_asset, 0.0) - req_qty)
            self.crypto_locked[base_asset] = self.crypto_locked.get(base_asset, 0.0) + req_qty
            locked_tmn = 0.0
            locked_coin = req_qty

        order_record = {
            "order_id": exchange_order_id,
            "client_id": req_cid,
            "symbol": req_symbol,
            "side": req_side,
            "price": req_price,
            "quantity": req_qty,
            "locked_tmn": locked_tmn,
            "locked_coin": locked_coin,
            "base_asset": base_asset,
            "status": "PENDING",
            "timestamp": self._now(),
        }
        self.open_orders[req_cid] = order_record

        return OrderExecutionResult(
            client_order_id=req_cid,
            exchange_order_id=exchange_order_id,
            symbol=req_symbol,
            status=OrderStatus.PENDING,
            filled_quantity=0.0,
            average_fill_price=req_price,
            fee_paid_tmn=0.0,
            executed_at=self._now(),
            raw_response=order_record,
        )

    async def get_open_orders(self, symbol: Optional[str] = None, priority: int = 1) -> List[Dict[str, Any]]:
        await self._check_faults_and_rate_limit(priority=priority)
        if symbol:
            return [o for o in self.open_orders.values() if o["symbol"] == symbol]
        return list(self.open_orders.values())

    async def get_order_status(self, client_order_id: str) -> Optional[OrderExecutionResult]:
        await self._check_faults_and_rate_limit(priority=1)
        if client_order_id in self.open_orders:
            o = self.open_orders[client_order_id]
            return OrderExecutionResult(
                client_order_id=client_order_id,
                exchange_order_id=o["order_id"],
                symbol=o["symbol"],
                status=OrderStatus.PENDING,
                filled_quantity=0.0,
                average_fill_price=o["price"],
                fee_paid_tmn=0.0,
                executed_at=o["timestamp"],
                raw_response=o,
            )
        for ex in self.executed_orders:
            if ex.get("client_id") == client_order_id:
                return OrderExecutionResult(
                    client_order_id=client_order_id,
                    exchange_order_id=ex["order_id"],
                    symbol=ex["symbol"],
                    status=OrderStatus.FILLED,
                    filled_quantity=ex["quantity"],
                    average_fill_price=ex["price"],
                    fee_paid_tmn=ex["fee_tmn"],
                    executed_at=ex["timestamp"],
                    raw_response=ex,
                )
        for c in self.cancelled_orders:
            if c.get("client_id") == client_order_id or c.get("order_id") == client_order_id:
                return OrderExecutionResult(
                    client_order_id=c.get("client_id", client_order_id),
                    exchange_order_id=c.get("order_id"),
                    symbol=c.get("symbol", ""),
                    status=OrderStatus.CANCELLED,
                    filled_quantity=0.0,
                    average_fill_price=0.0,
                    fee_paid_tmn=0.0,
                    executed_at=c.get("timestamp", self._now()),
                    raw_response=c,
                )
        return None

    async def cancel_order(
        self,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        priority: int = 0,
    ) -> bool:
        await self._check_faults_and_rate_limit(priority=priority)

        target_cid = None
        if client_order_id and client_order_id in self.open_orders:
            target_cid = client_order_id
        elif order_id:
            for cid, o in self.open_orders.items():
                if o.get("order_id") == order_id:
                    target_cid = cid
                    break

        if target_cid:
            o = self.open_orders.pop(target_cid)
            locked_tmn = o.get("locked_tmn", 0.0)
            if locked_tmn > 0:
                self.tmn_locked = max(0.0, self.tmn_locked - locked_tmn)
                self.tmn_available += locked_tmn

            locked_coin = o.get("locked_coin", 0.0)
            base_asset = o.get("base_asset")
            if locked_coin > 0 and base_asset:
                self.crypto_locked[base_asset] = max(0.0, self.crypto_locked.get(base_asset, 0.0) - locked_coin)
                self.crypto_balances[base_asset] = self.crypto_balances.get(base_asset, 0.0) + locked_coin

            cancel_record = {
                "order_id": o["order_id"],
                "client_id": target_cid,
                "symbol": o.get("symbol"),
                "timestamp": self._now(),
            }
            self.cancelled_orders.append(cancel_record)
            return True

        # Even if not found in open_orders, return True if order_id given (idempotent cancel)
        if order_id or client_order_id:
            self.cancelled_orders.append({
                "order_id": order_id or client_order_id,
                "client_id": client_order_id or order_id,
                "timestamp": self._now(),
            })
            return True
        return False

    async def cancel_all_orders(self, symbol: Optional[str] = None, priority: int = 0) -> int:
        await self._check_faults_and_rate_limit(priority=priority)
        to_cancel = [cid for cid, o in self.open_orders.items() if (symbol is None or o["symbol"] == symbol)]
        cancelled = 0
        for cid in to_cancel:
            if await self.cancel_order(client_order_id=cid, priority=priority):
                cancelled += 1
        return cancelled

    async def health_check(self) -> bool:
        if self.fault_injection["network_error"]:
            return False
        return True
