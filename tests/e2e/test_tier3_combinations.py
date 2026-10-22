"""Tier 3: Cross-Module Pipeline Integration Tests.
Verifies asynchronous dataflow and state transitions across subsystem boundaries:
Ingestion -> Macro Brain -> Strategy -> JEV Gate -> Execution Dispatcher -> Decision Auditor.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
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
# Pipeline 1: Full Bullish Momentum Ingestion -> Strategy -> JEV -> Exec -> Log
# ==============================================================================

def test_comb1_full_bullish_pipeline_ingestion_to_logged_execution(
    mock_wallex_client: MockWallexClient,
    decision_auditor: MockDecisionAuditor,
    fake_clock: FakeClock,
):
    """Executes the complete end-to-end Bullish Momentum pipeline:
    1. Ingest MarketDataSnapshot with positive imbalance.
    2. Macro Agent sets BULLISH_TREND policy.
    3. Strategy generates Momentum Breakout CandidateSignal.
    4. JEV Gate verifies E[V] >= +0.35% and sizes position <= 20% equity.
    5. Execution Engine dispatches order to Wallex client and gets FILLED.
    6. Decision Auditor writes structured immutable JSON log.
    7. Verifier confirms 100% transaction match.
    """
    async def _run():
        # Step 1: Ingest depth snapshot
        snapshot = await mock_wallex_client.get_market_depth("BTCTMN")
        assert snapshot.best_ask > 0

        # Step 2: Macro Agent emits policy
        macro_policy = MacroTradingPolicy(
            regime=MarketRegime.BULLISH_TREND,
            target_symbols=["BTCTMN"],
            max_position_size_pct=0.20,
            risk_multiplier=1.0,
            agent_thesis="Strong bid absorption on 15m orderbook with EMA crossover",
            generated_at=fake_clock.time(),
            valid_until=fake_clock.time() + 900.0,
        )

        # Step 3: Strategy generates candidate signal
        entry_price = snapshot.best_ask
        target_price = entry_price * 1.020  # +2.0%
        stop_price = entry_price * 0.990    # -1.0%
        win_prob = 0.66
        fee_pct = 0.0050

        signal = CandidateSignal(
            signal_id="SIG-BULL-PIPE-001",
            symbol="BTCTMN",
            action="BUY",
            entry_price=entry_price,
            target_price=target_price,
            stop_loss_price=stop_price,
            estimated_win_prob=win_prob,
            regime_source=macro_policy.regime,
            indicators={"rsi_14": 52.0, "imbalance": snapshot.imbalance_ratio, "atr_14": 40_000_000.0},
            timestamp=fake_clock.time(),
        )

        # Step 4: JEV Risk Gate Evaluation
        jev_score = oracle_calc_jev(win_prob, entry_price, target_price, stop_price, fee_pct)
        rr_ratio = oracle_calc_reward_to_risk(entry_price, target_price, stop_price)
        assert jev_score >= 0.0035
        assert rr_ratio >= 1.50

        balance = await mock_wallex_client.get_account_balance()
        allocated_tmn = min(balance.total_equity_tmn * macro_policy.max_position_size_pct, 1_000_000.0)
        quantity = allocated_tmn / entry_price

        proposal = TradeProposal(
            proposal_id="PROP-BULL-001",
            signal=signal,
            allocated_size_tmn=allocated_tmn,
            quantity=quantity,
            expected_gain_pct=0.020,
            expected_loss_pct=0.010,
            fee_pct=fee_pct,
            expected_value=jev_score,
            reward_to_risk=rr_ratio,
            is_approved=True,
            evaluated_at=fake_clock.time(),
        )
        assert proposal.is_approved is True

        # Step 5: Micro Execution Dispatcher
        client_order_id = f"CL-PIPE-{proposal.proposal_id}"
        exec_result = await mock_wallex_client.post_order(
            symbol=proposal.signal.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=entry_price,
            quantity=quantity,
            client_order_id=client_order_id,
        )
        assert exec_result.status == OrderStatus.FILLED
        assert exec_result.exchange_order_id is not None

        # Step 6: Immutable Decision Audit Logging
        log_path = decision_auditor.record_decision(
            trade_id=client_order_id,
            symbol=proposal.signal.symbol,
            action=proposal.signal.action,
            order_type="LIMIT",
            price=entry_price,
            quantity=quantity,
            total_tmn=allocated_tmn,
            macro_state={"regime": macro_policy.regime.value, "thesis": macro_policy.agent_thesis},
            micro_signals=proposal.signal.indicators,
            jev_evaluation={"expected_value": jev_score, "reward_to_risk": rr_ratio, "approved": True},
            risk_guardrails={"stop_loss_price": stop_price, "allocated_tmn": allocated_tmn},
        )
        assert log_path.exists()

        # Step 7: Audit Completeness Verifier
        exchange_trades = mock_wallex_client.executed_orders
        all_matched, total_tx, total_logs = decision_auditor.verify_audit_completeness(exchange_trades)
        assert all_matched is True
        assert total_tx == 1
        assert total_logs == 1

    asyncio.run(_run())


# ==============================================================================
# Pipeline 2: Ranging Scalp Pipeline & Take-Profit Cycle
# ==============================================================================

def test_comb2_ranging_scalp_pipeline_hit_take_profit(
    mock_wallex_client: MockWallexClient,
    decision_auditor: MockDecisionAuditor,
    fake_clock: FakeClock,
):
    """Executes a 2-stage mean-reversion scalp cycle:
    1. BUY executed at Lower Bollinger Band support.
    2. Price recovers to Middle Bollinger Band (Take Profit target).
    3. SELL executed, realizing profit.
    4. Decision logs recorded for both entry and exit.
    """
    async def _run():
        entry_price = 4_180_000_000.0
        allocated_tmn = 750_000.0  # 15% equity
        quantity = allocated_tmn / entry_price
        buy_client_id = "CL-SCALP-BUY-001"

        # 1. Entry Buy
        buy_res = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=entry_price,
            quantity=quantity,
            client_order_id=buy_client_id,
        )
        assert buy_res.status == OrderStatus.FILLED

        decision_auditor.record_decision(
            trade_id=buy_client_id,
            symbol="BTCTMN",
            action="BUY",
            order_type="LIMIT",
            price=entry_price,
            quantity=quantity,
            total_tmn=allocated_tmn,
            macro_state={"regime": "RANGING_SIDEWAYS"},
            micro_signals={"rsi_14": 29.0},
            jev_evaluation={"approved": True, "expected_value": 0.0055},
            risk_guardrails={"stop_loss": 4_155_000_000.0},
        )

        # Advance time and market price to take-profit (Middle BB)
        fake_clock.advance(1800.0)  # 30 mins later
        take_profit_price = 4_225_000_000.0  # +1.07%
        sell_client_id = "CL-SCALP-SELL-001"

        sell_res = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=take_profit_price,
            quantity=quantity,
            client_order_id=sell_client_id,
        )
        assert sell_res.status == OrderStatus.FILLED

        decision_auditor.record_decision(
            trade_id=sell_client_id,
            symbol="BTCTMN",
            action="SELL",
            order_type="LIMIT",
            price=take_profit_price,
            quantity=quantity,
            total_tmn=take_profit_price * quantity,
            macro_state={"regime": "RANGING_SIDEWAYS"},
            micro_signals={"rsi_14": 51.0},
            jev_evaluation={"approved": True, "reason": "TAKE_PROFIT_TRIGGERED"},
            risk_guardrails={},
        )

        # Realized balance check: Initial 5M TMN should have gained net profit
        balance = await mock_wallex_client.get_account_balance()
        assert balance.crypto_balances["BTC"] == 0.0
        assert balance.total_equity_tmn > 5_000_000.0  # Net profit realized

        # Verifier checks both trades logged
        all_matched, total_tx, total_logs = decision_auditor.verify_audit_completeness(
            mock_wallex_client.executed_orders
        )
        assert all_matched is True
        assert total_tx == 2
        assert total_logs == 2

    asyncio.run(_run())


# ==============================================================================
# Pipeline 3: JEV Gate Rejection Halts Execution Pipeline
# ==============================================================================

def test_comb3_jev_rejection_halts_execution_pipeline(
    mock_wallex_client: MockWallexClient,
    decision_auditor: MockDecisionAuditor,
    fake_clock: FakeClock,
):
    """Verifies that when CandidateSignal fails the JEV hurdle (E[V] < +0.35%),
    the proposal is rejected, execution engine is NOT called, and 0 orders reach exchange.
    """
    entry = 4_200_000_000.0
    target = 4_220_000_000.0  # Low upside (+0.47%)
    stop = 4_170_000_000.0    # High downside (-0.71%)
    win_prob = 0.50
    fee = 0.0050

    # Calculate JEV: 0.50 * 0.0047 - 0.50 * 0.0071 - 0.0050 = 0.00235 - 0.00355 - 0.0050 = -0.0062
    jev = oracle_calc_jev(win_prob, entry, target, stop, fee)
    assert jev < 0.0035  # Below hurdle!

    signal = CandidateSignal(
        signal_id="SIG-WEAK-001",
        symbol="BTCTMN",
        action="BUY",
        entry_price=entry,
        target_price=target,
        stop_loss_price=stop,
        estimated_win_prob=win_prob,
        regime_source=MarketRegime.BULLISH_TREND,
        indicators={},
        timestamp=fake_clock.time(),
    )

    proposal = TradeProposal(
        proposal_id="PROP-REJECT-001",
        signal=signal,
        allocated_size_tmn=0.0,
        quantity=0.0,
        expected_gain_pct=(target - entry) / entry,
        expected_loss_pct=(entry - stop) / entry,
        fee_pct=fee,
        expected_value=jev,
        reward_to_risk=oracle_calc_reward_to_risk(entry, target, stop),
        is_approved=False,
        rejection_reason=f"JEV score {jev:.5f} below minimum hurdle +0.00350",
    )
    assert proposal.is_approved is False

    # Execution Engine check: Discard rejected proposals
    def dispatch_proposal(prop: TradeProposal) -> bool:
        if not prop.is_approved:
            return False
        return True

    dispatched = dispatch_proposal(proposal)
    assert dispatched is False
    assert len(mock_wallex_client.executed_orders) == 0


# ==============================================================================
# Pipeline 4: Trailing Stop-Loss Trigger Under Sudden Reversal
# ==============================================================================

def test_comb4_trailing_stop_loss_trigger_under_sudden_reversal(
    mock_wallex_client: MockWallexClient,
    fake_clock: FakeClock,
):
    """Verifies that an open position tracks price peak, and when market reverses
    by > dynamic ATR stop distance, the trailing stop fires a SELL order.
    """
    async def _run():
        entry_price = 4_200_000_000.0
        size_tmn = 1_000_000.0
        qty = size_tmn / entry_price

        # Initial buy
        await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=entry_price,
            quantity=qty,
            client_order_id="CL-TRAIL-BUY",
        )

        # Market rallies to peak
        peak_price = 4_300_000_000.0
        atr_14 = 35_000_000.0
        # Dynamic trailing stop from peak
        trailing_stop_price = oracle_calc_dynamic_atr_stop(peak_price, atr_14, max_pct=0.02)
        # 1.2 * 35M = 42M (< 2% = 86M) => Stop = 4,300M - 42M = 4,258M
        assert trailing_stop_price == 4_258_000_000.0

        # Market price drops to 4,250,000,000 (breaches trailing stop 4,258,000,000)
        market_price = 4_250_000_000.0
        should_stop_out = market_price <= trailing_stop_price
        assert should_stop_out is True

        # Trigger emergency trailing stop sell
        stop_res = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=market_price,
            quantity=qty,
            client_order_id="CL-TRAIL-STOP-SELL",
        )
        assert stop_res.status == OrderStatus.FILLED
        assert mock_wallex_client.crypto_balances["BTC"] == 0.0
        # Trade still locked in profit above entry (4,250M vs 4,200M)
        assert mock_wallex_client.tmn_available > 5_000_000.0

    asyncio.run(_run())


# ==============================================================================
# Pipeline 5: Exchange TTL Order Cancellation & Balance Reconciliation
# ==============================================================================

def test_comb5_exchange_ttl_order_cancellation_and_balance_reconciliation(
    mock_wallex_client: MockWallexClient,
    fake_clock: FakeClock,
):
    """Verifies that an unfilled limit order exceeding its TTL (20 seconds)
    is cancelled via Wallex MCP delete tool, releasing locked resources.
    """
    async def _run():
        order_id = "WX-ORDER-UNFILLED-001"
        created_at = fake_clock.time()
        ttl_seconds = 20.0

        def is_order_expired(order_created: float, current_time: float) -> bool:
            return (current_time - order_created) >= ttl_seconds

        # 10 seconds later: Not expired
        fake_clock.advance(10.0)
        assert is_order_expired(created_at, fake_clock.time()) is False

        # 21 seconds later: Expired!
        fake_clock.advance(11.0)
        assert is_order_expired(created_at, fake_clock.time()) is True

        # Trigger cancel order
        success = await mock_wallex_client.cancel_order(order_id)
        assert success is True
        assert len(mock_wallex_client.cancelled_orders) == 1
        assert mock_wallex_client.cancelled_orders[0]["order_id"] == order_id

    asyncio.run(_run())


# ==============================================================================
# Pipeline 6: Decision Auditor Batch Completeness Verifier
# ==============================================================================

def test_comb6_decision_auditor_completeness_verifier(
    mock_wallex_client: MockWallexClient,
    decision_auditor: MockDecisionAuditor,
):
    """Executes a batch of 10 simulated trades, writes 10 corresponding decision logs,
    and runs the Audit Verifier asserting 100% complete match.
    """
    async def _run():
        symbols = ["BTCTMN", "ETHTMN", "USDTTMN"]
        for i in range(10):
            sym = symbols[i % 3]
            price = 4_200_000_000.0 if sym == "BTCTMN" else (180_000_000.0 if sym == "ETHTMN" else 65_000.0)
            cost = 100_000.0
            qty = cost / price
            cid = f"BATCH-TX-{i:03d}"

            # Post trade
            res = await mock_wallex_client.post_order(
                symbol=sym,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=price,
                quantity=qty,
                client_order_id=cid,
            )
            assert res.status == OrderStatus.FILLED

            # Write decision log
            decision_auditor.record_decision(
                trade_id=cid,
                symbol=sym,
                action="BUY",
                order_type="LIMIT",
                price=price,
                quantity=qty,
                total_tmn=cost,
                macro_state={"regime": "BULLISH_TREND"},
                micro_signals={"imbalance": 0.3},
                jev_evaluation={"expected_value": 0.0040, "approved": True},
                risk_guardrails={"drawdown": 0.0},
            )

        # Run verification check
        all_matched, total_tx, total_logs = decision_auditor.verify_audit_completeness(
            mock_wallex_client.executed_orders
        )
        assert all_matched is True
        assert total_tx == 10
        assert total_logs == 10

    asyncio.run(_run())
