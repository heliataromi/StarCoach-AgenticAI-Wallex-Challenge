"""Canonical interface contracts, data models, mock clients, and mathematical oracles
for the Wallex Algorithmic Trading test suite.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# ==============================================================================
# 1. Interface Contracts (Canonical Definitions from PROJECT.md & SPECIFICATION.md)
# ==============================================================================

class MarketRegime(str, Enum):
    BULLISH_TREND = "BULLISH_TREND"
    RANGING_SIDEWAYS = "RANGING_SIDEWAYS"
    HIGH_VOLATILITY_PANIC = "HIGH_VOLATILITY_PANIC"
    PRE_DEADLINE_LIQUIDATION = "PRE_DEADLINE_LIQUIDATION"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str                              # e.g., "BTCTMN", "ETHTMN"
    timestamp: float                         # Unix epoch in milliseconds
    best_bid: float
    best_ask: float
    spread_pct: float                        # (best_ask - best_bid) / best_bid
    bids: List[Tuple[float, float]]          # Top 10 [(price, volume), ...]
    asks: List[Tuple[float, float]]          # Top 10 [(price, volume), ...]
    imbalance_ratio: float                   # (BidVol - AskVol) / (BidVol + AskVol)
    recent_candles: List[Dict[str, Any]]     # 15m OHLCV records
    is_stale: bool = False


@dataclass(frozen=True)
class AccountBalance:
    toman_available: float                   # Available TMN
    toman_locked: float                      # TMN locked in open orders
    crypto_balances: Dict[str, float]        # e.g. {"BTC": 0.00025, "ETH": 0.0}
    total_equity_tmn: float                  # Mark-to-market equity in TMN
    timestamp: float


@dataclass
class MacroTradingPolicy:
    regime: MarketRegime
    target_symbols: List[str]
    max_position_size_pct: float             # <= 0.20
    risk_multiplier: float                   # 0.5 to 1.5
    agent_thesis: str                        # LLM or rule explanation
    generated_at: float
    valid_until: float


@dataclass
class CandidateSignal:
    signal_id: str
    symbol: str
    action: str                              # "BUY" or "SELL"
    entry_price: float
    target_price: float
    stop_loss_price: float
    estimated_win_prob: float                # 0.0 to 1.0
    regime_source: MarketRegime
    indicators: Dict[str, float]             # RSI, EMA9, EMA21, ATR, Imbalance
    timestamp: float


@dataclass
class TradeProposal:
    proposal_id: str
    signal: CandidateSignal
    allocated_size_tmn: float                # Sized in TMN (<= 20% equity, max 1M TMN)
    quantity: float                          # Crypto quantity
    expected_gain_pct: float                 # (target - entry) / entry
    expected_loss_pct: float                 # (entry - stop) / entry
    fee_pct: float                           # 0.0050 round-trip
    expected_value: float                    # JEV score >= +0.0035
    reward_to_risk: float                    # Gain / Loss >= 1.5
    is_approved: bool
    rejection_reason: Optional[str] = None
    evaluated_at: float = 0.0


@dataclass
class OrderExecutionResult:
    client_order_id: str
    exchange_order_id: Optional[str]
    symbol: str
    status: OrderStatus
    filled_quantity: float
    average_fill_price: float
    fee_paid_tmn: float
    executed_at: float
    raw_response: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# 2. Mathematical Oracles & Authoritative Calculators
# ==============================================================================

def oracle_calc_imbalance(bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> float:
    """Computes orderbook imbalance ratio: (BidVol - AskVol) / (BidVol + AskVol)."""
    bid_vol = sum(v for _, v in bids)
    ask_vol = sum(v for _, v in asks)
    total_vol = bid_vol + ask_vol
    if total_vol <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total_vol


def oracle_calc_jev(win_prob: float, entry: float, target: float, stop: float, fee_pct: float = 0.0050) -> float:
    """Calculates Joint Expected Value (JEV):
    E[V] = P(Win) * GainPct - (1 - P(Win)) * LossPct - RoundTripFees
    """
    if entry <= 0:
        return -1.0
    gain_pct = (target - entry) / entry
    loss_pct = (entry - stop) / entry
    return (win_prob * gain_pct) - ((1.0 - win_prob) * loss_pct) - fee_pct


def oracle_calc_reward_to_risk(entry: float, target: float, stop: float) -> float:
    """Calculates potential Reward-to-Risk ratio: (Target - Entry) / (Entry - Stop)."""
    downside = entry - stop
    if downside <= 0:
        return 0.0
    upside = target - entry
    return upside / downside


def oracle_calc_drawdown(peak_equity: float, current_equity: float) -> float:
    """Calculates peak-to-trough drawdown ratio: (Peak - Current) / Peak."""
    if peak_equity <= 0:
        return 0.0
    if current_equity >= peak_equity:
        return 0.0
    return (peak_equity - current_equity) / peak_equity


def oracle_calc_dynamic_atr_stop(entry: float, atr_14: float, max_pct: float = 0.02) -> float:
    """Calculates dynamic stop distance: min(1.2 * ATR_14, 2.0% * entry)."""
    distance = min(1.2 * atr_14, max_pct * entry)
    return entry - distance


# ==============================================================================
# 3. Deterministic Fake Clock
# ==============================================================================

class FakeClock:
    """Deterministic time provider for testing rate limiting, TTLs, and liquidation stages."""

    def __init__(self, start_timestamp: float = 1700000000.0) -> None:
        self._current_time = start_timestamp

    def time(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Time cannot travel backwards")
        self._current_time += seconds

    def set_time(self, timestamp: float) -> None:
        self._current_time = timestamp


# ==============================================================================
# 4. High-Fidelity Mock Wallex Client & Rate Limiter
# ==============================================================================

class MockTokenBucketRateLimiter:
    """Token bucket rate limiter simulating Wallex 30 requests / 10s rule."""

    def __init__(self, capacity: int = 30, window_seconds: float = 10.0, clock: Optional[FakeClock] = None) -> None:
        self.capacity = capacity
        self.window_seconds = window_seconds
        self.refill_rate = capacity / window_seconds  # 3.0 tokens/sec
        self.tokens = float(capacity)
        self.clock = clock
        self.last_refill = clock.time() if clock else time.time()
        self.request_count = 0
        self.throttle_count = 0

    def _current_time(self) -> float:
        return self.clock.time() if self.clock else time.time()

    def _refill(self) -> None:
        now = self._current_time()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(float(self.capacity), self.tokens + (elapsed * self.refill_rate))
            self.last_refill = now

    def acquire(self) -> bool:
        """Attempts to acquire 1 request token. Returns True if allowed, False if throttled."""
        self._refill()
        self.request_count += 1
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        else:
            self.throttle_count += 1
            return False


class MockWallexClient:
    """High-fidelity in-memory client simulating Wallex REST & JSON-RPC 2.0 MCP endpoints.
    Operates 100% offline without live network or external credentials.
    """

    def __init__(
        self,
        api_key: str = "mock-api-key-test",
        sub_account_id: str = "hackathon-sub-5m",
        clock: Optional[FakeClock] = None,
    ) -> None:
        self.api_key = api_key
        self.sub_account_id = sub_account_id
        self.clock = clock or FakeClock()
        self.rate_limiter = MockTokenBucketRateLimiter(capacity=30, window_seconds=10.0, clock=self.clock)

        # Dedicated 5,000,000 TMN Account State
        self.initial_tmn = 5_000_000.0
        self.tmn_available = 5_000_000.0
        self.tmn_locked = 0.0
        self.crypto_balances: Dict[str, float] = {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0}

        # Order Book Repositories
        self.orderbooks: Dict[str, Dict[str, Any]] = self._init_default_orderbooks()
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.executed_orders: List[Dict[str, Any]] = []
        self.cancelled_orders: List[Dict[str, Any]] = []

    def _init_default_orderbooks(self) -> Dict[str, Dict[str, Any]]:
        return {
            "BTCTMN": {
                "bids": [(4_200_000_000.0, 0.05), (4_195_000_000.0, 0.08), (4_190_000_000.0, 0.12)],
                "asks": [(4_210_000_000.0, 0.04), (4_215_000_000.0, 0.07), (4_220_000_000.0, 0.10)],
            },
            "ETHTMN": {
                "bids": [(180_000_000.0, 0.50), (179_500_000.0, 0.80)],
                "asks": [(180_500_000.0, 0.40), (181_000_000.0, 0.70)],
            },
            "USDTTMN": {
                "bids": [(65_000.0, 1000.0), (64_900.0, 2000.0)],
                "asks": [(65_100.0, 800.0), (65_200.0, 1500.0)],
            },
        }

    def set_orderbook(self, symbol: str, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> None:
        self.orderbooks[symbol] = {"bids": bids, "asks": asks}

    async def get_market_depth(self, symbol: str) -> MarketDataSnapshot:
        """Simulates `wallex_basic_get_market_depth` MCP tool."""
        if not self.rate_limiter.acquire():
            raise RuntimeError("HTTP 429: Too Many Requests - Token bucket exhausted")

        ob = self.orderbooks.get(symbol)
        if not ob:
            raise ValueError(f"Unknown symbol: {symbol}")

        bids = ob["bids"]
        asks = ob["asks"]
        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        spread_pct = (best_ask - best_bid) / best_bid if best_bid > 0 else 0.0
        imbalance = oracle_calc_imbalance(bids, asks)

        return MarketDataSnapshot(
            symbol=symbol,
            timestamp=self.clock.time() * 1000.0,
            best_bid=best_bid,
            best_ask=best_ask,
            spread_pct=spread_pct,
            bids=bids,
            asks=asks,
            imbalance_ratio=imbalance,
            recent_candles=[],
        )

    async def get_account_balance(self) -> AccountBalance:
        """Simulates `wallex_credit_sub_account_balances_details`."""
        if not self.rate_limiter.acquire():
            raise RuntimeError("HTTP 429: Too Many Requests")

        btc_price = self.orderbooks["BTCTMN"]["bids"][0][0] if self.orderbooks["BTCTMN"]["bids"] else 4_200_000_000.0
        eth_price = self.orderbooks["ETHTMN"]["bids"][0][0] if self.orderbooks["ETHTMN"]["bids"] else 180_000_000.0
        usdt_price = self.orderbooks["USDTTMN"]["bids"][0][0] if self.orderbooks["USDTTMN"]["bids"] else 65_000.0

        crypto_value_tmn = (
            self.crypto_balances.get("BTC", 0.0) * btc_price
            + self.crypto_balances.get("ETH", 0.0) * eth_price
            + self.crypto_balances.get("USDT", 0.0) * usdt_price
        )

        total_equity = self.tmn_available + self.tmn_locked + crypto_value_tmn

        return AccountBalance(
            toman_available=self.tmn_available,
            toman_locked=self.tmn_locked,
            crypto_balances=dict(self.crypto_balances),
            total_equity_tmn=total_equity,
            timestamp=self.clock.time(),
        )

    async def post_order(
        self,
        symbol: str,
        side: Union[OrderSide, str],
        order_type: Union[OrderType, str],
        price: float,
        quantity: float,
        client_order_id: str,
    ) -> OrderExecutionResult:
        """Simulates `wallex_spot_post_account_orders`."""
        if not self.rate_limiter.acquire():
            raise RuntimeError("HTTP 429: Too Many Requests")

        side_str = side.value if isinstance(side, OrderSide) else side
        base_asset = symbol.replace("TMN", "")
        cost_tmn = price * quantity

        if side_str == "BUY":
            if cost_tmn > self.tmn_available:
                return OrderExecutionResult(
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    symbol=symbol,
                    status=OrderStatus.REJECTED,
                    filled_quantity=0.0,
                    average_fill_price=0.0,
                    fee_paid_tmn=0.0,
                    executed_at=self.clock.time(),
                    raw_response={"error": "Insufficient TMN available balance"},
                )

        if side_str == "SELL":
            current_coin = self.crypto_balances.get(base_asset, 0.0)
            if quantity > current_coin + 1e-9:
                return OrderExecutionResult(
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    symbol=symbol,
                    status=OrderStatus.REJECTED,
                    filled_quantity=0.0,
                    average_fill_price=0.0,
                    fee_paid_tmn=0.0,
                    executed_at=self.clock.time(),
                    raw_response={"error": f"Insufficient {base_asset} balance"},
                )

        fee_rate = 0.0025
        fee_paid = cost_tmn * fee_rate

        if side_str == "BUY":
            self.tmn_available -= (cost_tmn + fee_paid)
            self.crypto_balances[base_asset] = self.crypto_balances.get(base_asset, 0.0) + quantity
        else:
            self.crypto_balances[base_asset] = max(0.0, self.crypto_balances.get(base_asset, 0.0) - quantity)
            self.tmn_available += (cost_tmn - fee_paid)

        exchange_order_id = f"WX-{int(self.clock.time() * 1000)}-{len(self.executed_orders) + 1}"
        result = OrderExecutionResult(
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            symbol=symbol,
            status=OrderStatus.FILLED,
            filled_quantity=quantity,
            average_fill_price=price,
            fee_paid_tmn=fee_paid,
            executed_at=self.clock.time(),
            raw_response={"status": "FILLED", "order_id": exchange_order_id},
        )
        self.executed_orders.append({
            "order_id": exchange_order_id,
            "client_id": client_order_id,
            "symbol": symbol,
            "side": side_str,
            "price": price,
            "quantity": quantity,
            "cost_tmn": cost_tmn,
            "fee_tmn": fee_paid,
            "timestamp": self.clock.time(),
        })
        return result

    async def cancel_order(self, order_id: str) -> bool:
        """Simulates `wallex_spot_delete_account_orders`."""
        if not self.rate_limiter.acquire():
            raise RuntimeError("HTTP 429: Too Many Requests")
        self.cancelled_orders.append({"order_id": order_id, "timestamp": self.clock.time()})
        return True


# ==============================================================================
# 5. Immutable Decision Logger
# ==============================================================================

class MockDecisionAuditor:
    """Mock implementation of the Decision Auditor saving immutable JSON records
    for 100% of trades under a target directory.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.recorded_trades: List[Dict[str, Any]] = []

    def record_decision(
        self,
        trade_id: str,
        symbol: str,
        action: str,
        order_type: str,
        price: float,
        quantity: float,
        total_tmn: float,
        macro_state: Dict[str, Any],
        micro_signals: Dict[str, Any],
        jev_evaluation: Dict[str, Any],
        risk_guardrails: Dict[str, Any],
    ) -> Path:
        log_entry = {
            "log_version": "1.0",
            "trade_id": trade_id,
            "timestamp": time.time(),
            "market_pair": symbol,
            "action": action,
            "order_type": order_type,
            "price": price,
            "quantity": quantity,
            "total_tmn": total_tmn,
            "macro_state": macro_state,
            "micro_signals": micro_signals,
            "jev_evaluation": jev_evaluation,
            "risk_guardrails": risk_guardrails,
        }
        filename = f"{trade_id}.json"
        target_path = self.log_dir / filename
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, indent=2)

        self.recorded_trades.append(log_entry)
        return target_path

    def verify_audit_completeness(self, exchange_trades: List[Dict[str, Any]]) -> Tuple[bool, int, int]:
        logged_ids = {t["trade_id"] for t in self.recorded_trades}
        all_matched = True
        for tx in exchange_trades:
            tid = tx.get("client_id") or tx.get("order_id")
            if tid not in logged_ids:
                all_matched = False
        return all_matched, len(exchange_trades), len(self.recorded_trades)
