"""Unit and mathematical tests for JEV Decision Evaluator,
Position Sizer, and Drawdown Sentinel.
"""
import math
import pytest
import time

from src.risk.drawdown import DrawdownSentinel
from src.risk.jev import JEVEvaluator
from src.risk.models import LiquidationPhase, PositionRiskState, RiskConfig
from src.risk.sizing import PositionSizer


# ==============================================================================
# 1. JEV Decision Evaluator Tests
# ==============================================================================

class TestJEVEvaluator:
    def test_positive_expectancy_approved(self):
        """Entry=4.2B, TP=4.3B (+2.38%), SL=4.15B (-1.19%), WinProb=0.65 -> EV strongly positive."""
        evaluator = JEVEvaluator()
        result = evaluator.evaluate(
            entry_price=4_200_000_000.0,
            take_profit_price=4_300_000_000.0,
            stop_loss_price=4_150_000_000.0,
            win_probability=0.65,
            side="BUY",
        )
        assert result.is_approved is True
        assert result.rejection_reason is None
        assert result.reward_to_risk == pytest.approx(2.0, rel=1e-2)
        assert result.expected_value > 0.0035

    def test_rejection_when_ev_below_hurdle(self):
        """WinProb=0.40 with 1:1 risk-reward -> EV is negative."""
        evaluator = JEVEvaluator()
        result = evaluator.evaluate(
            entry_price=4_200_000_000.0,
            take_profit_price=4_260_000_000.0,
            stop_loss_price=4_160_000_000.0,
            win_probability=0.40,
            side="BUY",
        )
        assert result.is_approved is False
        assert "below hurdle" in result.rejection_reason

    def test_rejection_when_reward_to_risk_insufficient(self):
        """Gain=1.0%, Loss=1.0% (RR=1.0 < 1.5 min requirement) -> rejected even if WinProb=0.70."""
        evaluator = JEVEvaluator()
        result = evaluator.evaluate(
            entry_price=100.0,
            take_profit_price=101.0,
            stop_loss_price=99.0,
            win_probability=0.70,
            side="BUY",
        )
        assert result.is_approved is False
        assert "Reward-to-risk ratio" in result.rejection_reason

    def test_fee_friction_deduction(self):
        """Verify fees reduce expected value and can push a marginal trade below hurdle."""
        evaluator = JEVEvaluator()
        # Barely positive before fees
        res_low_fee = evaluator.evaluate(
            entry_price=100.0,
            take_profit_price=103.0,  # +3%
            stop_loss_price=98.0,     # -2%
            win_probability=0.55,
            custom_fee_pct=0.001,
        )
        res_high_fee = evaluator.evaluate(
            entry_price=100.0,
            take_profit_price=103.0,
            stop_loss_price=98.0,
            win_probability=0.55,
            custom_fee_pct=0.02,     # 2% fee kills the trade
        )
        assert res_low_fee.expected_value > res_high_fee.expected_value
        assert res_high_fee.is_approved is False

    def test_geometry_validation_for_buy_and_sell(self):
        evaluator = JEVEvaluator()
        # Invalid BUY: TP < Entry
        res_inv_buy = evaluator.evaluate(100.0, 95.0, 90.0, 0.6, side="BUY")
        assert res_inv_buy.is_approved is False
        assert "TP (95.0) > Entry (100.0) > SL (90.0)" in res_inv_buy.rejection_reason

        # Valid SELL: SL > Entry > TP
        res_valid_sell = evaluator.evaluate(100.0, 95.0, 102.5, 0.65, side="SELL")
        assert res_valid_sell.is_approved is True
        assert res_valid_sell.reward_to_risk == pytest.approx(2.0, rel=1e-2)

    def test_numerical_nan_and_inf_handling(self):
        evaluator = JEVEvaluator()
        assert evaluator.evaluate(float("nan"), 105.0, 95.0, 0.6).is_approved is False
        assert evaluator.evaluate(100.0, float("inf"), 95.0, 0.6).is_approved is False
        assert evaluator.evaluate(100.0, 105.0, 95.0, -0.5).is_approved is False
        assert evaluator.evaluate(100.0, 105.0, 95.0, 1.5).is_approved is False
        assert evaluator.evaluate(0.0, 105.0, 95.0, 0.6).is_approved is False


# ==============================================================================
# 2. Position Sizer & Dynamic ATR Stop Tests
# ==============================================================================

class TestPositionSizer:
    def test_max_position_size_cap_20_percent(self):
        sizer = PositionSizer()
        # 5,000,000 equity, 5,000,000 cash -> max 20% = 1,000,000 TMN
        size_tmn, qty, reason = sizer.calculate_position_size(
            current_equity_tmn=5_000_000.0,
            available_tmn=5_000_000.0,
            active_positions_count=0,
            price=4_000_000_000.0,
        )
        assert reason is None
        assert size_tmn == pytest.approx(1_000_000.0, abs=100.0)
        assert qty == pytest.approx(1_000_000.0 / 4_000_000_000.0)

    def test_rejection_at_max_concurrent_positions(self):
        sizer = PositionSizer()
        size_tmn, qty, reason = sizer.calculate_position_size(
            current_equity_tmn=5_000_000.0,
            available_tmn=3_000_000.0,
            active_positions_count=2,  # Limit is 2
            price=4_000_000_000.0,
        )
        assert size_tmn == 0.0
        assert "Max concurrent positions limit reached" in reason

    def test_rejection_when_cash_below_minimum_order(self):
        sizer = PositionSizer()
        size_tmn, qty, reason = sizer.calculate_position_size(
            current_equity_tmn=5_000_000.0,
            available_tmn=30_000.0,    # Below 50k minimum
            active_positions_count=0,
            price=100.0,
        )
        assert size_tmn == 0.0
        assert "below exchange minimum" in reason

    def test_atr_stop_loss_calculation(self):
        sizer = PositionSizer()
        # Entry 100, ATR=1.0 -> StopDistance = 1.0 * 1.2 = 1.2 -> Stop = 98.8
        stop_price = sizer.calculate_atr_stop_loss(entry_price=100.0, atr_value=1.0, side="BUY")
        assert stop_price == pytest.approx(98.8)

        # High ATR capped at max_stop_loss_pct (2%) -> StopDistance = 2.0 -> Stop = 98.0
        stop_capped = sizer.calculate_atr_stop_loss(entry_price=100.0, atr_value=5.0, side="BUY")
        assert stop_capped == pytest.approx(98.0)

    def test_trailing_stop_ratchet_logic(self):
        sizer = PositionSizer()
        pos = PositionRiskState(
            symbol="BTCTMN",
            side="BUY",
            entry_price=100.0,
            quantity=1.0,
            cost_tmn=100.0,
            current_price=100.0,
            stop_loss_price=98.0,
            take_profit_price=105.0,
            highest_price_seen=100.0,
            opened_at=time.time(),
        )

        # Price rises to 105 (ATR=1.0 -> dist=1.2) -> New candidate stop = 105 - 1.2 = 103.8
        updated = sizer.update_trailing_stop(pos, latest_price=105.0, atr_value=1.0)
        assert updated is True
        assert pos.highest_price_seen == 105.0
        assert pos.stop_loss_price == pytest.approx(103.8)

        # Price dips to 103 -> stop loss MUST NOT move down
        updated_dip = sizer.update_trailing_stop(pos, latest_price=103.0, atr_value=1.0)
        assert updated_dip is False
        assert pos.stop_loss_price == pytest.approx(103.8)


# ==============================================================================
# 3. Drawdown Sentinel & Circuit Breaker Tests
# ==============================================================================

class TestDrawdownSentinel:
    def test_drawdown_and_high_water_mark_tracking(self):
        sentinel = DrawdownSentinel()

        # Initial state: 5M
        snap1 = sentinel.update_equity(5_000_000.0, 0.0, {}, {})
        assert snap1.high_water_mark_tmn == 5_000_000.0
        assert snap1.drawdown_pct == 0.0
        assert snap1.circuit_breaker_active is False

        # Equity grows to 6M
        snap2 = sentinel.update_equity(6_000_000.0, 0.0, {}, {})
        assert snap2.high_water_mark_tmn == 6_000_000.0
        assert snap2.drawdown_pct == 0.0

        # Equity drops to 5.7M (Drawdown = 300k / 6M = 5.0%)
        snap3 = sentinel.update_equity(5_700_000.0, 0.0, {}, {})
        assert snap3.high_water_mark_tmn == 6_000_000.0
        assert snap3.drawdown_pct == pytest.approx(0.05)
        assert snap3.circuit_breaker_active is False

    def test_circuit_breaker_triggers_at_10_percent_drawdown(self):
        sentinel = DrawdownSentinel()
        # High water mark: 5M
        sentinel.update_equity(5_000_000.0, 0.0, {}, {})

        # Equity drops to 4,450,000 (Drawdown = 550k / 5M = 11% > 10% limit)
        snap = sentinel.update_equity(4_450_000.0, 0.0, {}, {})
        assert snap.circuit_breaker_active is True
        assert sentinel.circuit_breaker_active is True

        # Trading must be blocked
        allowed, reason = sentinel.can_open_position()
        assert allowed is False
        assert "Circuit Breaker" in reason

    def test_circuit_breaker_triggers_at_equity_floor_4_5M(self):
        sentinel = DrawdownSentinel()
        snap = sentinel.update_equity(4_400_000.0, 0.0, {}, {})
        assert snap.circuit_breaker_active is True
        assert sentinel.circuit_breaker_active is True
        assert sentinel.circuit_breaker_reason is not None
        assert "drawdown" in sentinel.circuit_breaker_reason.lower() or "equity" in sentinel.circuit_breaker_reason.lower()

    def test_deadline_liquidation_phases(self):
        current_time = 100_000.0
        deadline = current_time + (5 * 3600)  # 5 hours ahead

        sentinel = DrawdownSentinel(
            deadline_timestamp=deadline,
            clock_func=lambda: current_time,
        )

        # 5h left -> NORMAL_TRADING
        assert sentinel.get_liquidation_phase() == LiquidationPhase.NORMAL_TRADING
        assert sentinel.can_open_position()[0] is True

        # Advance to 3h left -> INHIBIT_NEW_BUYS (can_open_position is False)
        current_time = deadline - (3 * 3600)
        assert sentinel.get_liquidation_phase() == LiquidationPhase.INHIBIT_NEW_BUYS
        can_open, reason = sentinel.can_open_position()
        assert can_open is False
        assert "INHIBIT_NEW_BUYS" in reason

        # Advance to 1h left -> ORDERLY_LIQUIDATION
        current_time = deadline - (1 * 3600)
        assert sentinel.get_liquidation_phase() == LiquidationPhase.ORDERLY_LIQUIDATION

        # Advance to 20m left -> SWEEP_DUST
        current_time = deadline - (20 * 60)
        assert sentinel.get_liquidation_phase() == LiquidationPhase.SWEEP_DUST

        # Advance to 10m left -> COMPLETED
        current_time = deadline - (10 * 60)
        assert sentinel.get_liquidation_phase() == LiquidationPhase.COMPLETED
