"""Macro Agentic Brain.

Autonomous reasoner executing on a macro cadence (5-15 min) or event triggers:
- Evaluates cross-market conditions across permitted Wallex pairs
- Classifies global and per-asset latent market regimes
- Generates and publishes structured MacroTradingPolicy directives
- Coordinates strategic posture for the micro execution engine
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional
from src.strategy.models import MacroTradingPolicy, MarketRegime
from src.strategy.regimes import LatentRegimeClassifier
from src.wallex.models import Candle, OrderBook


class MacroAgent:
    """Strategic Macro Brain responsible for regime governance and portfolio policy."""

    def __init__(
        self,
        allowed_symbols: Optional[List[str]] = None,
        default_policy_ttl_seconds: float = 300.0,
        classifier: Optional[LatentRegimeClassifier] = None,
        llm_reasoner: Optional[Callable[[Dict[str, Any]], str]] = None,
    ) -> None:
        self.allowed_symbols = allowed_symbols or ["BTCTMN", "ETHTMN", "USDTTMN"]
        self.default_policy_ttl_seconds = default_policy_ttl_seconds
        self.classifier = classifier or LatentRegimeClassifier()
        self.llm_reasoner = llm_reasoner
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

        # Classify each allowed symbol
        for symbol in self.allowed_symbols:
            candles = market_candles.get(symbol, [])
            ob = orderbooks.get(symbol)
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
