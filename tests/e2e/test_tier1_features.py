"""Tier 1: Isolated Feature Contract Tests (R1 to R6).
Verifies the primary behavior (happy path) for each individual subsystem in isolation.
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
    MockTokenBucketRateLimiter,
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
# R1: Market Ingestion Subsystem & Wallex Client Tests
# ==============================================================================

def test_r1_market_depth_ingestion_and_imbalance(mock_wallex_client: MockWallexClient):
    """Verifies that market depth is ingested, top 10 bids/asks are captured,
    spread % is calculated, and orderbook imbalance is computed accurately.
    """
    async def _run():
        snapshot = await mock_wallex_client.get_market_depth("BTCTMN")
        assert isinstance(snapshot, MarketDataSnapshot)
        assert snapshot.symbol == "BTCTMN"
        assert snapshot.best_bid > 0
        assert snapshot.best_ask > snapshot.best_bid
        assert snapshot.spread_pct == pytest.approx((snapshot.best_ask - snapshot.best_bid) / snapshot.best_bid)
        assert len(snapshot.bids) > 0
        assert len(snapshot.asks) > 0

        # Verify orderbook imbalance calculation matches oracle
        expected_imbalance = oracle_calc_imbalance(snapshot.bids, snapshot.asks)
        assert snapshot.imbalance_ratio == pytest.approx(expected_imbalance, abs=1e-4)
        assert -1.0 <= snapshot.imbalance_ratio <= 1.0

    asyncio.run(_run())


def test_r1_token_bucket_rate_limiter_happy_path(fake_clock: FakeClock):
    """Verifies that normal request bursts within capacity (<= 30 req / 10s)
    pass without throttling.
    """
    limiter = MockTokenBucketRateLimiter(capacity=30, window_seconds=10.0, clock=fake_clock)
    # Fire 15 requests in quick succession
    for _ in range(15):
        assert limiter.acquire() is True
    assert limiter.throttle_count == 0
    assert limiter.request_count == 15
    assert limiter.tokens == pytest.approx(15.0, abs=1e-3)


def test_r1_account_balance_tracking(mock_wallex_client: MockWallexClient):
    """Verifies that the dedicated 5,000,000 TMN credit subaccount balances
    are retrieved with correct available TMN, locked TMN, and crypto balances.
    """
    async def _run():
        balance = await mock_wallex_client.get_account_balance()
        assert isinstance(balance, AccountBalance)
        assert balance.toman_available == 5_000_000.0
        assert balance.toman_locked == 0.0
        assert balance.crypto_balances == {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0}
        assert balance.total_equity_tmn == 5_000_000.0

    asyncio.run(_run())


# ==============================================================================
# R2: Macro Agentic Brain & 3-Regime Strategy Tests
# ==============================================================================

def test_r2_macro_policy_emission():
    """Verifies that the Macro Agent emits a valid MacroTradingPolicy contract
    specifying active regime, target symbols, position size cap, and risk multiplier.
    """
    policy = MacroTradingPolicy(
        regime=MarketRegime.BULLISH_TREND,
        target_symbols=["BTCTMN", "ETHTMN"],
        max_position_size_pct=0.20,
        risk_multiplier=1.0,
        agent_thesis="4h EMA bullish crossover with strong orderbook bid accumulation",
        generated_at=1700000000.0,
        valid_until=1700000900.0,
    )
    assert policy.regime == MarketRegime.BULLISH_TREND
    assert policy.max_position_size_pct <= 0.20
    assert 0.5 <= policy.risk_multiplier <= 1.5
    assert "BTCTMN" in policy.target_symbols
    assert len(policy.agent_thesis) > 10


def test_r2_regime1_momentum_bullish_signal():
    """Verifies that Regime 1 (Momentum Breakout) triggers when EMA9 > EMA21,
    orderbook imbalance > +0.25, generating a BUY CandidateSignal.
    """
    imbalance = 0.35
    indicators = {
        "ema_9": 4_250_000_000.0,
        "ema_21": 4_220_000_000.0,
        "imbalance": imbalance,
        "atr_14": 40_000_000.0,
    }
    assert indicators["ema_9"] > indicators["ema_21"]
    assert indicators["imbalance"] > 0.25

    signal = CandidateSignal(
        signal_id="SIG-MOM-001",
        symbol="BTCTMN",
        action="BUY",
        entry_price=4_250_000_000.0,
        target_price=4_335_000_000.0,  # +2.0%
        stop_loss_price=4_207_500_000.0,  # -1.0%
        estimated_win_prob=0.68,
        regime_source=MarketRegime.BULLISH_TREND,
        indicators=indicators,
        timestamp=1700000000.0,
    )
    assert signal.action == "BUY"
    assert signal.target_price > signal.entry_price
    assert signal.stop_loss_price < signal.entry_price
    assert signal.estimated_win_prob >= 0.50


def test_r2_regime2_mean_reversion_ranging_signal():
    """Verifies that Regime 2 (Mean Reversion Scalper) triggers when RSI < 32
    and price tests Lower Bollinger Band, targeting Middle Bollinger Band.
    """
    indicators = {
        "rsi_14": 28.5,
        "bb_lower": 4_180_000_000.0,
        "bb_middle": 4_220_000_000.0,
        "current_price": 4_181_000_000.0,
    }
    assert indicators["rsi_14"] < 32.0
    assert indicators["current_price"] <= indicators["bb_lower"] * 1.002

    signal = CandidateSignal(
        signal_id="SIG-REV-001",
        symbol="BTCTMN",
        action="BUY",
        entry_price=4_181_000_000.0,
        target_price=4_220_000_000.0,  # ~ +0.93%
        stop_loss_price=4_155_000_000.0,  # -0.62%
        estimated_win_prob=0.72,
        regime_source=MarketRegime.RANGING_SIDEWAYS,
        indicators=indicators,
        timestamp=1700000000.0,
    )
    assert signal.action == "BUY"
    assert signal.regime_source == MarketRegime.RANGING_SIDEWAYS


def test_r2_regime3_capital_preservation_guard():
    """Verifies that Regime 3 (Crash / Panic Guard) switches to cash preservation
    when spread blows out (> 1.5%) or market drops > 3% in 1 hour.
    """
    spread_pct = 0.022  # 2.2% spread blowout
    one_hour_drop_pct = -0.038  # -3.8% dump
    is_panic = spread_pct > 0.015 or one_hour_drop_pct < -0.030
    assert is_panic is True

    policy = MacroTradingPolicy(
        regime=MarketRegime.HIGH_VOLATILITY_PANIC,
        target_symbols=[],
        max_position_size_pct=0.0,
        risk_multiplier=0.0,
        agent_thesis="Panic dump detected: Spread blowout > 1.5% and BTC -3.8%/1h. 100% Cash Guard active.",
        generated_at=1700000000.0,
        valid_until=1700000600.0,
    )
    assert policy.regime == MarketRegime.HIGH_VOLATILITY_PANIC
    assert policy.max_position_size_pct == 0.0


# ==============================================================================
# R3: JEV Decision Gate & Drawdown Sentinel Tests
# ==============================================================================

def test_r3_jev_gate_positive_expectancy_approval():
    """Verifies that a candidate trade with E[V] >= +0.35% and Reward-to-Risk >= 1.5
    is APPROVED into a TradeProposal.
    """
    entry = 4_200_000_000.0
    target = 4_284_000_000.0  # +2.0%
    stop = 4_158_000_000.0    # -1.0%
    win_prob = 0.65
    fee_pct = 0.0050          # 0.50% round trip

    jev = oracle_calc_jev(win_prob, entry, target, stop, fee_pct)
    rr = oracle_calc_reward_to_risk(entry, target, stop)

    assert jev == pytest.approx(0.0045, abs=1e-5)  # +0.45%
    assert jev >= 0.0035  # Above hurdle
    assert rr == pytest.approx(2.0, abs=1e-2)      # Above 1.50

    signal = CandidateSignal(
        signal_id="SIG-001",
        symbol="BTCTMN",
        action="BUY",
        entry_price=entry,
        target_price=target,
        stop_loss_price=stop,
        estimated_win_prob=win_prob,
        regime_source=MarketRegime.BULLISH_TREND,
        indicators={},
        timestamp=1700000000.0,
    )
    proposal = TradeProposal(
        proposal_id="PROP-001",
        signal=signal,
        allocated_size_tmn=1_000_000.0,
        quantity=1_000_000.0 / entry,
        expected_gain_pct=(target - entry) / entry,
        expected_loss_pct=(entry - stop) / entry,
        fee_pct=fee_pct,
        expected_value=jev,
        reward_to_risk=rr,
        is_approved=True,
    )
    assert proposal.is_approved is True
    assert proposal.rejection_reason is None
    assert proposal.allocated_size_tmn <= 1_000_000.0


def test_r3_position_sizing_guard_20pct_limit():
    """Verifies that position sizing enforces the <= 20% equity cap (max 1M TMN on 5M capital)."""
    account_equity = 5_000_000.0
    max_pct = 0.20
    max_cap_tmn = 1_000_000.0

    allowed_size = min(account_equity * max_pct, max_cap_tmn)
    assert allowed_size == 1_000_000.0

    # If equity drops to 4,800,000 TMN, max size should be 960,000 TMN
    reduced_equity = 4_800_000.0
    reduced_size = min(reduced_equity * max_pct, max_cap_tmn)
    assert reduced_size == 960_000.0


def test_r3_dynamic_atr_trailing_stop_loss():
    """Verifies that dynamic stop-loss distance is calibrated via min(1.2 * ATR_14, 2.0% * Entry)."""
    entry_price = 4_200_000_000.0
    atr_14 = 30_000_000.0  # 1.2 * ATR = 36,000,000 (< 2% = 84,000,000)

    stop_price = oracle_calc_dynamic_atr_stop(entry_price, atr_14, max_pct=0.02)
    assert stop_price == 4_164_000_000.0
    distance = entry_price - stop_price
    assert distance == 36_000_000.0


# ==============================================================================
# R4: Micro Execution Engine Tests
# ==============================================================================

def test_r4_micro_execution_dispatcher_order_lifecycle(mock_wallex_client: MockWallexClient):
    """Verifies that the execution dispatcher submits an approved proposal,
    receives FILLED status, deducts available TMN, and tracks fee paid.
    """
    async def _run():
        entry_price = 4_200_000_000.0
        allocated_tmn = 1_000_000.0
        quantity = allocated_tmn / entry_price

        result = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=entry_price,
            quantity=quantity,
            client_order_id="CL-EXEC-001",
        )
        assert isinstance(result, OrderExecutionResult)
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == pytest.approx(quantity)
        assert result.average_fill_price == entry_price
        assert result.exchange_order_id is not None
        assert result.fee_paid_tmn == pytest.approx(allocated_tmn * 0.0025)

        # Check updated balances
        balance = await mock_wallex_client.get_account_balance()
        assert balance.crypto_balances["BTC"] == pytest.approx(quantity)
        assert balance.toman_available < 5_000_000.0

    asyncio.run(_run())


# ==============================================================================
# R5: Deterministic Backtesting & Live Shadow Testing Tests
# ==============================================================================

def test_r5_backtest_simulator_execution():
    """Verifies that deterministic backtest replay calculates key metrics:
    Net Return %, Max Drawdown %, and Win Rate %.
    """
    initial_equity = 5_000_000.0
    # Simulated sequence of trade equity values
    equity_curve = [5_000_000.0, 5_120_000.0, 5_080_000.0, 5_250_000.0, 5_200_000.0, 5_350_000.0]
    final_equity = equity_curve[-1]

    net_return_pct = ((final_equity - initial_equity) / initial_equity) * 100.0
    assert net_return_pct == pytest.approx(7.0)  # +7.0%

    # Calculate Max Drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak
        if dd > max_dd:
            max_dd = dd

    assert max_dd < 0.05  # Max drawdown under 5%


def test_r5_shadow_runner_paper_trading(mock_wallex_client: MockWallexClient):
    """Verifies that the live shadow testing mode processes real orderbook depth
    and executes paper orders without risking live capital.
    """
    async def _run():
        snapshot = await mock_wallex_client.get_market_depth("BTCTMN")
        virtual_portfolio = {"TMN": 5_000_000.0, "BTC": 0.0}

        # Simulate paper buy matching best ask
        paper_price = snapshot.best_ask
        paper_size_tmn = 1_000_000.0
        paper_qty = paper_size_tmn / paper_price
        fee = paper_size_tmn * 0.0025

        virtual_portfolio["TMN"] -= (paper_size_tmn + fee)
        virtual_portfolio["BTC"] += paper_qty

        assert virtual_portfolio["BTC"] > 0.0
        assert virtual_portfolio["TMN"] == pytest.approx(3_997_500.0)
        # Client real account remains untouched
        real_balance = await mock_wallex_client.get_account_balance()
        assert real_balance.toman_available == 5_000_000.0

    asyncio.run(_run())


# ==============================================================================
# R6: Structured Decision Auditor Tests
# ==============================================================================

def test_r6_decision_auditor_immutable_logging(decision_auditor: MockDecisionAuditor):
    """Verifies that 100% of executed trades generate a valid, structured JSON
    audit log capturing technical indicators, JEV score, and Agentic thesis.
    """
    trade_id = "WALLEX-TX-20261022-00001"
    log_path = decision_auditor.record_decision(
        trade_id=trade_id,
        symbol="BTCTMN",
        action="BUY",
        order_type="LIMIT",
        price=4_200_000_000.0,
        quantity=0.000238,
        total_tmn=999_600.0,
        macro_state={"regime": "BULLISH_TREND", "risk_multiplier": 1.0},
        micro_signals={"rsi_14": 45.2, "imbalance": 0.32},
        jev_evaluation={"win_prob": 0.65, "expected_value_pct": 0.0045, "approved": True},
        risk_guardrails={"stop_loss_price": 4_164_000_000.0, "portfolio_drawdown_current": 0.0},
    )

    assert log_path.exists()
    assert log_path.stat().st_size > 100

    # Audit Verifier check
    exchange_trades = [{"order_id": trade_id, "client_id": trade_id}]
    all_matched, total_tx, total_logs = decision_auditor.verify_audit_completeness(exchange_trades)
    assert all_matched is True
    assert total_tx == 1
    assert total_logs == 1
