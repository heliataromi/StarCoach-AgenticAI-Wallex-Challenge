"""Domain models and data contracts for the Macro Agentic Brain and Strategy Engine.

Adheres to Ponytail minimalism: immutable frozen dataclasses and lightweight enums.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, List, Optional


class MarketRegime(str, Enum):
    """Latent market regime states."""
    BULLISH_TREND = "BULLISH_TREND"
    RANGING_SIDEWAYS = "RANGING_SIDEWAYS"
    HIGH_VOLATILITY_PANIC = "HIGH_VOLATILITY_PANIC"


class SignalAction(str, Enum):
    """Signal execution directive."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class TradingSignal:
    """Discrete tactical trade signal emitted by multi-regime signal generators."""
    symbol: str
    action: SignalAction
    regime: MarketRegime
    current_price: float
    target_price: float
    stop_loss_price: float
    estimated_win_probability: float
    confidence: float
    rationale: str
    indicators: Dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def reward_to_risk(self) -> float:
        """Calculates expected reward to risk ratio."""
        if self.action == SignalAction.BUY:
            risk = max(1e-8, self.current_price - self.stop_loss_price)
            reward = max(0.0, self.target_price - self.current_price)
            return reward / risk
        elif self.action == SignalAction.SELL:
            risk = max(1e-8, self.stop_loss_price - self.current_price)
            reward = max(0.0, self.current_price - self.target_price)
            return reward / risk
        return 0.0


@dataclass(frozen=True)
class MacroTradingPolicy:
    """Strategic regime directive published by the Macro Agentic Brain to govern execution."""
    active_regime: MarketRegime
    allowed_symbols: List[str]
    max_allocation_per_trade_pct: float = 0.20
    target_expected_value_threshold: float = 0.0035  # +0.35% JEV hurdle
    regime_confidence: float = 1.0
    reasoning_summary: str = ""
    regime_timestamp: float = field(default_factory=time.time)
    ttl_seconds: float = 300.0

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        """Checks if the macro policy has outlived its validity TTL."""
        now = current_time if current_time is not None else time.time()
        return (now - self.regime_timestamp) > self.ttl_seconds
