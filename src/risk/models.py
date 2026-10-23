"""Data models, enums, and dataclasses for the Risk & Capital Sentinel Subsystem.
Strictly adheres to Python 3.12 dataclasses and pure math primitives.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple


class LiquidationPhase(str, Enum):
    """Lifecycle phase for mandatory Iranian Toman deadline liquidation."""
    NORMAL_TRADING = "NORMAL_TRADING"
    INHIBIT_NEW_BUYS = "INHIBIT_NEW_BUYS"         # T - 4h: No new buy orders
    ORDERLY_LIQUIDATION = "ORDERLY_LIQUIDATION"   # T - 2h: Limit-sell all open positions
    SWEEP_DUST = "SWEEP_DUST"                     # T - 30m: Market-sell any fractional coin dust
    COMPLETED = "COMPLETED"                       # T - 15m: 100% Toman, 0% crypto asserted


@dataclass(frozen=True)
class RiskConfig:
    """Institutional risk guardrail configuration."""
    initial_capital_tmn: float = 5_000_000.0     # Wallex dedicated hackathon credit
    max_position_size_pct: float = 0.20          # Max 20% of equity per trade (max 1M TMN)
    max_concurrent_positions: int = 2            # Max 2 concurrent open positions
    min_reward_to_risk: float = 1.5              # Minimum acceptable reward-to-risk ratio
    jev_hurdle_rate_pct: float = 0.0035          # Minimum positive edge (+0.35% net of fees)
    round_trip_fee_pct: float = 0.0055           # Wallex credit Toman fees (0.25% maker + 0.30% taker = 0.55%)
    toman_maker_fee_pct: float = 0.0025          # 0.25% Wallex maker fee in TMN markets
    toman_taker_fee_pct: float = 0.0030          # 0.30% Wallex taker fee in TMN markets
    usdt_maker_fee_pct: float = 0.0010           # 0.10% Wallex maker fee in USDT markets
    usdt_taker_fee_pct: float = 0.0013           # 0.13% Wallex taker fee in USDT markets
    max_drawdown_limit_pct: float = 0.10         # 10% max allowable peak-to-trough drawdown
    circuit_breaker_equity_tmn: float = 4_500_000.0 # Circuit breaker halts trading if equity <= 4.5M
    atr_stop_multiplier: float = 1.2             # 1.2 x ATR for dynamic stop-loss
    max_stop_loss_pct: float = 0.02              # Hard cap stop-loss at max 2.0% distance

    def get_fee_rates(self, symbol: Optional[str] = None) -> Tuple[float, float]:
        """Returns (maker_fee, taker_fee) based on market quote currency."""
        if symbol and symbol.upper().replace("/", "").replace("-", "").replace("_", "").endswith("USDT"):
            return (self.usdt_maker_fee_pct, self.usdt_taker_fee_pct)
        return (self.toman_maker_fee_pct, self.toman_taker_fee_pct)

    def calculate_round_trip_fee(
        self,
        symbol: Optional[str] = None,
        is_maker_entry: bool = True,
        is_maker_exit: bool = False,
    ) -> float:
        """Calculates exact realistic round-trip fee friction for a market pair."""
        maker_fee, taker_fee = self.get_fee_rates(symbol)
        entry_fee = maker_fee if is_maker_entry else taker_fee
        exit_fee = maker_fee if is_maker_exit else taker_fee
        return entry_fee + exit_fee


@dataclass(frozen=True)
class JEVEvaluationResult:
    """Statistical outcome of a Joint Expected Value assessment."""
    expected_value: float                        # Net expected return fraction (e.g. +0.0084 = +0.84%)
    is_approved: bool                            # True if EV >= hurdle and Reward:Risk >= min_ratio
    win_probability: float                       # P(Win)
    reward_to_risk: float                        # Gain / Loss ratio
    expected_gain_pct: float                     # Upside target distance fraction
    expected_loss_pct: float                     # Downside stop distance fraction
    round_trip_fee_pct: float                    # Fee friction deducted
    hurdle_rate_pct: float                       # Benchmark hurdle rate
    rejection_reason: Optional[str] = None       # Explanation if rejected


@dataclass
class PositionRiskState:
    """Active open position risk monitoring record."""
    symbol: str
    side: str                                    # "BUY"
    entry_price: float
    quantity: float
    cost_tmn: float
    current_price: float
    stop_loss_price: float
    take_profit_price: float
    highest_price_seen: float
    opened_at: float                             # Unix timestamp
    is_trailing: bool = True

    @property
    def unrealized_pnl_tmn(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price


@dataclass
class AccountSnapshot:
    """Point-in-time portfolio capitalization and drawdown metrics."""
    timestamp: float
    tmn_available: float
    tmn_locked: float
    crypto_equity_tmn: float
    total_equity_tmn: float
    high_water_mark_tmn: float
    drawdown_tmn: float
    drawdown_pct: float
    circuit_breaker_active: bool
