"""Multi-Agent Dialectical Debate Subsystem.

Implements an adversarial Bull vs. Bear debate architecture (MIT/UCLA TradingAgents & Orallexa pattern)
governed by an impartial Risk Arbiter to synthesize balanced market regime policies
without confirmation bias.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Dict, List, Optional

from src.strategy.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_order_book_imbalance,
    calculate_rsi,
)
from src.strategy.models import MarketRegime
from src.wallex.models import Candle, OrderBook


@dataclass(frozen=True)
class BullThesis:
    """Quantitative thesis formulated by the Bull Researcher Agent."""
    symbol: str
    bull_score: float              # 0.0 to 1.0 (conviction strength)
    momentum_catalyst: str        # e.g., "EMA9 > EMA21 breakout with strong bid wall"
    support_level: float          # Invalidation / entry support floor
    target_price: float           # Projected upside expansion target
    key_factors: List[str]        # Contributing bullish indicators
    rationale: str                # Natural-language argument


@dataclass(frozen=True)
class BearThesis:
    """Quantitative thesis formulated by the Bear Researcher Agent."""
    symbol: str
    bear_score: float              # 0.0 to 1.0 (downside risk conviction)
    risk_factor: str              # e.g., "Overhead resistance at Upper BB, ATR spike"
    resistance_level: float       # Key overhead rejection level
    invalidation_price: float     # Stop-loss floor if shorted or invalidation point
    key_risks: List[str]          # Contributing bearish warning signs
    rationale: str                # Natural-language risk argument


@dataclass(frozen=True)
class DebateTranscript:
    """Full deliberation record reconciling Bull and Bear perspectives."""
    symbol: str
    timestamp: float
    bull_thesis: BullThesis
    bear_thesis: BearThesis
    arbiter_verdict: str
    recommended_regime: MarketRegime
    confidence: float             # 0.0 to 1.0
    max_allocation_pct: float     # 0.0 to 0.20
    summary_reasoning: str

    def to_dict(self) -> Dict[str, Any]:
        """Converts transcript into a JSON-serializable dictionary for audit logging."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "bull_thesis": {
                "bull_score": round(self.bull_thesis.bull_score, 4),
                "momentum_catalyst": self.bull_thesis.momentum_catalyst,
                "support_level": round(self.bull_thesis.support_level, 2),
                "target_price": round(self.bull_thesis.target_price, 2),
                "key_factors": list(self.bull_thesis.key_factors),
                "rationale": self.bull_thesis.rationale,
            },
            "bear_thesis": {
                "bear_score": round(self.bear_thesis.bear_score, 4),
                "risk_factor": self.bear_thesis.risk_factor,
                "resistance_level": round(self.bear_thesis.resistance_level, 2),
                "invalidation_price": round(self.bear_thesis.invalidation_price, 2),
                "key_risks": list(self.bear_thesis.key_risks),
                "rationale": self.bear_thesis.rationale,
            },
            "arbiter_verdict": self.arbiter_verdict,
            "recommended_regime": self.recommended_regime.value,
            "confidence": round(self.confidence, 4),
            "max_allocation_pct": round(self.max_allocation_pct, 4),
            "summary_reasoning": self.summary_reasoning,
        }


class BullResearcher:
    """Specialized agent advocating for upside opportunities and trend continuation."""

    def evaluate(
        self,
        symbol: str,
        candles: List[Candle],
        orderbook: Optional[OrderBook] = None,
        memory_insights: Optional[List[str]] = None,
    ) -> BullThesis:
        """Constructs a quantitative Bull Thesis from price action and orderbook depth."""
        if not candles or len(candles) < 5:
            return BullThesis(
                symbol=symbol,
                bull_score=0.10,
                momentum_catalyst="Insufficient data",
                support_level=0.0,
                target_price=0.0,
                key_factors=["insufficient_history"],
                rationale="Insufficient historical candles to construct a reliable bullish thesis.",
            )

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        curr_price = closes[-1]

        # Indicator extraction
        ema9 = calculate_ema(closes, 9)[-1] if len(closes) >= 9 else curr_price
        ema21 = calculate_ema(closes, 21)[-1] if len(closes) >= 21 else curr_price
        rsi = calculate_rsi(closes, 14)
        atr = calculate_atr(highs, lows, closes, 14)
        atr = max(atr, curr_price * 0.005)
        lower_bb, mid_bb, upper_bb = calculate_bollinger_bands(closes, 20)

        obi = 0.0
        if orderbook and (orderbook.bids or orderbook.asks):
            obi = calculate_order_book_imbalance(orderbook.bids, orderbook.asks, depth=5)

        bull_score = 0.0
        factors = []

        # 1. Trend alignment
        if ema9 > ema21:
            bull_score += 0.40
            factors.append(f"EMA9 ({ema9:.1f}) > EMA21 ({ema21:.1f}) golden alignment")

        # 2. RSI health (not overbought, but has momentum)
        if 50.0 <= rsi <= 78.0:
            bull_score += 0.25
            factors.append(f"RSI in bullish sweet-spot ({rsi:.1f})")
        elif rsi > 78.0:
            bull_score += 0.15
            factors.append(f"Strong upward momentum ({rsi:.1f})")
        elif rsi < 35.0:
            bull_score += 0.20
            factors.append(f"RSI deeply oversold bounce potential ({rsi:.1f})")

        # 3. Orderbook support (buyers absorbing sells)
        if obi > 0.10:
            bull_score += 0.25
            factors.append(f"Bid book imbalance positive ({obi:+.2f})")
        elif obi >= 0.0:
            bull_score += 0.10

        # 4. Volatility expansion support
        if curr_price >= mid_bb:
            bull_score += 0.15
            factors.append(f"Price ({curr_price:.1f}) trading above 20-SMA baseline ({mid_bb:.1f})")

        bull_score = min(1.0, max(0.05, bull_score))

        # Memory contextual boost
        if memory_insights:
            for insight in memory_insights:
                if any(w in insight.lower() for w in ["breakout", "profitable", "validated", "gain"]):
                    bull_score = min(1.0, bull_score + 0.05)
                    factors.append(f"Historical memory reinforced: {insight[:40]}...")

        support = max(curr_price - (1.5 * atr), lower_bb if lower_bb > 0 else curr_price * 0.98)
        target = curr_price + max(2.5 * atr, curr_price * 0.025)

        rationale = (
            f"Bull thesis for {symbol}: Conviction={bull_score:.2f}. "
            f"Key catalysts: {'; '.join(factors) if factors else 'None'}. "
            f"Projected target: {target:,.1f} TMN with dynamic support at {support:,.1f} TMN."
        )

        return BullThesis(
            symbol=symbol,
            bull_score=bull_score,
            momentum_catalyst=factors[0] if factors else "Neutral price action",
            support_level=support,
            target_price=target,
            key_factors=factors,
            rationale=rationale,
        )


class BearResearcher:
    """Specialized agent stress-testing downside hazards, resistance, and fee friction."""

    def evaluate(
        self,
        symbol: str,
        candles: List[Candle],
        orderbook: Optional[OrderBook] = None,
        memory_insights: Optional[List[str]] = None,
    ) -> BearThesis:
        """Constructs a quantitative Bear Thesis scrutinizing downside vulnerabilities."""
        if not candles or len(candles) < 5:
            return BearThesis(
                symbol=symbol,
                bear_score=0.10,
                risk_factor="Insufficient data",
                resistance_level=0.0,
                invalidation_price=0.0,
                key_risks=["insufficient_history"],
                rationale="Insufficient history to stress-test downside risk.",
            )

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        curr_price = closes[-1]

        # Indicators
        ema9 = calculate_ema(closes, 9)[-1] if len(closes) >= 9 else curr_price
        ema21 = calculate_ema(closes, 21)[-1] if len(closes) >= 21 else curr_price
        rsi = calculate_rsi(closes, 14)
        atr = calculate_atr(highs, lows, closes, 14)
        atr = max(atr, curr_price * 0.005)
        lower_bb, mid_bb, upper_bb = calculate_bollinger_bands(closes, 20)

        obi = 0.0
        if orderbook and (orderbook.bids or orderbook.asks):
            obi = calculate_order_book_imbalance(orderbook.bids, orderbook.asks, depth=5)

        bear_score = 0.0
        risks = []

        # 1. Bearish trend or EMA death cross
        if ema9 < ema21:
            bear_score += 0.35
            risks.append(f"EMA9 ({ema9:.1f}) below EMA21 ({ema21:.1f}) downward pressure")

        # 2. RSI overbought exhaustion or momentum collapse
        if rsi > 80.0:
            bear_score += 0.25
            risks.append(f"RSI extreme overbought ({rsi:.1f}) vulnerable to sharp reversal")
        elif rsi < 42.0:
            bear_score += 0.20
            risks.append(f"RSI weak momentum ({rsi:.1f}) failing to sustain rallies")

        # 3. Orderbook sell wall / distribution
        if obi < -0.15:
            bear_score += 0.25
            risks.append(f"Ask book depth dominance (OBI: {obi:+.2f}) indicating institutional selling")

        # 4. Volatility expansion / Panic warning (Sharp downward moves or extreme single-bar dump)
        recent_drop = (max(highs[-5:]) - closes[-1]) / max(1.0, max(highs[-5:]))
        single_bar_dump = (highs[-1] - lows[-1]) > (2.5 * atr) and closes[-1] < ema9
        if recent_drop > 0.05 or single_bar_dump:
            bear_score += 0.35
            risks.append(f"Abnormal downside volatility expansion: drop={recent_drop * 100:.1f}%")

        # 5. Overhead resistance proximity
        if upper_bb > 0 and curr_price >= (upper_bb * 0.998):
            bear_score += 0.15
            risks.append(f"Price ({curr_price:.1f}) pressing against upper Bollinger Band ({upper_bb:.1f})")

        bear_score = min(1.0, max(0.05, bear_score))

        # Memory contextual risk amplification
        if memory_insights:
            for insight in memory_insights:
                if any(w in insight.lower() for w in ["whipsaw", "false breakout", "stopped out", "adverse move", "loss"]):
                    bear_score = min(1.0, bear_score + 0.08)
                    risks.append(f"Memory risk alert: {insight[:40]}...")

        resistance = max(upper_bb if upper_bb > 0 else curr_price * 1.02, curr_price + (1.2 * atr))
        invalidation = max(0.0, curr_price - (1.5 * atr))

        rationale = (
            f"Bear thesis for {symbol}: Conviction={bear_score:.2f}. "
            f"Key risk factors: {'; '.join(risks) if risks else 'None'}. "
            f"Overhead resistance at {resistance:,.1f} TMN; downside invalidation at {invalidation:,.1f} TMN."
        )

        return BearThesis(
            symbol=symbol,
            bear_score=bear_score,
            risk_factor=risks[0] if risks else "Benign market conditions",
            resistance_level=resistance,
            invalidation_price=invalidation,
            key_risks=risks,
            rationale=rationale,
        )


class RiskArbiter:
    """Impartial judge reconciling Bull and Bear perspectives into an actionable policy."""

    def __init__(self, llm_arbiter_hook: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None) -> None:
        self.llm_arbiter_hook = llm_arbiter_hook

    def adjudicate(
        self,
        symbol: str,
        bull: BullThesis,
        bear: BearThesis,
        current_time: Optional[float] = None,
    ) -> DebateTranscript:
        """Evaluates dialectical tension between Bull and Bear to decide market regime and allocation."""
        now = current_time if current_time is not None else time.time()

        # Optional LLM strategic hook (if provided)
        if self.llm_arbiter_hook:
            try:
                hook_res = self.llm_arbiter_hook({
                    "symbol": symbol,
                    "bull_score": bull.bull_score,
                    "bull_rationale": bull.rationale,
                    "bear_score": bear.bear_score,
                    "bear_rationale": bear.rationale,
                })
                if isinstance(hook_res, dict) and "recommended_regime" in hook_res:
                    return DebateTranscript(
                        symbol=symbol,
                        timestamp=now,
                        bull_thesis=bull,
                        bear_thesis=bear,
                        arbiter_verdict=str(hook_res.get("verdict", "LLM Adjudication")),
                        recommended_regime=MarketRegime(hook_res["recommended_regime"]),
                        confidence=float(hook_res.get("confidence", 0.75)),
                        max_allocation_pct=float(hook_res.get("max_allocation_pct", 0.20)),
                        summary_reasoning=str(hook_res.get("summary_reasoning", "Adjudicated by LLM Arbiter")),
                    )
            except Exception:
                pass  # Fall back to quantitative adjudication

        # Quantitative Adjudication Logic
        conviction_delta = bull.bull_score - bear.bear_score

        # Case 1: Extreme risk or Panic override
        if bear.bear_score >= 0.70 or "volatility expansion" in bear.risk_factor.lower():
            regime = MarketRegime.HIGH_VOLATILITY_PANIC
            confidence = bear.bear_score
            max_alloc = 0.0
            verdict = "DEFENSIVE_HALT"
            summary = (
                f"Bear thesis dominates with {bear.bear_score:.2f} conviction. "
                f"Elevated tail risk ({bear.risk_factor}) warrants defensive capital preservation."
            )

        # Case 2: Clear Bullish Superiority
        elif conviction_delta >= 0.15 and bull.bull_score >= 0.50:
            regime = MarketRegime.BULLISH_TREND
            confidence = min(0.95, bull.bull_score)
            max_alloc = 0.20  # Max 20% limit per competition rule
            verdict = "APPROVE_BULLISH_TREND"
            summary = (
                f"Bull thesis prevails by delta {conviction_delta:+.2f} (Bull: {bull.bull_score:.2f} vs Bear: {bear.bear_score:.2f}). "
                f"Authorizing trend continuation up to 20% equity cap."
            )

        # Case 3: Balanced or Low Conviction -> Range-Bound Mean-Reversion
        else:
            regime = MarketRegime.RANGING_SIDEWAYS
            confidence = max(0.50, 1.0 - abs(conviction_delta))
            max_alloc = 0.15  # Conservative 15% for range scalping
            verdict = "APPROVE_RANGE_SCALPING"
            summary = (
                f"Dialectical equilibrium reached (Bull: {bull.bull_score:.2f}, Bear: {bear.bear_score:.2f}). "
                f"Market exhibits balanced oscillations. Authorizing mean-reversion scalping with orderbook support."
            )

        return DebateTranscript(
            symbol=symbol,
            timestamp=now,
            bull_thesis=bull,
            bear_thesis=bear,
            arbiter_verdict=verdict,
            recommended_regime=regime,
            confidence=confidence,
            max_allocation_pct=max_alloc,
            summary_reasoning=summary,
        )
