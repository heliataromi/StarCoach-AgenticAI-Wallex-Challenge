"""Unit and integration tests for Wallex Credit Account Market Whitelisting / Blacklisting
and exact fee schedule calibration (0.25% maker / 0.30% taker for Toman markets).
"""
import pytest
import time

from src.engine.dispatcher import OrderExecutionDispatcher
from src.engine.models import ExecutionStatus
from src.engine.portfolio import PortfolioTracker
from src.risk.drawdown import DrawdownSentinel
from src.risk.jev import JEVEvaluator
from src.risk.models import RiskConfig
from src.risk.sizing import PositionSizer
from src.strategy.macro_agent import MacroAgent
from src.strategy.models import MarketRegime, SignalAction, TradingSignal
from src.wallex.client import WallexClient
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WallexOrderError,
    is_credit_market_supported,
    normalize_symbol,
    validate_credit_market_symbol,
    WALLEX_CREDIT_DISABLED_SYMBOLS,
)


class TestCreditMarketValidation:
    """Verifies symbol normalization and credit account market filtering."""

    def test_normalize_symbol(self):
        assert normalize_symbol("BTC/TMN") == "BTCTMN"
        assert normalize_symbol("btc-tmn") == "BTCTMN"
        assert normalize_symbol("eth_tmn") == "ETHTMN"
        assert normalize_symbol("  soltmn  ") == "SOLTMN"
        assert normalize_symbol("") == ""
        assert normalize_symbol(None) == ""

    def test_liquid_pairs_permitted_in_credit_facility(self):
        liquid_pairs = ["BTCTMN", "ETHTMN", "USDTTMN", "SOLTMN", "DOGETMN", "XRPTMN"]
        for symbol in liquid_pairs:
            assert is_credit_market_supported(symbol) is True, f"{symbol} should be permitted"
            # Should not raise
            validate_credit_market_symbol(symbol)

    def test_disabled_pairs_rejected_in_credit_facility(self):
        disabled_pairs = [
            "A/TMN", "ATMN", "G/TMN", "GTMN", "CAT/TMN", "CATTMN",
            "BANANA/TMN", "BANANATMN", "AERO/USDT", "AEROUSDT",
            "1BBABYDOGE/TMN", "1BBABYDOGETMN", "ETHFI/TMN", "ETHFITMN"
        ]
        for symbol in disabled_pairs:
            assert is_credit_market_supported(symbol) is False, f"{symbol} should be disabled"
            with pytest.raises(WallexOrderError) as exc_info:
                validate_credit_market_symbol(symbol)
            assert "disabled for Wallex credit facility" in str(exc_info.value)

    def test_empty_symbol_rejected(self):
        assert is_credit_market_supported("") is False
        with pytest.raises(WallexOrderError):
            validate_credit_market_symbol("")

    def test_disabled_symbols_frozenset_size(self):
        # Must contain all ~345 disabled symbols reported in the official notice
        assert len(WALLEX_CREDIT_DISABLED_SYMBOLS) >= 300
        assert "ATMN" in WALLEX_CREDIT_DISABLED_SYMBOLS
        assert "BTCTMN" not in WALLEX_CREDIT_DISABLED_SYMBOLS


class TestFeeCalibration:
    """Verifies portal fee schedule alignment (0.25% maker / 0.30% taker for TMN)."""

    def test_toman_fee_schedule(self):
        config = RiskConfig()
        maker, taker = config.get_fee_rates("BTCTMN")
        assert maker == pytest.approx(0.0025)
        assert taker == pytest.approx(0.0030)

        # Realistic round-trip: maker entry + taker exit = 0.55%
        rt_fee = config.calculate_round_trip_fee("BTCTMN", is_maker_entry=True, is_maker_exit=False)
        assert rt_fee == pytest.approx(0.0055)

        # Worst case round-trip: taker entry + taker exit = 0.60%
        rt_taker = config.calculate_round_trip_fee("BTCTMN", is_maker_entry=False, is_maker_exit=False)
        assert rt_taker == pytest.approx(0.0060)

    def test_usdt_fee_schedule(self):
        config = RiskConfig()
        maker, taker = config.get_fee_rates("BTCUSDT")
        assert maker == pytest.approx(0.0010)
        assert taker == pytest.approx(0.0013)

        rt_fee = config.calculate_round_trip_fee("BTCUSDT", is_maker_entry=True, is_maker_exit=False)
        assert rt_fee == pytest.approx(0.0023)

    def test_jev_symbol_aware_evaluation(self):
        evaluator = JEVEvaluator()
        # Evaluate for BTCTMN (0.55% fee)
        res_tmn = evaluator.evaluate(
            entry_price=100.0,
            take_profit_price=103.0,
            stop_loss_price=98.0,
            win_probability=0.60,
            symbol="BTCTMN",
        )
        assert res_tmn.round_trip_fee_pct == pytest.approx(0.0055)

        # Evaluate for BTCUSDT (0.23% fee)
        res_usdt = evaluator.evaluate(
            entry_price=100.0,
            take_profit_price=103.0,
            stop_loss_price=98.0,
            win_probability=0.60,
            symbol="BTCUSDT",
        )
        assert res_usdt.round_trip_fee_pct == pytest.approx(0.0023)
        assert res_usdt.expected_value > res_tmn.expected_value


class TestClientPreOrderValidation:
    """Verifies MockWallexClient and WallexClient reject blacklisted credit pairs."""

    @pytest.mark.anyio
    async def test_mock_client_rejects_disabled_pair(self):
        client = MockWallexClient()
        await client.connect()

        # Attempt to order blacklisted pair
        with pytest.raises(WallexOrderError) as exc_info:
            await client.post_order(
                symbol="CATTMN",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=100.0,
                quantity=10.0,
            )
        assert "disabled for Wallex credit facility" in str(exc_info.value)
        await client.close()

    @pytest.mark.anyio
    async def test_mock_client_allows_valid_pair(self):
        client = MockWallexClient()
        await client.connect()

        # Place valid order on permitted symbol (MARKET fills immediately)
        result = await client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            price=4_000_000_000.0,
            quantity=0.0001,
        )
        assert result.symbol == "BTCTMN"
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == pytest.approx(0.0001)
        await client.close()


class TestDispatcherCreditFiltering:
    """Verifies that OrderExecutionDispatcher intercepts disabled credit symbols."""

    @pytest.mark.anyio
    async def test_dispatcher_rejects_disabled_symbol_signal(self):
        client = MockWallexClient()
        portfolio = PortfolioTracker(initial_equity_tmn=5_000_000.0)
        dispatcher = OrderExecutionDispatcher(client=client, portfolio=portfolio)

        signal = TradingSignal(
            symbol="BANANATMN",  # Blacklisted in credit
            action=SignalAction.BUY,
            regime=MarketRegime.RANGING_SIDEWAYS,
            current_price=50_000.0,
            target_price=55_000.0,
            stop_loss_price=48_000.0,
            estimated_win_probability=0.80,
            confidence=0.90,
            rationale="Test disabled credit symbol",
            timestamp=time.time(),
        )

        result = await dispatcher.dispatch_signal(signal)
        assert result.status == ExecutionStatus.REJECTED_RISK
        assert "inactive for Wallex credit facility" in result.message

    @pytest.mark.anyio
    async def test_dispatcher_accepts_permitted_symbol_signal(self):
        client = MockWallexClient()
        portfolio = PortfolioTracker(initial_equity_tmn=5_000_000.0)
        dispatcher = OrderExecutionDispatcher(client=client, portfolio=portfolio)

        signal = TradingSignal(
            symbol="BTCTMN",  # Permitted
            action=SignalAction.BUY,
            regime=MarketRegime.BULLISH_TREND,
            current_price=4_000_000_000.0,
            target_price=4_150_000_000.0,
            stop_loss_price=3_950_000_000.0,
            estimated_win_probability=0.75,
            confidence=0.85,
            rationale="Test permitted credit symbol",
            timestamp=time.time(),
        )

        result = await dispatcher.dispatch_signal(signal)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.symbol == "BTCTMN"


class TestMacroAgentCreditFiltering:
    """Verifies MacroAgent auto-filters disabled symbols upon initialization."""

    def test_macro_agent_filters_allowed_symbols(self):
        agent = MacroAgent(allowed_symbols=["BTCTMN", "CATTMN", "ETHTMN", "BANANATMN", "USDTTMN"])
        assert agent.allowed_symbols == ["BTCTMN", "ETHTMN", "USDTTMN"]
        assert "CATTMN" not in agent.allowed_symbols
        assert "BANANATMN" not in agent.allowed_symbols
