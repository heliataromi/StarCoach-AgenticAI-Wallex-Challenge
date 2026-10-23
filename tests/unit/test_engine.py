"""Unit and integration tests for the Micro Execution Engine.

Tests PortfolioTracker, OrderExecutionDispatcher, sub-50ms latency invariants,
JEV gate rejections, 20% sizing limits, and panic cash guard liquidation.
"""
from __future__ import annotations

import asyncio
import time
import pytest
from src.engine.models import EngineMetrics, ExecutionMode, ExecutionStatus, OrderExecutionResult
from src.engine.portfolio import PortfolioTracker
from src.engine.dispatcher import OrderExecutionDispatcher
from src.risk.drawdown import DrawdownSentinel
from src.risk.jev import JEVEvaluator
from src.risk.models import PositionRiskState, RiskConfig
from src.risk.sizing import PositionSizer
from src.strategy.models import MacroTradingPolicy, MarketRegime, SignalAction, TradingSignal
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import AccountBalance, OrderSide, OrderStatus, OrderType


@pytest.fixture
def mock_client():
    return MockWallexClient(initial_tmn=5_000_000.0)


@pytest.fixture
def portfolio():
    return PortfolioTracker(initial_equity_tmn=5_000_000.0)


@pytest.fixture
def dispatcher(mock_client, portfolio):
    config = RiskConfig(max_position_size_pct=0.20)
    return OrderExecutionDispatcher(
        client=mock_client,
        portfolio=portfolio,
        jev_evaluator=JEVEvaluator(config=config),
        position_sizer=PositionSizer(config=config),
    )


class TestPortfolioTracker:
    """Verifies in-memory portfolio lifecycle, equity mark-to-market, and trailing stops."""

    def test_sync_from_exchange_balance(self, portfolio):
        bal = AccountBalance(
            toman_available=3_000_000.0,
            toman_locked=500_000.0,
            total_equity_tmn=3_500_000.0,
            timestamp=time.time(),
            crypto_balances={"BTC": 0.01, "ETH": 0.5, "USDT": 100.0},
        )
        portfolio.sync_from_exchange_balance(bal)
        assert portfolio.tmn_available == 3_000_000.0
        assert portfolio.tmn_locked == 500_000.0
        assert portfolio.crypto_balances["BTC"] == 0.01
        assert portfolio.crypto_balances["ETH"] == 0.5

    def test_record_buy_and_sell_pnl_cycle(self, portfolio):
        # 1. Record BUY fill
        buy_res = OrderExecutionResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            symbol="USDTTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            requested_price=65_000.0,
            requested_quantity=10.0,
            executed_price=65_000.0,
            executed_quantity=10.0,
            fee_paid=975.0,
            client_order_id="BUY-01",
        )
        portfolio.record_fill(buy_res)
        assert portfolio.has_active_position("USDTTMN")
        pos = portfolio.get_position("USDTTMN")
        assert pos.quantity == 10.0
        assert pos.entry_price == 65_000.0
        assert portfolio.crypto_balances["USDT"] == 10.0
        assert portfolio.tmn_available == 5_000_000.0 - (650_000.0 + 975.0)

        initial_stop = pos.stop_loss_price
        # 2. Update price upward and verify trailing stop advances
        portfolio.update_market_price("USDTTMN", 68_000.0, atr_value=500.0)
        pos_updated = portfolio.get_position("USDTTMN")
        assert pos_updated.highest_price_seen == 68_000.0
        assert pos_updated.stop_loss_price > initial_stop

        # 3. Record SELL fill at profit
        sell_res = OrderExecutionResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            symbol="USDTTMN",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            requested_price=68_000.0,
            requested_quantity=10.0,
            executed_price=68_000.0,
            executed_quantity=10.0,
            fee_paid=1_020.0,
            client_order_id="SELL-01",
        )
        portfolio.record_fill(sell_res)
        assert not portfolio.has_active_position("USDTTMN")
        assert portfolio.crypto_balances["USDT"] == 0.0
        assert portfolio.total_realized_pnl_tmn == pytest.approx((68_000.0 - 65_000.0) * 10 - 1_020.0, 1e-4)


class TestOrderExecutionDispatcher:
    """Verifies fast-path order routing, risk gating, latency, and JEV hurdles."""

    def test_hold_signal_skipped_cleanly(self, dispatcher):
        sig = TradingSignal(
            symbol="BTCTMN",
            action=SignalAction.HOLD,
            regime=MarketRegime.RANGING_SIDEWAYS,
            current_price=4_000_000_000.0,
            target_price=0.0,
            stop_loss_price=0.0,
            estimated_win_probability=0.0,
            confidence=0.5,
            rationale="No action",
        )
        res = asyncio.run(dispatcher.dispatch_signal(sig))
        assert res.success is True
        assert res.status == ExecutionStatus.SKIPPED
        assert res.requested_quantity == 0.0

    def test_buy_signal_passes_jev_and_executes_under_50ms(self, dispatcher):
        async def _run():
            # Attractive high-expectancy BUY signal
            curr = 65_000.0
            sig = TradingSignal(
                symbol="USDTTMN",
                action=SignalAction.BUY,
                regime=MarketRegime.BULLISH_TREND,
                current_price=curr,
                target_price=curr * 1.03,  # +3% gain
                stop_loss_price=curr * 0.985,  # -1.5% loss (R:R = 2.0)
                estimated_win_probability=0.70,
                confidence=0.85,
                rationale="High JEV bullish continuation",
            )
            policy = MacroTradingPolicy(
                active_regime=MarketRegime.BULLISH_TREND,
                allowed_symbols=["USDTTMN"],
                max_allocation_per_trade_pct=0.20,
            )

            res = await dispatcher.dispatch_signal(sig, policy=policy)
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS
            assert res.latency_ms < 50.0  # Strict sub-50ms latency invariant
            assert res.executed_quantity > 0
            # Sizing strictly capped at <= 20% equity (1,000,000 TMN on 5M account)
            order_cost = res.executed_price * res.executed_quantity
            assert order_cost <= 1_000_000.0 + 1e-6
            assert dispatcher.portfolio.has_active_position("USDTTMN")

        asyncio.run(_run())

    def test_weak_signal_rejected_by_jev_hurdle(self, dispatcher):
        async def _run():
            # Negative expectancy signal (win prob 0.35, high loss, fee drag)
            curr = 65_000.0
            sig = TradingSignal(
                symbol="USDTTMN",
                action=SignalAction.BUY,
                regime=MarketRegime.RANGING_SIDEWAYS,
                current_price=curr,
                target_price=curr * 1.005,  # +0.5% gain
                stop_loss_price=curr * 0.98,  # -2.0% loss
                estimated_win_probability=0.35,
                confidence=0.40,
                rationale="Weak low-expectancy setup",
            )
            res = await dispatcher.dispatch_signal(sig)
            assert res.success is False
            assert res.status == ExecutionStatus.REJECTED_JEV
            assert "JEV hurdle rejected" in res.message

        asyncio.run(_run())

    def test_circuit_breaker_rejects_buy_orders(self, dispatcher):
        async def _run():
            # Force 10% drawdown trip on sentinel
            dispatcher.drawdown_sentinel.update_equity(
                tmn_available=4_400_000.0,  # >10% drop from 5M
                tmn_locked=0.0,
                crypto_holdings={},
                market_prices={},
            )
            assert dispatcher.drawdown_sentinel.circuit_breaker_active

            sig = TradingSignal(
                symbol="BTCTMN",
                action=SignalAction.BUY,
                regime=MarketRegime.BULLISH_TREND,
                current_price=4_000_000_000.0,
                target_price=4_200_000_000.0,
                stop_loss_price=3_950_000_000.0,
                estimated_win_probability=0.80,
                confidence=0.90,
                rationale="Should be blocked by circuit breaker",
            )
            res = await dispatcher.dispatch_signal(sig)
            assert res.success is False
            assert res.status == ExecutionStatus.REJECTED_CIRCUIT
            assert "Circuit breaker active" in res.message

        asyncio.run(_run())

    def test_deadline_liquidation_schedule_blocks_buy(self, dispatcher):
        async def _run():
            deadline = 100_000.0
            fake_clock = lambda: deadline - (3 * 3600)  # T-3h
            sentinel = DrawdownSentinel(
                deadline_timestamp=deadline,
                clock_func=fake_clock,
            )
            dispatcher.drawdown_sentinel = sentinel

            sig = TradingSignal(
                symbol="ETHTMN",
                action=SignalAction.BUY,
                regime=MarketRegime.BULLISH_TREND,
                current_price=180_000_000.0,
                target_price=190_000_000.0,
                stop_loss_price=175_000_000.0,
                estimated_win_probability=0.75,
                confidence=0.80,
                rationale="Should be blocked by deadline rule",
            )
            res = await dispatcher.dispatch_signal(sig, current_time=fake_clock())
            assert res.success is False
            assert res.status == ExecutionStatus.REJECTED_DEADLINE
            assert "liquidation" in res.message

        asyncio.run(_run())

    def test_panic_regime_dispatches_market_sell(self, dispatcher):
        async def _run():
            # Pre-load position into portfolio and mock client
            dispatcher.portfolio.crypto_balances["BTC"] = 0.001
            dispatcher.portfolio.active_positions["BTCTMN"] = PositionRiskState(
                symbol="BTCTMN",
                side="BUY",
                quantity=0.001,
                entry_price=4_200_000_000.0,
                cost_tmn=4_200_000.0,
                current_price=4_100_000_000.0,
                stop_loss_price=4_000_000_000.0,
                take_profit_price=4_300_000_000.0,
                highest_price_seen=4_200_000_000.0,
                opened_at=time.time(),
            )
            dispatcher.client.crypto_balances["BTC"] = 0.001

            # Panic sell signal
            sig = TradingSignal(
                symbol="BTCTMN",
                action=SignalAction.SELL,
                regime=MarketRegime.HIGH_VOLATILITY_PANIC,
                current_price=4_100_000_000.0,
                target_price=4_100_000_000.0,
                stop_loss_price=4_200_000_000.0,
                estimated_win_probability=0.90,
                confidence=0.95,
                rationale="Emergency liquidation",
            )
            res = await dispatcher.dispatch_signal(sig)
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS
            assert res.order_type == OrderType.MARKET  # Urgency uses MARKET
            assert not dispatcher.portfolio.has_active_position("BTCTMN")

        asyncio.run(_run())

    def test_policy_symbol_restriction_rejects_disallowed_symbol(self, dispatcher):
        async def _run():
            policy = MacroTradingPolicy(
                active_regime=MarketRegime.BULLISH_TREND,
                allowed_symbols=["BTCTMN"],  # USDTTMN is not permitted
            )
            sig = TradingSignal(
                symbol="USDTTMN",
                action=SignalAction.BUY,
                regime=MarketRegime.BULLISH_TREND,
                current_price=65_000.0,
                target_price=67_000.0,
                stop_loss_price=64_000.0,
                estimated_win_probability=0.75,
                confidence=0.85,
                rationale="Disallowed symbol test",
            )
            res = await dispatcher.dispatch_signal(sig, policy=policy)
            assert res.success is False
            assert res.status == ExecutionStatus.REJECTED_RISK
            assert "allowed_symbols" in res.message

        asyncio.run(_run())

    def test_engine_metrics_accumulation(self, dispatcher):
        async def _run():
            initial_count = dispatcher.metrics.total_orders_dispatched
            # Run one BUY order
            curr = 65_000.0
            sig = TradingSignal(
                symbol="USDTTMN",
                action=SignalAction.BUY,
                regime=MarketRegime.BULLISH_TREND,
                current_price=curr,
                target_price=curr * 1.03,
                stop_loss_price=curr * 0.985,
                estimated_win_probability=0.70,
                confidence=0.85,
                rationale="Metrics test buy",
            )
            res = await dispatcher.dispatch_signal(sig)
            assert res.success is True
            assert dispatcher.metrics.total_orders_dispatched == initial_count + 1
            assert dispatcher.metrics.total_orders_filled >= 1
            assert dispatcher.metrics.total_volume_tmn > 0
            assert dispatcher.metrics.average_latency_ms > 0

        asyncio.run(_run())
