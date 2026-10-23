"""Joint Expected Value (JEV) Mathematical Decision Evaluator.
Implements formal decision-theoretic expected value gating for all trade proposals.
"""
from __future__ import annotations

import math
from typing import Optional

from src.risk.models import JEVEvaluationResult, RiskConfig


class JEVEvaluator:
    """
    Evaluates probabilistic expectancy and risk-to-reward ratios
    for quantitative trade proposals before capital allocation.
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config or RiskConfig()

    def evaluate(
        self,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        win_probability: float,
        side: str = "BUY",
        custom_fee_pct: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> JEVEvaluationResult:
        """
        Calculates E[V] = P(Win) * GainPct - (1 - P(Win)) * LossPct - RoundTripFees.
        Returns JEVEvaluationResult with verdict and detailed metrics.
        """
        if custom_fee_pct is not None:
            fee_pct = custom_fee_pct
        elif symbol is not None:
            fee_pct = self.config.calculate_round_trip_fee(symbol)
        else:
            fee_pct = self.config.round_trip_fee_pct
        hurdle = self.config.jev_hurdle_rate_pct
        min_rr = self.config.min_reward_to_risk

        # 1. Structural Price and Probability Validations
        if any(math.isnan(x) or math.isinf(x) for x in (entry_price, take_profit_price, stop_loss_price, win_probability)):
            return JEVEvaluationResult(
                expected_value=-1.0,
                is_approved=False,
                win_probability=win_probability,
                reward_to_risk=0.0,
                expected_gain_pct=0.0,
                expected_loss_pct=0.0,
                round_trip_fee_pct=fee_pct,
                hurdle_rate_pct=hurdle,
                rejection_reason="Invalid numeric input (NaN or Inf detected)",
            )

        if entry_price <= 0 or take_profit_price <= 0 or stop_loss_price <= 0:
            return JEVEvaluationResult(
                expected_value=-1.0,
                is_approved=False,
                win_probability=win_probability,
                reward_to_risk=0.0,
                expected_gain_pct=0.0,
                expected_loss_pct=0.0,
                round_trip_fee_pct=fee_pct,
                hurdle_rate_pct=hurdle,
                rejection_reason="Prices must be strictly positive",
            )

        if not (0.0 < win_probability < 1.0):
            return JEVEvaluationResult(
                expected_value=-1.0,
                is_approved=False,
                win_probability=win_probability,
                reward_to_risk=0.0,
                expected_gain_pct=0.0,
                expected_loss_pct=0.0,
                round_trip_fee_pct=fee_pct,
                hurdle_rate_pct=hurdle,
                rejection_reason=f"Win probability must be in (0.0, 1.0), got {win_probability}",
            )

        # 2. Geometric Direction Validations
        side_upper = side.upper()
        if side_upper == "BUY":
            if not (take_profit_price > entry_price > stop_loss_price):
                return JEVEvaluationResult(
                    expected_value=-1.0,
                    is_approved=False,
                    win_probability=win_probability,
                    reward_to_risk=0.0,
                    expected_gain_pct=0.0,
                    expected_loss_pct=0.0,
                    round_trip_fee_pct=fee_pct,
                    hurdle_rate_pct=hurdle,
                    rejection_reason=f"For BUY, TP ({take_profit_price}) > Entry ({entry_price}) > SL ({stop_loss_price}) required",
                )
            gain_pct = (take_profit_price - entry_price) / entry_price
            loss_pct = (entry_price - stop_loss_price) / entry_price
        elif side_upper == "SELL":
            if not (stop_loss_price > entry_price > take_profit_price):
                return JEVEvaluationResult(
                    expected_value=-1.0,
                    is_approved=False,
                    win_probability=win_probability,
                    reward_to_risk=0.0,
                    expected_gain_pct=0.0,
                    expected_loss_pct=0.0,
                    round_trip_fee_pct=fee_pct,
                    hurdle_rate_pct=hurdle,
                    rejection_reason=f"For SELL, SL ({stop_loss_price}) > Entry ({entry_price}) > TP ({take_profit_price}) required",
                )
            gain_pct = (entry_price - take_profit_price) / entry_price
            loss_pct = (stop_loss_price - entry_price) / entry_price
        else:
            return JEVEvaluationResult(
                expected_value=-1.0,
                is_approved=False,
                win_probability=win_probability,
                reward_to_risk=0.0,
                expected_gain_pct=0.0,
                expected_loss_pct=0.0,
                round_trip_fee_pct=fee_pct,
                hurdle_rate_pct=hurdle,
                rejection_reason=f"Unsupported order side: {side}",
            )

        # 3. Reward-to-Risk and Expected Value Calculation
        rr_ratio = gain_pct / loss_pct if loss_pct > 0 else 0.0
        p_win = win_probability
        p_loss = 1.0 - p_win

        expected_value = (p_win * gain_pct) - (p_loss * loss_pct) - fee_pct

        # 4. Gating Threshold Checks
        rejections = []
        if rr_ratio < min_rr:
            rejections.append(f"Reward-to-risk ratio {rr_ratio:.2f} is below minimum requirement {min_rr:.2f}")

        if expected_value < hurdle:
            rejections.append(f"Expected value {expected_value * 100:.3f}% is below hurdle {hurdle * 100:.3f}%")

        is_approved = (len(rejections) == 0)
        rejection_reason = "; ".join(rejections) if rejections else None

        return JEVEvaluationResult(
            expected_value=expected_value,
            is_approved=is_approved,
            win_probability=win_probability,
            reward_to_risk=rr_ratio,
            expected_gain_pct=gain_pct,
            expected_loss_pct=loss_pct,
            round_trip_fee_pct=fee_pct,
            hurdle_rate_pct=hurdle,
            rejection_reason=rejection_reason,
        )
