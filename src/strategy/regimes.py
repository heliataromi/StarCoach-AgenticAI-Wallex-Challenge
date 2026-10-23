"""Latent Market Regime Classifier.

Classifies market state into discrete regimes:
1. BULLISH_TREND: Momentum breakout and upward trend following
2. RANGING_SIDEWAYS: Range-bound consolidation and mean-reversion
3. HIGH_VOLATILITY_PANIC: Severe sell-off, high ATR expansion, cash preservation
"""
from __future__ import annotations

from typing import List, Optional, Tuple
from src.strategy.models import MarketRegime
from src.strategy.indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_order_book_imbalance,
)
from src.wallex.models import Candle, OrderBook


class LatentRegimeClassifier:
    """Classifies live and historical market conditions into latent trading regimes."""

    def __init__(
        self,
        ema_fast_period: int = 9,
        ema_slow_period: int = 21,
        rsi_period: int = 14,
        atr_period: int = 14,
        panic_drop_threshold_pct: float = 0.025,  # 2.5% drop in recent window triggers panic
        panic_normalized_atr_threshold: float = 0.030,  # 3.0% normalized ATR
        panic_obi_threshold: float = -0.60,
    ) -> None:
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.panic_drop_threshold_pct = panic_drop_threshold_pct
        self.panic_normalized_atr_threshold = panic_normalized_atr_threshold
        self.panic_obi_threshold = panic_obi_threshold

    def classify(
        self,
        candles: List[Candle],
        orderbook: Optional[OrderBook] = None,
    ) -> Tuple[MarketRegime, float, str]:
        """Classifies the market regime from candle history and current order book depth.

        Returns:
            Tuple of (MarketRegime, confidence [0.0-1.0], rationale string).
        """
        if not candles or len(candles) < 5:
            # Insufficient data: default safely to RANGING with low confidence
            return MarketRegime.RANGING_SIDEWAYS, 0.5, "Insufficient candle history; defaulting to RANGING_SIDEWAYS"

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        current_price = closes[-1]

        # Calculate Indicators
        ema_fast = calculate_ema(closes, self.ema_fast_period)
        ema_slow = calculate_ema(closes, self.ema_slow_period)
        rsi = calculate_rsi(closes, self.rsi_period)
        atr = calculate_atr(highs, lows, closes, self.atr_period)
        lower_bb, mid_bb, upper_bb = calculate_bollinger_bands(closes, period=20)
        
        normalized_atr = (atr / current_price) if current_price > 0 else 0.0

        obi = 0.0
        spread_pct = 0.0
        if orderbook and orderbook.bids and orderbook.asks:
            obi = calculate_order_book_imbalance(orderbook.bids, orderbook.asks, depth=5)
            spread_pct = orderbook.spread / current_price if current_price > 0 else 0.0

        # Recent drawdown in last 10 candles
        lookback = min(10, len(closes))
        peak_close = max(closes[-lookback:])
        recent_drop_pct = (peak_close - current_price) / peak_close if peak_close > 0 else 0.0

        # =====================================================================
        # Check 1: HIGH_VOLATILITY_PANIC Gate
        # =====================================================================
        is_flash_crash = recent_drop_pct >= self.panic_drop_threshold_pct
        is_vol_explosion = normalized_atr >= self.panic_normalized_atr_threshold
        is_bid_collapse = (obi <= self.panic_obi_threshold) and (spread_pct > 0.005)

        if is_flash_crash or is_vol_explosion or is_bid_collapse:
            reasons = []
            confidence = 0.70
            if is_flash_crash:
                reasons.append(f"sharp drop {recent_drop_pct * 100:.2f}% >= {self.panic_drop_threshold_pct * 100:.1f}%")
                confidence += 0.15
            if is_vol_explosion:
                reasons.append(f"normalized ATR {normalized_atr * 100:.2f}% >= {self.panic_normalized_atr_threshold * 100:.1f}%")
                confidence += 0.10
            if is_bid_collapse:
                reasons.append(f"bid depletion OBI {obi:.2f} <= {self.panic_obi_threshold:.2f}")
                confidence += 0.05

            return (
                MarketRegime.HIGH_VOLATILITY_PANIC,
                min(1.0, confidence),
                f"Panic condition detected: {', '.join(reasons)}. Enforce cash preservation.",
            )

        # =====================================================================
        # Check 2: BULLISH_TREND Gate
        # =====================================================================
        curr_fast = ema_fast[-1] if ema_fast else current_price
        curr_slow = ema_slow[-1] if ema_slow else current_price
        prev_fast = ema_fast[-2] if len(ema_fast) >= 2 else curr_fast

        ema_bullish_cross = curr_fast > curr_slow
        fast_ema_rising = curr_fast >= prev_fast
        rsi_bullish = rsi >= 48.0
        price_above_slow = current_price >= curr_slow

        if ema_bullish_cross and fast_ema_rising and rsi_bullish and price_above_slow:
            # Score trend confidence
            trend_separation = (curr_fast - curr_slow) / current_price if current_price > 0 else 0.0
            confidence = 0.65
            if trend_separation > 0.005:  # >0.5% separation
                confidence += 0.15
            if obi > 0.10:  # Bid depth confirmation
                confidence += 0.10
            if 55.0 <= rsi <= 70.0:
                confidence += 0.10

            return (
                MarketRegime.BULLISH_TREND,
                min(1.0, confidence),
                f"Bullish trend confirmed: EMA{self.ema_fast_period} ({curr_fast:.1f}) > EMA{self.ema_slow_period} ({curr_slow:.1f}), RSI={rsi:.1f}, OBI={obi:.2f}",
            )

        # =====================================================================
        # Check 3: RANGING_SIDEWAYS (Default Mean-Reversion Mode)
        # =====================================================================
        bb_width_pct = (upper_bb - lower_bb) / mid_bb if mid_bb > 0 else 0.0
        confidence = 0.70
        if bb_width_pct < 0.04:  # Tight consolidating band
            confidence += 0.15
        if 40.0 <= rsi <= 60.0:
            confidence += 0.10

        return (
            MarketRegime.RANGING_SIDEWAYS,
            min(1.0, confidence),
            f"Range-bound consolidation: BB width={bb_width_pct * 100:.2f}%, RSI={rsi:.1f}, OBI={obi:.2f}",
        )
