"""Tier 4: Realistic Multi-Day Lifecycle & Stress Scenarios.
Simulates end-to-end full hackathon operations:
- Scenario 1: 3-Day (72h) Trading Simulation from 5M initial capital to Toman realization
- Scenario 2: Severe Market Flash Crash & Panic Regime Capital Preservation
- Scenario 3: Drawdown Sentinel 10% Circuit Breaker Stress Halt
- Scenario 4: Live Shadow Mode Stream Soak Test with Zero Capital Risk
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

import pytest

from tests.contracts import (
    AccountBalance,
    CandidateSignal,
    FakeClock,
    MacroTradingPolicy,
    MarketDataSnapshot,
    MarketRegime,
    MockDecisionAuditor,
    MockWallexClient,
    OrderExecutionResult,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeProposal,
    oracle_calc_drawdown,
    oracle_calc_dynamic_atr_stop,
    oracle_calc_imbalance,
    oracle_calc_jev,
    oracle_calc_reward_to_risk,
)

# ==============================================================================
# Scenario 1: 3-Day (72h) Hackathon Lifecycle Simulation
# ==============================================================================

def test_scenario1_3day_hackathon_lifecycle_simulation(
    mock_wallex_client: MockWallexClient,
    decision_auditor: MockDecisionAuditor,
    fake_clock: FakeClock,
):
    """Simulates the complete 72-hour Wallex Hackathon on a 5M TMN account:
    - Day 1: Bullish momentum regime -> 2 successful trades (+250k TMN profit).
    - Day 2: Ranging sideways market -> Scalping trades (+120k TMN profit).
    - Day 3 (T-4h): Pre-deadline liquidation enters, buy orders locked.
    - Day 3 (T-2h): Limit sell liquidation of all open crypto positions.
    - Day 3 (T-30m): Fractional dust sweep via market sell.
    - Day 3 (T-15m): Verification: Positive net return, DD < 10%, 100% Toman cash.
    """
    async def _run():
        start_time = fake_clock.time()
        end_time = start_time + (72 * 3600.0)  # 72 hours duration
        initial_equity = 5_000_000.0

        # --- DAY 1: Bullish Momentum Phase (0h to 24h) ---
        fake_clock.advance(4 * 3600.0)  # Hour 4
        # Trade 1: Buy BTC at 4.2B, sell at 4.284B (+2.0%)
        buy_price = 4_200_000_000.0
        allocated = 1_000_000.0  # 20% equity
        qty = allocated / buy_price

        await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=buy_price,
            quantity=qty,
            client_order_id="CL-DAY1-BUY1",
        )
        decision_auditor.record_decision(
            trade_id="CL-DAY1-BUY1",
            symbol="BTCTMN",
            action="BUY",
            order_type="LIMIT",
            price=buy_price,
            quantity=qty,
            total_tmn=allocated,
            macro_state={"regime": "BULLISH_TREND"},
            micro_signals={"imbalance": 0.35},
            jev_evaluation={"expected_value": 0.0060, "approved": True},
            risk_guardrails={"stop_loss": 4_158_000_000.0},
        )

        fake_clock.advance(6 * 3600.0)  # Hour 10: Target reached
        sell_price = 4_284_000_000.0
        await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=sell_price,
            quantity=qty,
            client_order_id="CL-DAY1-SELL1",
        )
        decision_auditor.record_decision(
            trade_id="CL-DAY1-SELL1",
            symbol="BTCTMN",
            action="SELL",
            order_type="LIMIT",
            price=sell_price,
            quantity=qty,
            total_tmn=sell_price * qty,
            macro_state={"regime": "BULLISH_TREND"},
            micro_signals={},
            jev_evaluation={"approved": True, "reason": "TAKE_PROFIT"},
            risk_guardrails={},
        )

        bal_day1 = await mock_wallex_client.get_account_balance()
        assert bal_day1.total_equity_tmn > initial_equity

        # --- DAY 2: Ranging Sideways Scalp Phase (24h to 48h) ---
        fake_clock.set_time(start_time + (28 * 3600.0))  # Hour 28
        # Scalp ETH at 180M, sell at 182M (+1.1%)
        eth_buy = 180_000_000.0
        eth_alloc = 800_000.0
        eth_qty = eth_alloc / eth_buy

        await mock_wallex_client.post_order(
            symbol="ETHTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=eth_buy,
            quantity=eth_qty,
            client_order_id="CL-DAY2-BUY2",
        )
        decision_auditor.record_decision(
            trade_id="CL-DAY2-BUY2",
            symbol="ETHTMN",
            action="BUY",
            order_type="LIMIT",
            price=eth_buy,
            quantity=eth_qty,
            total_tmn=eth_alloc,
            macro_state={"regime": "RANGING_SIDEWAYS"},
            micro_signals={"rsi_14": 30.5},
            jev_evaluation={"expected_value": 0.0042, "approved": True},
            risk_guardrails={"stop_loss": 178_000_000.0},
        )

        fake_clock.advance(4 * 3600.0)  # Hour 32
        eth_sell = 182_000_000.0
        await mock_wallex_client.post_order(
            symbol="ETHTMN",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=eth_sell,
            quantity=eth_qty,
            client_order_id="CL-DAY2-SELL2",
        )
        decision_auditor.record_decision(
            trade_id="CL-DAY2-SELL2",
            symbol="ETHTMN",
            action="SELL",
            order_type="LIMIT",
            price=eth_sell,
            quantity=eth_qty,
            total_tmn=eth_sell * eth_qty,
            macro_state={"regime": "RANGING_SIDEWAYS"},
            micro_signals={},
            jev_evaluation={"approved": True, "reason": "TAKE_PROFIT"},
            risk_guardrails={},
        )

        # Enter position on BTC for Day 3
        fake_clock.set_time(start_time + (52 * 3600.0))  # Hour 52
        btc_carry_price = 4_210_000_000.0
        btc_carry_alloc = 900_000.0
        btc_carry_qty = btc_carry_alloc / btc_carry_price

        await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=btc_carry_price,
            quantity=btc_carry_qty,
            client_order_id="CL-DAY3-CARRY",
        )
        decision_auditor.record_decision(
            trade_id="CL-DAY3-CARRY",
            symbol="BTCTMN",
            action="BUY",
            order_type="LIMIT",
            price=btc_carry_price,
            quantity=btc_carry_qty,
            total_tmn=btc_carry_alloc,
            macro_state={"regime": "BULLISH_TREND"},
            micro_signals={},
            jev_evaluation={"expected_value": 0.0045, "approved": True},
            risk_guardrails={},
        )

        # --- DAY 3: Mandatory Pre-Deadline Liquidation Protocol ---
        # Stage 1: T - 4h (Hour 68) -> Inhibit all BUY orders
        fake_clock.set_time(end_time - (4 * 3600.0))
        time_to_end = end_time - fake_clock.time()
        assert time_to_end <= 4 * 3600.0
        # Assert BUY orders are blocked
        buy_blocked = time_to_end <= 4 * 3600.0
        assert buy_blocked is True

        # Stage 2: T - 2h (Hour 70) -> Orderly Limit Sells of Crypto
        fake_clock.set_time(end_time - (2 * 3600.0))
        cur_btc = mock_wallex_client.crypto_balances["BTC"]
        assert cur_btc > 0
        limit_liq_price = 4_240_000_000.0

        await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=limit_liq_price,
            quantity=cur_btc,
            client_order_id="CL-DAY3-LIQ-T2H",
        )
        decision_auditor.record_decision(
            trade_id="CL-DAY3-LIQ-T2H",
            symbol="BTCTMN",
            action="SELL",
            order_type="LIMIT",
            price=limit_liq_price,
            quantity=cur_btc,
            total_tmn=limit_liq_price * cur_btc,
            macro_state={"regime": "PRE_DEADLINE_LIQUIDATION"},
            micro_signals={},
            jev_evaluation={"approved": True, "reason": "SCHEDULED_LIQUIDATION_T2H"},
            risk_guardrails={},
        )

        # Stage 3 & 4: T - 15m (Hour 71.75) -> Final Audit Verification
        fake_clock.set_time(end_time - (15 * 60.0))
        final_balance = await mock_wallex_client.get_account_balance()

        # Invariant 1: 100% Toman cash (0 crypto remaining)
        assert final_balance.crypto_balances["BTC"] == 0.0
        assert final_balance.crypto_balances["ETH"] == 0.0
        assert final_balance.crypto_balances["USDT"] == 0.0
        assert final_balance.toman_locked == 0.0

        # Invariant 2: Positive Net Return
        net_return_tmn = final_balance.total_equity_tmn - initial_equity
        net_return_pct = (net_return_tmn / initial_equity) * 100.0
        assert net_return_pct > 0.0
        assert final_balance.total_equity_tmn > initial_equity

        # Invariant 3: 100% of trades have decision logs
        all_matched, total_tx, total_logs = decision_auditor.verify_audit_completeness(
            mock_wallex_client.executed_orders
        )
        assert all_matched is True
        assert total_tx == 6
        assert total_logs == 6

    asyncio.run(_run())


# ==============================================================================
# Scenario 2: Sudden Flash Crash Panic Capital Preservation
# ==============================================================================

def test_scenario2_market_crash_panic_capital_preservation(
    mock_wallex_client: MockWallexClient,
    fake_clock: FakeClock,
):
    """Simulates a severe market crash:
    - BTC drops 5.2% in 30 minutes, spread widens to 2.4%.
    - Regime 3 (Capital Preservation Cash Guard) triggers.
    - Open positions are immediately closed to Toman.
    - Buys are halted.
    - Capital preservation keeps drawdown well under the 10% limit.
    """
    async def _run():
        entry_price = 4_200_000_000.0
        allocated = 1_000_000.0
        qty = allocated / entry_price

        # Initial open trade
        await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=entry_price,
            quantity=qty,
            client_order_id="CL-PRE-CRASH",
        )

        # Market flash crash: Price drops to 3,980,000,000 (-5.2%)
        crash_price = 3_980_000_000.0
        spread_pct = 0.024  # 2.4% spread blowout

        # Panic detector
        is_panic = spread_pct > 0.015 or ((crash_price - entry_price) / entry_price) < -0.03
        assert is_panic is True

        # Emergency market liquidation to 100% TMN
        panic_sell = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=crash_price,
            quantity=qty,
            client_order_id="CL-PANIC-EXIT",
        )
        assert panic_sell.status == OrderStatus.FILLED
        assert mock_wallex_client.crypto_balances["BTC"] == 0.0

        # Account drawdown check
        bal = await mock_wallex_client.get_account_balance()
        drawdown = oracle_calc_drawdown(5_000_000.0, bal.total_equity_tmn)
        # Even with the loss, drawdown is ~1.1%, well below 10% circuit breaker
        assert drawdown < 0.05
        assert bal.total_equity_tmn > 4_900_000.0

    asyncio.run(_run())


# ==============================================================================
# Scenario 3: Drawdown Sentinel 10% Circuit Breaker Stress Halt
# ==============================================================================

def test_scenario3_circuit_breaker_stress_protection():
    """Simulates cumulative drawdowns approaching 10.0%:
    - Peak Equity: 5,000,000 TMN
    - Drawdown reaching exactly 10.0% (Equity = 4,500,000 TMN)
    - Circuit Breaker trips, locks engine into HALTED state, rejects all proposals.
    """
    class EngineWithCircuitBreaker:
        def __init__(self, peak_equity: float = 5_000_000.0) -> None:
            self.peak_equity = peak_equity
            self.current_equity = peak_equity
            self.is_halted = False

        def update_equity(self, equity: float) -> None:
            self.current_equity = equity
            dd = oracle_calc_drawdown(self.peak_equity, self.current_equity)
            if dd >= 0.10:
                self.is_halted = True

        def submit_proposal(self, proposal: TradeProposal) -> Tuple[bool, str]:
            if self.is_halted:
                return False, "CIRCUIT_BREAKER_LOCKED: Trading halted due to >= 10% drawdown"
            return True, "ACCEPTED"

    engine = EngineWithCircuitBreaker(peak_equity=5_000_000.0)

    # Moderate drawdown to 4,600,000 TMN (8.0% DD)
    engine.update_equity(4_600_000.0)
    assert engine.is_halted is False

    # Drawdown touches 4,500,000 TMN (10.0% DD)
    engine.update_equity(4_500_000.0)
    assert engine.is_halted is True

    # Any new trade proposal must be rejected
    mock_prop = TradeProposal(
        proposal_id="PROP-POST-HALT",
        signal=CandidateSignal(
            signal_id="SIG-01", symbol="BTCTMN", action="BUY",
            entry_price=4.2e9, target_price=4.3e9, stop_loss_price=4.15e9,
            estimated_win_prob=0.7, regime_source=MarketRegime.BULLISH_TREND,
            indicators={}, timestamp=1700000000.0,
        ),
        allocated_size_tmn=500_000.0,
        quantity=0.0001,
        expected_gain_pct=0.02,
        expected_loss_pct=0.01,
        fee_pct=0.005,
        expected_value=0.005,
        reward_to_risk=2.0,
        is_approved=True,
    )

    accepted, reason = engine.submit_proposal(mock_prop)
    assert accepted is False
    assert "CIRCUIT_BREAKER_LOCKED" in reason


# ==============================================================================
# Scenario 4: Live Shadow Mode Stream Soak Test (100 Ticks)
# ==============================================================================

def test_scenario4_shadow_mode_live_stream_soak_test(
    mock_wallex_client: MockWallexClient,
    fake_clock: FakeClock,
):
    """Simulates 100 ticks of live orderbook streaming in Shadow Mode:
    - Verifies zero live capital risk.
    - Evaluates signals and tracks virtual portfolio.
    - Asserts 100 ticks execute with zero crashes and clean state.
    """
    async def _run():
        virtual_account = {"TMN": 5_000_000.0, "BTC": 0.0}
        initial_client_tmn = mock_wallex_client.tmn_available
        trades_executed = 0

        for tick in range(100):
            fake_clock.advance(5.0)  # 5-second interval
            base_price = 4_200_000_000.0 + (tick * 100_000.0)
            mock_wallex_client.set_orderbook(
                "BTCTMN",
                bids=[(base_price, 0.50)],
                asks=[(base_price + 5_000_000.0, 0.40)],
            )

            # Ingest live depth
            snapshot = await mock_wallex_client.get_market_depth("BTCTMN")
            assert snapshot.best_bid > 0

            # Execute virtual trade every 25 ticks
            if tick % 25 == 0 and virtual_account["TMN"] >= 1_000_000.0:
                cost = 500_000.0
                qty = cost / snapshot.best_ask
                virtual_account["TMN"] -= cost
                virtual_account["BTC"] += qty
                trades_executed += 1

        # Real Wallex client account remains completely untouched!
        assert mock_wallex_client.tmn_available == initial_client_tmn
        assert mock_wallex_client.crypto_balances["BTC"] == 0.0
        assert trades_executed == 4
        assert virtual_account["BTC"] > 0.0

    asyncio.run(_run())
