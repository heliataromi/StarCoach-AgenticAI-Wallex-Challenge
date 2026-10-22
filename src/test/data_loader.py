"""Historical and synthetic market data loader for backtesting and simulation.

Adheres to Ponytail minimalism: parses TradingView UDF format, loads offline fixtures,
and generates deterministic multi-regime synthetic datasets for repeatable testing.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import random
import time
from typing import Dict, List, Optional, Tuple
from src.wallex.models import Candle, DepthLevel, OrderBook


def parse_udf_candles(raw_data: Dict[str, Any], symbol: str = "BTCTMN") -> List[Candle]:
    """Parses Wallex TradingView UDF response payload into a list of Candle objects.

    Payload format:
    {"s": "ok", "t": [...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}
    """
    if not isinstance(raw_data, dict) or raw_data.get("s") != "ok":
        return []

    timestamps = raw_data.get("t", [])
    opens = raw_data.get("o", [])
    highs = raw_data.get("h", [])
    lows = raw_data.get("l", [])
    closes = raw_data.get("c", [])
    volumes = raw_data.get("v", [])

    n = min(len(timestamps), len(opens), len(highs), len(lows), len(closes))
    candles: List[Candle] = []

    for i in range(n):
        ts = float(timestamps[i])
        # Convert seconds to milliseconds if needed
        ts_ms = ts * 1000.0 if ts < 1e11 else ts
        o = float(opens[i])
        h = float(highs[i])
        l = float(lows[i])
        c = float(closes[i])
        v = float(volumes[i]) if i < len(volumes) else 1.0

        candles.append(
            Candle(
                timestamp=ts_ms,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                resolution="15",
            )
        )
    return candles


def load_candles_from_file(file_path: str | Path, symbol: str = "BTCTMN") -> List[Candle]:
    """Loads and parses candles from an offline JSON fixture file."""
    path = Path(file_path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "s" in data:
        return parse_udf_candles(data, symbol)
    elif isinstance(data, list):
        candles: List[Candle] = []
        for item in data:
            if isinstance(item, dict):
                candles.append(
                    Candle(
                        timestamp=float(item.get("timestamp", item.get("t", 0.0))),
                        open=float(item.get("open", item.get("o", 0.0))),
                        high=float(item.get("high", item.get("h", 0.0))),
                        low=float(item.get("low", item.get("l", 0.0))),
                        close=float(item.get("close", item.get("c", 0.0))),
                        volume=float(item.get("volume", item.get("v", 1.0))),
                        resolution=str(item.get("resolution", "15")),
                    )
                )
        return candles
    return []


def generate_synthetic_market_data(
    symbol: str = "BTCTMN",
    base_price: float = 4_200_000_000.0,
    num_candles: int = 300,
    seed: int = 42,
    interval_seconds: int = 900,  # 15 minutes
) -> List[Candle]:
    """Generates a deterministic 3-phase synthetic market history for repeatable backtesting.

    - Phase 1 (0 to 100): RANGING_SIDEWAYS (Oscillations within ±1.5% range)
    - Phase 2 (100 to 220): BULLISH_TREND (Upward trend with pullbacks, +18% gain)
    - Phase 3 (220 to 300): HIGH_VOLATILITY_PANIC (Sharp dump -8% with high ATR expansion)
    """
    rng = random.Random(seed)
    candles: List[Candle] = []
    curr_price = float(base_price)
    start_ts = 1700000000.0  # Unix timestamp in seconds

    for i in range(num_candles):
        step_ts = (start_ts + (i * interval_seconds)) * 1000.0

        if i < 100:
            # Phase 1: Range-bound consolidation
            drift = math.sin(i * 0.2) * (base_price * 0.003)
            noise = rng.uniform(-0.002, 0.002) * base_price
            close_price = base_price + drift + noise
            spread = close_price * 0.002
            vol = rng.uniform(0.5, 2.0)
        elif i < 220:
            # Phase 2: Steady Bullish Trend
            trend_step = (i - 100) * (base_price * 0.0015)  # Steady +0.15% per step
            pullback = math.sin(i * 0.5) * (base_price * 0.004)
            close_price = base_price + trend_step + pullback
            spread = close_price * 0.003
            vol = rng.uniform(1.5, 5.0)  # Higher volume during trend
        else:
            # Phase 3: High-Volatility Panic Dump
            crash_step = (i - 220) * (base_price * 0.002)
            panic_noise = rng.uniform(-0.015, 0.005) * base_price
            close_price = max(base_price * 0.8, (base_price * 1.15) - crash_step + panic_noise)
            spread = close_price * 0.010  # Wide spread during panic
            vol = rng.uniform(3.0, 10.0)

        open_price = curr_price
        high_price = max(open_price, close_price) + (spread * 0.5)
        low_price = min(open_price, close_price) - (spread * 0.5)

        candles.append(
            Candle(
                timestamp=step_ts,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=vol,
                resolution="15",
            )
        )
        curr_price = close_price

    return candles


def create_orderbook_for_price(
    symbol: str,
    price: float,
    spread_pct: float = 0.001,
    imbalance: float = 0.0,
    timestamp_ms: Optional[float] = None,
) -> OrderBook:
    """Constructs a realistic OrderBook around a given market price."""
    half_spread = price * (spread_pct / 2.0)
    best_bid = price - half_spread
    best_ask = price + half_spread
    ts = timestamp_ms or (time.time() * 1000.0)

    # Imbalance weighting
    bid_weight = 1.0 + max(-0.8, min(0.8, imbalance))
    ask_weight = 1.0 - max(-0.8, min(0.8, imbalance))

    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []

    for depth in range(1, 6):
        b_p = best_bid * (1.0 - (depth - 1) * 0.0005)
        b_v = (1.0 + (depth * 0.2)) * bid_weight
        bids.append((b_p, b_v))

        a_p = best_ask * (1.0 + (depth - 1) * 0.0005)
        a_v = (1.0 + (depth * 0.2)) * ask_weight
        asks.append((a_p, a_v))

    return OrderBook(
        symbol=symbol,
        timestamp=ts,
        bids=bids,
        asks=asks,
    )


def parse_wallex_depth(
    payload: Dict[str, Any],
    symbol: str = "BTCTMN",
    timestamp_ms: float = 0.0,
) -> OrderBook:
    """Ingests both live Wallex /v1/depth JSON and offline normalized depth fixtures."""
    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []

    # Live Wallex API: payload["result"]["bid"] and payload["result"]["ask"]
    if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
        res = payload["result"]
        for b in res.get("bid", []):
            bids.append((float(b["price"]), float(b["quantity"])))
        for a in res.get("ask", []):
            asks.append((float(a["price"]), float(a["quantity"])))

    # Offline fixture: payload["bids"] and payload["asks"]
    elif isinstance(payload, dict) and "bids" in payload and "asks" in payload:
        bids = [(float(p), float(q)) for p, q in payload["bids"]]
        asks = [(float(p), float(q)) for p, q in payload["asks"]]

    # Ensure sorting: bids descending, asks ascending
    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    ts = timestamp_ms or float(payload.get("timestamp", time.time() * 1000.0) if isinstance(payload, dict) else (time.time() * 1000.0))
    return OrderBook(
        symbol=symbol,
        timestamp=ts,
        bids=bids[:10],
        asks=asks[:10],
    )

