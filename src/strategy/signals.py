"""Multi-Regime Signal Generator.

Produces discrete TradingSignals tailored to each latent market regime:
- Regime 1 (BULLISH_TREND): Trend momentum breakout & pullback continuation
- Regime 2 (RANGING_SIDEWAYS): Bollinger Band lower boundary mean-reversion with OBI bid support
- Regime 3 (HIGH_VOLATILITY_PANIC): Cash preservation sentinel (Hold cash or liquidate)
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional
from src.strategy.models import MarketRegime, SignalAction, TradingSignal
from src.strategy.indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_order_book_imbalance,
)
from src.wallex.models import Candle, OrderBook


class MultiRegimeSignalGenerator:
    """Generates execution-ready trading signals strictly adhering to the active regime."""

    def __init__(
        self,
        min_profit_hurdle_pct: float = 0.010,  # 1.0% minimum target upside to ensure JEV hurdle
    ) -> None:
        self.min_profit_hurdle_pct = min_profit_hurdle_pct

    def generate_signal(
        self,
        symbol: str,
        regime: MarketRegime,
        candles: List[Candle],
        orderbook: Optional[OrderBook] = None,
        has_open_position: bool = False,
        entry_price: float = 0.0,
    ) -> TradingSignal:
        """Evaluates price action, indicators, and orderbook to produce a TradingSignal."""
        now = time.time()
        if not candles or len(candles) < 5:
            return TradingSignal(
                symbol=symbol,
                action=SignalAction.HOLD,
                regime=regime,
                current_price=0.0,
                target_price=0.0,
                stop_loss_price=0.0,
                estimated_win_probability=0.0,
                confidence=0.0,
                rationale="Insufficient candle data",
                timestamp=now,
            )

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        current_price = closes[-1]

        # Indicator Calculations
        ema9_series = calculate_ema(closes, 9)
        ema21_series = calculate_ema(closes, 21)
        ema9 = ema9_series[-1] if ema9_series else current_price
        ema21 = ema21_series[-1] if ema21_series else current_price
        rsi = calculate_rsi(closes, 14)
        atr = calculate_atr(highs, lows, closes, 14)
        if atr <= 0.0 or atr < (current_price * 0.002):
            atr = max(current_price * 0.008, 1.0)  # Conservative fallback ATR (0.8%)

        lower_bb, mid_bb, upper_bb = calculate_bollinger_bands(closes, 20)

        obi = 0.0
        best_bid = current_price
        best_ask = current_price
        if orderbook:
            if hasattr(orderbook, "best_bid") and orderbook.best_bid is not None:
                best_bid = float(orderbook.best_bid)
            elif orderbook.bids:
                b0 = orderbook.bids[0]
                best_bid = float(b0[0] if isinstance(b0, (tuple, list)) else b0.price)

            if hasattr(orderbook, "best_ask") and orderbook.best_ask is not None:
                best_ask = float(orderbook.best_ask)
            elif orderbook.asks:
                a0 = orderbook.asks[0]
                best_ask = float(a0[0] if isinstance(a0, (tuple, list)) else a0.price)

            if orderbook.bids or orderbook.asks:
                obi = calculate_order_book_imbalance(orderbook.bids, orderbook.asks, depth=5)

        indicators_snapshot: Dict[str, float] = {
            "ema9": ema9,
            "ema21": ema21,
            "rsi": rsi,
            "atr": atr,
            "lower_bb": lower_bb,
            "mid_bb": mid_bb,
            "upper_bb": upper_bb,
            "obi": obi,
            "best_bid": best_bid,
            "best_ask": best_ask,
        }

        # =====================================================================
        # Strategy 3: HIGH_VOLATILITY_PANIC (Cash Guard)
        # =====================================================================
        if regime == MarketRegime.HIGH_VOLATILITY_PANIC:
            if has_open_position:
                return TradingSignal(
                    symbol=symbol,
                    action=SignalAction.SELL,
                    regime=regime,
                    current_price=current_price,
                    target_price=current_price,
                    stop_loss_price=current_price * 1.05,
                    estimated_win_probability=0.85,
                    confidence=0.95,
                    rationale="Emergency panic de-risking: liquidate open position to Iranian Toman",
                    indicators=indicators_snapshot,
                    timestamp=now,
                )
            return TradingSignal(
                symbol=symbol,
                action=SignalAction.HOLD,
                regime=regime,
                current_price=current_price,
                target_price=0.0,
                stop_loss_price=0.0,
                estimated_win_probability=0.0,
                confidence=1.0,
                rationale="Panic regime active: strictly 100% Iranian Toman cash preservation",
                indicators=indicators_snapshot,
                timestamp=now,
            )

        # =====================================================================
        # Strategy 1: BULLISH_TREND (Momentum & Trend Continuation)
        # =====================================================================
        if regime == MarketRegime.BULLISH_TREND:
            if has_open_position:
                # Check for trend exhaustion or target reach
                trailing_stop = max(entry_price * 0.985, current_price - (1.5 * atr))
                if current_price < trailing_stop or rsi > 82.0 or obi < -0.40:
                    return TradingSignal(
                        symbol=symbol,
                        action=SignalAction.SELL,
                        regime=regime,
                        current_price=current_price,
                        target_price=current_price,
                        stop_loss_price=trailing_stop,
                        estimated_win_probability=0.75,
                        confidence=0.80,
                        rationale="Trend exit: RSI overbought or trailing stop reached",
                        indicators=indicators_snapshot,
                        timestamp=now,
                    )
                return TradingSignal(
                    symbol=symbol,
                    action=SignalAction.HOLD,
                    regime=regime,
                    current_price=current_price,
                    target_price=entry_price + (2.5 * atr),
                    stop_loss_price=trailing_stop,
                    estimated_win_probability=0.65,
                    confidence=0.75,
                    rationale="Riding bullish trend with dynamic trailing stop",
                    indicators=indicators_snapshot,
                    timestamp=now,
                )

            # Looking for BUY entry
            ema_aligned = ema9 > ema21
            momentum_ok = 48.0 <= rsi <= 90.0
            orderbook_support = obi >= -0.10

            if ema_aligned and momentum_ok and orderbook_support:
                stop_dist = max(1.2 * atr, current_price * 0.008)
                target_dist = max(2.2 * atr, current_price * self.min_profit_hurdle_pct)
                target_p = current_price + target_dist
                stop_p = max(0.0, current_price - stop_dist)

                win_prob = 0.65
                if obi > 0.15:
                    win_prob += 0.05
                if 55.0 <= rsi <= 68.0:
                    win_prob += 0.05

                return TradingSignal(
                    symbol=symbol,
                    action=SignalAction.BUY,
                    regime=regime,
                    current_price=current_price,
                    target_price=target_p,
                    stop_loss_price=stop_p,
                    estimated_win_probability=min(0.80, win_prob),
                    confidence=0.80,
                    rationale=f"Bullish trend breakout: EMA9 ({ema9:.1f}) > EMA21 ({ema21:.1f}), RSI={rsi:.1f}, OBI={obi:.2f}",
                    indicators=indicators_snapshot,
                    timestamp=now,
                )

            return TradingSignal(
                symbol=symbol,
                action=SignalAction.HOLD,
                regime=regime,
                current_price=current_price,
                target_price=0.0,
                stop_loss_price=0.0,
                estimated_win_probability=0.0,
                confidence=0.60,
                rationale="Bullish trend setup pending: waiting for clean EMA/RSI momentum alignment",
                indicators=indicators_snapshot,
                timestamp=now,
            )

        # =====================================================================
        # Strategy 2: RANGING_SIDEWAYS (Bollinger Mean-Reversion Scalping)
        # =====================================================================
        if regime == MarketRegime.RANGING_SIDEWAYS:
            if has_open_position:
                # Target is middle band (mean) or upper band; stop is below lower band
                if current_price >= mid_bb or rsi >= 65.0:
                    return TradingSignal(
                        symbol=symbol,
                        action=SignalAction.SELL,
                        regime=regime,
                        current_price=current_price,
                        target_price=current_price,
                        stop_loss_price=current_price * 0.99,
                        estimated_win_probability=0.75,
                        confidence=0.80,
                        rationale=f"Range mean-reversion target reached: SMA20 ({mid_bb:.1f}) reached, RSI={rsi:.1f}",
                        indicators=indicators_snapshot,
                        timestamp=now,
                    )
                return TradingSignal(
                    symbol=symbol,
                    action=SignalAction.HOLD,
                    regime=regime,
                    current_price=current_price,
                    target_price=mid_bb,
                    stop_loss_price=entry_price - (0.8 * atr),
                    estimated_win_probability=0.65,
                    confidence=0.70,
                    rationale="Holding mean-reversion position towards SMA20",
                    indicators=indicators_snapshot,
                    timestamp=now,
                )

            # Looking for BUY entry at lower boundary
            is_at_lower_band = current_price <= (lower_bb * 1.004)
            rsi_oversold = rsi <= 40.0
            orderbook_bid_wall = obi >= 0.05  # Solid bid wall absorbing sell pressure

            if is_at_lower_band and rsi_oversold and orderbook_bid_wall:
                target_p = max(mid_bb, current_price * (1.0 + self.min_profit_hurdle_pct))
                stop_dist = max(0.8 * atr, current_price * 0.006)
                stop_p = max(0.0, current_price - stop_dist)

                win_prob = 0.68
                if obi > 0.20:
                    win_prob += 0.05
                if rsi < 32.0:
                    win_prob += 0.04

                return TradingSignal(
                    symbol=symbol,
                    action=SignalAction.BUY,
                    regime=regime,
                    current_price=current_price,
                    target_price=target_p,
                    stop_loss_price=stop_p,
                    estimated_win_probability=min(0.82, win_prob),
                    confidence=0.78,
                    rationale=f"Bollinger oversold bounce: Price ({current_price:.1f}) <= LowerBB ({lower_bb:.1f}), RSI={rsi:.1f}, Bid OBI={obi:.2f}",
                    indicators=indicators_snapshot,
                    timestamp=now,
                )

            return TradingSignal(
                symbol=symbol,
                action=SignalAction.HOLD,
                regime=regime,
                current_price=current_price,
                target_price=0.0,
                stop_loss_price=0.0,
                estimated_win_probability=0.0,
                confidence=0.55,
                rationale="Range consolidation: price within normal bounds, no extreme mean-reversion setup",
                indicators=indicators_snapshot,
                timestamp=now,
            )

        # Fallback default
        return TradingSignal(
            symbol=symbol,
            action=SignalAction.HOLD,
            regime=regime,
            current_price=current_price,
            target_price=0.0,
            stop_loss_price=0.0,
            estimated_win_probability=0.0,
            confidence=0.50,
            rationale="No actionable condition met",
            indicators=indicators_snapshot,
            timestamp=now,
        )
