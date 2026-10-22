"""Capital Allocation, Dynamic ATR Trailing Stops, and Position Sizing Sentinel.
Enforces institutional position ceilings (max 20% equity) and trailing stop dynamics.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from src.risk.models import PositionRiskState, RiskConfig


class PositionSizer:
    """
    Computes mathematically constrained order sizes and trailing stop levels
    to prevent over-allocation and contain downside volatility.
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or RiskConfig()

    def calculate_position_size(
        self,
        current_equity_tmn: float,
        available_tmn: float,
        active_positions_count: int,
        price: float,
        risk_multiplier: float = 1.0,
    ) -> Tuple[float, float, Optional[str]]:
        """
        Determines the maximum allowed order size in Iranian Toman (TMN) and asset quantity.
        Returns: (size_tmn, quantity, rejection_reason)
        """
        if current_equity_tmn <= 0 or available_tmn <= 0 or price <= 0:
            return 0.0, 0.0, "Invalid equity, available cash, or price (must be positive)"

        # Check maximum concurrent positions limit
        if active_positions_count >= self.config.max_concurrent_positions:
            return 0.0, 0.0, f"Max concurrent positions limit reached ({active_positions_count}/{self.config.max_concurrent_positions})"

        # Compute theoretical allocation: Equity * MaxPct * RiskMultiplier
        clamped_risk = max(0.2, min(1.5, risk_multiplier))
        target_allocation_pct = self.config.max_position_size_pct * clamped_risk
        # Absolute ceiling: never more than 25% under any multiplier
        target_allocation_pct = min(0.25, target_allocation_pct)

        max_order_tmn = current_equity_tmn * target_allocation_pct

        # Factor in round-trip exchange fees: Total Cost = Size + Fee
        fee_buffer = 1.0 + (self.config.round_trip_fee_pct / 2.0)
        affordable_tmn = available_tmn / fee_buffer

        allowed_size_tmn = min(max_order_tmn, affordable_tmn)

        # Minimum viable order size (e.g. 50,000 TMN on Wallex)
        if allowed_size_tmn < 50_000.0:
            return 0.0, 0.0, f"Calculated order size {allowed_size_tmn:,.0f} TMN is below exchange minimum (50,000 TMN)"

        quantity = allowed_size_tmn / price
        return allowed_size_tmn, quantity, None

    def calculate_atr_stop_loss(
        self,
        entry_price: float,
        atr_value: float,
        side: str = "BUY",
        tighten_pct: Optional[float] = None,
    ) -> float:
        """
        Calculates dynamic Stop-Loss price using Average True Range (ATR).
        StopDistance = min(entry * max_stop_loss_pct, atr * atr_stop_multiplier).
        """
        if entry_price <= 0:
            raise ValueError(f"Entry price must be positive, got {entry_price}")

        # Compute ATR-based stop distance
        if atr_value > 0:
            distance = atr_value * self.config.atr_stop_multiplier
        else:
            distance = entry_price * self.config.max_stop_loss_pct

        # Hard ceiling on stop loss distance (max allowable risk per trade)
        max_dist = entry_price * (tighten_pct or self.config.max_stop_loss_pct)
        stop_distance = min(distance, max_dist)

        if side.upper() == "BUY":
            return max(0.0, entry_price - stop_distance)
        elif side.upper() == "SELL":
            return entry_price + stop_distance
        else:
            raise ValueError(f"Unsupported side: {side}")

    def update_trailing_stop(
        self,
        position: PositionRiskState,
        latest_price: float,
        atr_value: float,
    ) -> bool:
        """
        Updates trailing stop-loss for an active position when price moves favorably.
        Returns True if stop-loss price was tightened/adjusted upward.
        """
        if latest_price <= 0:
            return False

        position.current_price = latest_price
        updated = False

        if position.side.upper() == "BUY":
            # If new high price established, compute candidate trailing stop
            if latest_price > position.highest_price_seen:
                position.highest_price_seen = latest_price

            stop_dist = min(
                position.highest_price_seen * self.config.max_stop_loss_pct,
                atr_value * self.config.atr_stop_multiplier if atr_value > 0 else position.highest_price_seen * self.config.max_stop_loss_pct
            )
            candidate_stop = position.highest_price_seen - stop_dist

            # Trailing stop ratchet: only moves UP, never down
            if candidate_stop > position.stop_loss_price:
                position.stop_loss_price = candidate_stop
                updated = True

        return updated
