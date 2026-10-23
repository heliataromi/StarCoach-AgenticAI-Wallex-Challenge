"""Macro Agentic Brain.

Autonomous reasoner executing on a macro cadence (5-15 min) or event triggers:
- Evaluates cross-market conditions across permitted Wallex pairs
- Classifies global and per-asset latent market regimes
- Generates and publishes structured MacroTradingPolicy directives
- Coordinates strategic posture for the micro execution engine
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional
from src.strategy.debate import BearResearcher, BullResearcher, DebateTranscript, RiskArbiter
from src.strategy.llm_gateway import BoxAPIGateway
from src.strategy.models import MacroTradingPolicy, MarketRegime
from src.strategy.reflection import EpisodicMemoryBuffer
from src.strategy.regimes import LatentRegimeClassifier
from src.wallex.models import Candle, OrderBook, is_credit_market_supported


class MacroAgent:
    """Strategic Macro Brain responsible for regime governance and portfolio policy."""

    def __init__(
        self,
        allowed_symbols: Optional[List[str]] = None,
        default_policy_ttl_seconds: float = 300.0,
        classifier: Optional[LatentRegimeClassifier] = None,
        llm_reasoner: Optional[Callable[[Dict[str, Any]], str]] = None,
        memory: Optional[EpisodicMemoryBuffer] = None,
        enable_debate: bool = True,
        llm_gateway: Optional[BoxAPIGateway] = None,
    ) -> None:
        raw_symbols = allowed_symbols or ["BTCTMN", "ETHTMN", "USDTTMN"]
        self.allowed_symbols = [s for s in raw_symbols if is_credit_market_supported(s)]
        self.default_policy_ttl_seconds = default_policy_ttl_seconds
        self.classifier = classifier or LatentRegimeClassifier()
        self.llm_reasoner = llm_reasoner
        self.memory = memory or EpisodicMemoryBuffer()
        self.enable_debate = enable_debate
        self.llm_gateway = llm_gateway

        # Dialectical debate components
        self.bull_researcher = BullResearcher()
        self.bear_researcher = BearResearcher()

        # Wire LLM Arbiter hook if gateway is configured
        arbiter_hook = None
        if self.llm_gateway:
            def _arbiter_hook(ctx: Dict[str, Any]) -> Dict[str, Any]:
                if not self.llm_gateway or not self.llm_gateway.is_configured:
                    return {}
                bull_score = ctx.get("bull_score", 0.0)
                bear_score = ctx.get("bear_score", 0.0)
                if max(bull_score, bear_score) < 0.30:
                    return {}  # Choppy noise, conserve budget
                lessons = self.memory.get_recent_lessons(ctx.get("symbol"))
                res = self.llm_gateway.adjudicate_debate(
                    symbol=ctx.get("symbol", ""),
                    bull_thesis={"bull_score": bull_score, "rationale": ctx.get("bull_rationale", "")},
                    bear_thesis={"bear_score": bear_score, "rationale": ctx.get("bear_rationale", "")},
                    recent_lessons=lessons,
                )
                return res or {}
            arbiter_hook = _arbiter_hook

        self.risk_arbiter = RiskArbiter(llm_arbiter_hook=arbiter_hook)
        self.latest_debates: Dict[str, DebateTranscript] = {}

        self.current_policy: Optional[MacroTradingPolicy] = None

    def evaluate_policy(
        self,
        market_candles: Dict[str, List[Candle]],
        market_orderbooks: Optional[Dict[str, OrderBook]] = None,
        current_time: Optional[float] = None,
    ) -> MacroTradingPolicy:
        """Evaluates all candidate markets and constructs the active MacroTradingPolicy."""
        now = current_time if current_time is not None else time.time()
        orderbooks = market_orderbooks or {}

        symbol_regimes: Dict[str, MarketRegime] = {}
        symbol_confidences: Dict[str, float] = {}
        symbol_rationales: Dict[str, str] = {}

        # Classify each allowed symbol via Dialectical Debate or Classifier
        for symbol in self.allowed_symbols:
            candles = market_candles.get(symbol, [])
            ob = orderbooks.get(symbol)
            if self.enable_debate and len(candles) >= 5:
                lessons = self.memory.get_recent_lessons(symbol, limit=2)
                bull_thesis = self.bull_researcher.evaluate(symbol, candles, ob, memory_insights=lessons)
                bear_thesis = self.bear_researcher.evaluate(symbol, candles, ob, memory_insights=lessons)
                transcript = self.risk_arbiter.adjudicate(symbol, bull_thesis, bear_thesis, current_time=now)
                self.latest_debates[symbol] = transcript

                symbol_regimes[symbol] = transcript.recommended_regime
                symbol_confidences[symbol] = transcript.confidence
                symbol_rationales[symbol] = transcript.summary_reasoning
            else:
                regime, conf, rationale = self.classifier.classify(candles, ob)
                symbol_regimes[symbol] = regime
                symbol_confidences[symbol] = conf
                symbol_rationales[symbol] = rationale

        # Check for Panic contagion
        panic_symbols = [s for s, r in symbol_regimes.items() if r == MarketRegime.HIGH_VOLATILITY_PANIC]
        bullish_symbols = [s for s, r in symbol_regimes.items() if r == MarketRegime.BULLISH_TREND]

        if panic_symbols:
            # If any primary asset is in panic, shift to defensive Cash Guard
            active_regime = MarketRegime.HIGH_VOLATILITY_PANIC
            regime_confidence = max(symbol_confidences[s] for s in panic_symbols)
            max_alloc = 0.0  # Zero allocation allowed
            allowed = []  # No trading allowed
            summary = (
                f"Global defensive cash preservation: Panic detected on {', '.join(panic_symbols)}. "
                f"Halting all buy allocations to protect dedicated 5M TMN capital."
            )
        elif bullish_symbols:
            # Bullish momentum on one or more assets
            active_regime = MarketRegime.BULLISH_TREND
            regime_confidence = sum(symbol_confidences[s] for s in bullish_symbols) / len(bullish_symbols)
            max_alloc = 0.20  # Max 20% equity cap per challenge rules
            allowed = bullish_symbols
            summary = (
                f"Bullish trend regime: Upward momentum active on {', '.join(bullish_symbols)}. "
                f"Authorizing trend continuation and breakout trades."
            )
        else:
            # Range-bound mean-reversion
            active_regime = MarketRegime.RANGING_SIDEWAYS
            regime_confidence = sum(symbol_confidences.values()) / max(1, len(symbol_confidences))
            max_alloc = 0.15  # Conservative 15% for range scalping
            allowed = self.allowed_symbols
            summary = (
                f"Ranging market regime: Volatility is contained. "
                f"Authorizing Bollinger Band mean-reversion scalping with order book bid support."
            )

        # Optional LLM strategic enhancement hook
        if self.llm_reasoner:
            try:
                context = {
                    "active_regime": active_regime.value,
                    "symbol_regimes": {s: r.value for s, r in symbol_regimes.items()},
                    "confidences": symbol_confidences,
                    "rationales": symbol_rationales,
                }
                enhanced_summary = self.llm_reasoner(context)
                if enhanced_summary:
                    summary = enhanced_summary
            except Exception:
                pass  # Graceful fallback to deterministic synthesis

        policy = MacroTradingPolicy(
            active_regime=active_regime,
            allowed_symbols=allowed,
            max_allocation_per_trade_pct=max_alloc,
            target_expected_value_threshold=0.0035,  # +0.35% JEV hurdle
            regime_confidence=regime_confidence,
            reasoning_summary=summary,
            regime_timestamp=now,
            ttl_seconds=self.default_policy_ttl_seconds,
        )

        self.current_policy = policy
        return policy

    def get_or_refresh_policy(
        self,
        market_candles: Dict[str, List[Candle]],
        market_orderbooks: Optional[Dict[str, OrderBook]] = None,
        current_time: Optional[float] = None,
    ) -> MacroTradingPolicy:
        """Returns existing policy if still valid, or recalculates if expired."""
        now = current_time if current_time is not None else time.time()
        if self.current_policy is not None and not self.current_policy.is_expired(now):
            return self.current_policy
        return self.evaluate_policy(market_candles, market_orderbooks, current_time=now)

    def record_trade_outcome(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        pnl_tmn: float,
        fee_tmn: float,
        regime: MarketRegime = MarketRegime.BULLISH_TREND,
        exit_reason: str = "Target or stop executed",
        timestamp: Optional[float] = None,
    ) -> None:
        """Records completed trade outcome into episodic reflection memory."""
        final_reason = exit_reason
        if self.llm_gateway and self.llm_gateway.is_configured:
            try:
                llm_lesson = self.llm_gateway.reflect_on_trade({
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "quantity": quantity,
                    "pnl_tmn": pnl_tmn,
                    "fee_tmn": fee_tmn,
                    "exit_reason": exit_reason,
                })
                if llm_lesson:
                    final_reason = f"{exit_reason} (LLM: {llm_lesson})"
            except Exception:
                pass

        self.memory.record_completed_trade(
            symbol=symbol,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            pnl_tmn=pnl_tmn,
            fee_tmn=fee_tmn,
            regime=regime,
            exit_reason=final_reason,
            timestamp=timestamp,
        )

    def get_debate_transcript(self, symbol: str) -> Optional[DebateTranscript]:
        """Retrieves the latest Bull vs. Bear debate transcript for a symbol."""
        return self.latest_debates.get(symbol)
