"""Unit tests for Strategy subsystem: Indicators, Latent Regime Classifier, Signal Generator, and Macro Agent.

Tests edge cases, degenerate series, mathematical invariants, and multi-regime signal rules.
"""
from __future__ import annotations

import time
import pytest
from src.strategy.models import MarketRegime, SignalAction, TradingSignal, MacroTradingPolicy
from src.strategy.indicators import (
    calculate_sma,
    calculate_ema,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_order_book_imbalance,
)
from src.strategy.regimes import LatentRegimeClassifier
from src.strategy.signals import MultiRegimeSignalGenerator
from src.strategy.macro_agent import MacroAgent
from src.wallex.models import Candle, DepthLevel, OrderBook


def make_candle(close: float, high: float = 0.0, low: float = 0.0, volume: float = 1.0, timestamp: float = 0.0) -> Candle:
    h = high if high > 0 else close * 1.002
    l = low if low > 0 else close * 0.998
    return Candle(
        timestamp=timestamp or time.time(),
        open=close,
        high=h,
        low=l,
        close=close,
        volume=volume,
    )


class TestIndicators:
    """Tests technical indicator accuracy and mathematical edge cases."""

    def test_sma_basic_and_boundaries(self):
        assert calculate_sma([], 5) == 0.0
        assert calculate_sma([10.0, 20.0, 30.0], 0) == 0.0
        assert calculate_sma([10.0, 20.0, 30.0], 2) == 25.0
        assert calculate_sma([10.0, 20.0, 30.0], 5) == 20.0

    def test_ema_series(self):
        assert calculate_ema([], 9) == []
        assert calculate_ema([100.0] * 10, 5) == [100.0] * 10

        # Monotonically increasing sequence
        prices = [float(i) for i in range(1, 21)]
        ema = calculate_ema(prices, 5)
        assert len(ema) == 20
        # EMA must be monotonically increasing and lag behind price in strong uptrend
        for i in range(1, len(ema)):
            assert ema[i] > ema[i - 1]
            assert ema[i] <= prices[i]

    def test_rsi_extremes_and_neutral(self):
        # Empty or insufficient data returns 50.0 neutral
        assert calculate_rsi([], 14) == 50.0
        assert calculate_rsi([100.0, 101.0], 14) == 50.0

        # All gains -> RSI 100.0
        gains = [float(100 + i * 2) for i in range(20)]
        assert calculate_rsi(gains, 14) == 100.0

        # All losses -> RSI 0.0
        losses = [float(200 - i * 2) for i in range(20)]
        assert calculate_rsi(losses, 14) == 0.0

        # Balanced oscillations
        flat = [100.0, 105.0, 100.0, 105.0] * 5
        rsi = calculate_rsi(flat, 14)
        assert 45.0 <= rsi <= 55.0

    def test_bollinger_bands(self):
        assert calculate_bollinger_bands([], 20) == (0.0, 0.0, 0.0)
        # Flat prices -> lower == mid == upper
        flat = [50.0] * 20
        lower, mid, upper = calculate_bollinger_bands(flat, 20)
        assert lower == 50.0
        assert mid == 50.0
        assert upper == 50.0

        # Regular series with standard deviation
        data = [10.0, 20.0, 10.0, 20.0, 10.0, 20.0]
        lower, mid, upper = calculate_bollinger_bands(data, 6, num_std=2.0)
        assert mid == 15.0
        assert lower < mid < upper

    def test_atr_volatility(self):
        assert calculate_atr([], [], [], 14) == 0.0
        highs = [105.0, 110.0, 115.0]
        lows = [95.0, 100.0, 105.0]
        closes = [100.0, 105.0, 110.0]
        atr = calculate_atr(highs, lows, closes, period=2)
        assert atr == 10.0

    def test_order_book_imbalance(self):
        bids = [DepthLevel(price=100.0, quantity=10.0)]
        asks = [DepthLevel(price=101.0, quantity=0.0)]
        assert calculate_order_book_imbalance(bids, asks) == 1.0

        asks2 = [DepthLevel(price=101.0, quantity=10.0)]
        assert calculate_order_book_imbalance(bids, asks2) == 0.0

        bids_empty = []
        assert calculate_order_book_imbalance(bids_empty, asks2) == -1.0


class TestLatentRegimeClassifier:
    """Verifies classification into BULLISH, RANGING, and PANIC regimes."""

    def test_insufficient_data_defaults_safely(self):
        classifier = LatentRegimeClassifier()
        regime, conf, rationale = classifier.classify([])
        assert regime == MarketRegime.RANGING_SIDEWAYS
        assert conf == 0.5

    def test_panic_classification_on_sharp_drop(self):
        classifier = LatentRegimeClassifier(panic_drop_threshold_pct=0.03)
        # 10 candles: steady at 100M then drops sharply to 94M (>3% drop)
        candles = [make_candle(100_000_000.0) for _ in range(8)]
        candles.append(make_candle(97_000_000.0))
        candles.append(make_candle(94_000_000.0))

        regime, conf, rationale = classifier.classify(candles)
        assert regime == MarketRegime.HIGH_VOLATILITY_PANIC
        assert conf >= 0.70
        assert "sharp drop" in rationale

    def test_panic_classification_on_bid_depletion(self):
        classifier = LatentRegimeClassifier()
        candles = [make_candle(100_000_000.0) for _ in range(15)]
        # Asks heavily outweigh bids (severe sell wall and spread > 0.5%)
        ob = OrderBook(
            symbol="BTCTMN",
            timestamp=time.time() * 1000.0,
            bids=[(98_000_000.0, 0.1)],
            asks=[(100_000_000.0, 10.0)],
        )
        regime, conf, rationale = classifier.classify(candles, orderbook=ob)
        assert regime == MarketRegime.HIGH_VOLATILITY_PANIC
        assert "bid depletion" in rationale

    def test_bullish_trend_classification(self):
        classifier = LatentRegimeClassifier()
        # Steady uptrend over 30 candles
        base = 50_000_000.0
        candles = [make_candle(base + (i * 500_000.0)) for i in range(30)]
        ob = OrderBook(
            symbol="BTCTMN",
            timestamp=time.time() * 1000.0,
            bids=[(base + 15_000_000.0, 5.0)],
            asks=[(base + 15_100_000.0, 2.0)],
        )
        regime, conf, rationale = classifier.classify(candles, orderbook=ob)
        assert regime == MarketRegime.BULLISH_TREND
        assert conf >= 0.65
        assert "Bullish trend confirmed" in rationale

    def test_ranging_sideways_classification(self):
        classifier = LatentRegimeClassifier()
        # Flat prices fluctuating within tight 0.5% band
        base = 50_000_000.0
        candles = [
            make_candle(base + (200_000.0 if i % 2 == 0 else -200_000.0))
            for i in range(30)
        ]
        regime, conf, rationale = classifier.classify(candles)
        assert regime == MarketRegime.RANGING_SIDEWAYS
        assert "Range-bound consolidation" in rationale


class TestMultiRegimeSignalGenerator:
    """Verifies tactical signal production across all 3 regimes."""

    def test_panic_regime_forces_cash_preservation_and_liquidation(self):
        generator = MultiRegimeSignalGenerator()
        candles = [make_candle(50_000_000.0) for _ in range(10)]

        # When flat: MUST HOLD 100% cash
        sig_flat = generator.generate_signal(
            symbol="BTCTMN",
            regime=MarketRegime.HIGH_VOLATILITY_PANIC,
            candles=candles,
            has_open_position=False,
        )
        assert sig_flat.action == SignalAction.HOLD
        assert "cash preservation" in sig_flat.rationale

        # When holding position: MUST SELL/LIQUIDATE
        sig_pos = generator.generate_signal(
            symbol="BTCTMN",
            regime=MarketRegime.HIGH_VOLATILITY_PANIC,
            candles=candles,
            has_open_position=True,
            entry_price=52_000_000.0,
        )
        assert sig_pos.action == SignalAction.SELL
        assert "liquidate open position" in sig_pos.rationale

    def test_bullish_trend_buy_and_trailing_sell(self):
        generator = MultiRegimeSignalGenerator()
        # Realistic uptrend series with natural pullbacks
        prices = [
            100_000.0, 101_000.0, 100_500.0, 102_000.0, 101_800.0,
            103_000.0, 102_500.0, 104_000.0, 103_500.0, 105_000.0,
            104_800.0, 106_000.0, 105_500.0, 107_000.0, 106_800.0,
            108_000.0, 107_500.0, 109_000.0, 108_500.0, 110_000.0,
            109_800.0, 111_000.0, 110_500.0, 112_000.0, 113_000.0,
        ]
        candles = [make_candle(p) for p in prices]
        curr = prices[-1]
        ob = OrderBook(
            symbol="USDTTMN",
            timestamp=time.time() * 1000.0,
            bids=[(curr, 500.0)],
            asks=[(curr + 50.0, 100.0)],
        )

        # Flat state generates BUY signal
        buy_sig = generator.generate_signal(
            symbol="USDTTMN",
            regime=MarketRegime.BULLISH_TREND,
            candles=candles,
            orderbook=ob,
            has_open_position=False,
        )
        assert buy_sig.action == SignalAction.BUY
        assert buy_sig.target_price > buy_sig.current_price
        assert buy_sig.stop_loss_price < buy_sig.current_price
        assert buy_sig.reward_to_risk >= 1.5

        # In-position state when trailing stop or profit reached generates SELL
        sell_sig = generator.generate_signal(
            symbol="USDTTMN",
            regime=MarketRegime.BULLISH_TREND,
            candles=candles,
            orderbook=ob,
            has_open_position=True,
            entry_price=curr + 10_000.0,  # price currently well below entry -> trailing stop breach
        )
        assert sell_sig.action == SignalAction.SELL

    def test_ranging_sideways_bollinger_scalping(self):
        generator = MultiRegimeSignalGenerator()
        # Create a series with variance then a sharp dip to lower band
        prices = [100.0] * 15 + [105.0, 100.0, 95.0, 93.0, 90.0]
        candles = [make_candle(p) for p in prices]
        ob = OrderBook(
            symbol="ETHTMN",
            timestamp=time.time() * 1000.0,
            bids=[(90.0, 10.0)],  # strong bid wall
            asks=[(91.0, 1.0)],
        )

        buy_sig = generator.generate_signal(
            symbol="ETHTMN",
            regime=MarketRegime.RANGING_SIDEWAYS,
            candles=candles,
            orderbook=ob,
            has_open_position=False,
        )
        assert buy_sig.action == SignalAction.BUY
        assert "Bollinger oversold bounce" in buy_sig.rationale


class TestMacroAgent:
    """Verifies strategic portfolio policy generation and panic contagion."""

    def test_macro_agent_panic_contagion(self):
        agent = MacroAgent(allowed_symbols=["BTCTMN", "ETHTMN"])
        # BTC is in panic drop, ETH is normal
        btc_candles = [make_candle(100.0) for _ in range(8)] + [make_candle(90.0), make_candle(85.0)]
        eth_candles = [make_candle(10.0) for _ in range(15)]

        policy = agent.evaluate_policy(
            market_candles={"BTCTMN": btc_candles, "ETHTMN": eth_candles}
        )
        assert policy.active_regime == MarketRegime.HIGH_VOLATILITY_PANIC
        assert policy.max_allocation_per_trade_pct == 0.0
        assert policy.allowed_symbols == []
        assert "Panic detected" in policy.reasoning_summary

    def test_macro_agent_bullish_policy(self):
        agent = MacroAgent(allowed_symbols=["USDTTMN"])
        candles = [make_candle(50_000.0 + (i * 500.0)) for i in range(30)]
        ob = OrderBook(
            symbol="USDTTMN",
            timestamp=time.time() * 1000.0,
            bids=[(65_000.0, 100.0)],
            asks=[(65_100.0, 20.0)],
        )

        policy = agent.evaluate_policy(
            market_candles={"USDTTMN": candles},
            market_orderbooks={"USDTTMN": ob},
        )
        assert policy.active_regime == MarketRegime.BULLISH_TREND
        assert policy.max_allocation_per_trade_pct == 0.20
        assert "USDTTMN" in policy.allowed_symbols

    def test_macro_policy_ttl_and_refresh(self):
        agent = MacroAgent(default_policy_ttl_seconds=60.0)
        candles = [make_candle(100.0) for _ in range(15)]
        
        t0 = 1000.0
        p1 = agent.get_or_refresh_policy({"BTCTMN": candles}, current_time=t0)
        assert not p1.is_expired(t0 + 30.0)
        assert p1.is_expired(t0 + 61.0)

        # Refresh returns new policy when expired
        p2 = agent.get_or_refresh_policy({"BTCTMN": candles}, current_time=t0 + 70.0)
        assert p2.regime_timestamp == t0 + 70.0
