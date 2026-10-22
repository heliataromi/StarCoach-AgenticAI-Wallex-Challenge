"""Technical indicator library implemented with zero-dependency pure Python math.

Following the Ponytail principle: transparent, ultra-fast, robust against edge cases (empty or short series, zero variance, NaN values).
"""
from __future__ import annotations

import math
from typing import Any, List, Sequence, Tuple
from src.wallex.models import DepthLevel


def calculate_sma(prices: Sequence[float], period: int) -> float:
    """Calculates Simple Moving Average of the most recent `period` prices."""
    if not prices or period <= 0:
        return 0.0
    slice_prices = prices[-period:]
    return sum(slice_prices) / len(slice_prices)


def calculate_ema(prices: Sequence[float], period: int) -> List[float]:
    """Calculates Exponential Moving Average across the price sequence.
    Returns list of EMA values matching the length of `prices`.
    """
    if not prices or period <= 0:
        return []
    
    ema_values: List[float] = []
    multiplier = 2.0 / (period + 1.0)
    
    # Initialize first valid EMA with SMA of first min(period, len(prices))
    first_window = prices[:period]
    current_ema = sum(first_window) / len(first_window)
    
    for i, p in enumerate(prices):
        if i < period - 1:
            # During warm-up phase, return rolling SMA
            ema_values.append(sum(prices[:i + 1]) / (i + 1))
        elif i == period - 1:
            ema_values.append(current_ema)
        else:
            current_ema = (p - current_ema) * multiplier + current_ema
            ema_values.append(current_ema)
            
    return ema_values


def calculate_rsi(prices: Sequence[float], period: int = 14) -> float:
    """Calculates Relative Strength Index (RSI) in range [0.0, 100.0].
    Defaults to 50.0 (neutral) if insufficient data.
    """
    if len(prices) < period + 1 or period <= 0:
        return 50.0

    # Calculate price changes
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent_changes = changes[-period:]

    gains = [max(0.0, ch) for ch in recent_changes]
    losses = [max(0.0, -ch) for ch in recent_changes]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    if avg_gain == 0.0:
        return 0.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return max(0.0, min(100.0, rsi))


def calculate_bollinger_bands(
    prices: Sequence[float], period: int = 20, num_std: float = 2.0
) -> Tuple[float, float, float]:
    """Calculates Bollinger Bands: (lower_band, middle_band, upper_band).
    Middle band is SMA(period), bands are ± num_std sample standard deviations.
    """
    if not prices or period <= 0:
        return (0.0, 0.0, 0.0)

    window = list(prices[-period:])
    n = len(window)
    sma = sum(window) / n
    if n < 2:
        return (sma, sma, sma)

    variance = sum((x - sma) ** 2 for x in window) / (n - 1)
    std_dev = math.sqrt(max(0.0, variance))

    upper_band = sma + (num_std * std_dev)
    lower_band = sma - (num_std * std_dev)
    return (lower_band, sma, upper_band)


def calculate_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    """Calculates Average True Range (ATR) over high, low, close series."""
    n = len(closes)
    if n < 2 or len(highs) < n or len(lows) < n or period <= 0:
        if n >= 1:
            return max(0.0, highs[-1] - lows[-1])
        return 0.0

    true_ranges: List[float] = []
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        prev_close = closes[i - 1]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    recent_tr = true_ranges[-period:]
    return sum(recent_tr) / len(recent_tr)


def calculate_order_book_imbalance(
    bids: Sequence[Any],
    asks: Sequence[Any],
    depth: int = 5,
) -> float:
    """Calculates Order Book Imbalance (OBI) metric in range [-1.0, 1.0].
    Handles both (price, volume) tuples and DepthLevel objects.
    Positive values represent bid-side buy pressure; negative represent ask-side sell pressure.
    """
    if not bids and not asks:
        return 0.0

    def _get_vol(level: Any) -> float:
        if isinstance(level, (tuple, list)):
            return float(level[1])
        if hasattr(level, "quantity"):
            return float(level.quantity)
        if hasattr(level, "volume"):
            return float(level.volume)
        return float(level)

    bid_vol = sum(_get_vol(b) for b in bids[:depth])
    ask_vol = sum(_get_vol(a) for a in asks[:depth])
    total_vol = bid_vol + ask_vol

    if total_vol <= 0.0:
        return 0.0

    return (bid_vol - ask_vol) / total_vol
