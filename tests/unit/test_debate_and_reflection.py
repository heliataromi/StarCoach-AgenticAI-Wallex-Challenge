"""Unit tests for Multi-Agent Dialectical Debate and Episodic Memory Reflection.

Verifies BullResearcher, BearResearcher, RiskArbiter adjudication,
EpisodicMemoryBuffer post-mortems, and MacroAgent integration.
"""
from __future__ import annotations

import time
import pytest
from src.strategy.debate import (
    BearResearcher,
    BearThesis,
    BullResearcher,
    BullThesis,
    DebateTranscript,
    RiskArbiter,
)
from src.strategy.macro_agent import MacroAgent
from src.strategy.models import MarketRegime
from src.strategy.reflection import EpisodicMemoryBuffer, TradePostMortem
from src.wallex.models import Candle, OrderBook


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


class TestBullResearcher:
    """Verifies bullish thesis formulation, catalyst discovery, and memory reinforcement."""

    def test_insufficient_data_returns_minimal_conviction(self):
        researcher = BullResearcher()
        thesis = researcher.evaluate("BTCTMN", [])
        assert thesis.bull_score <= 0.15
        assert "Insufficient" in thesis.rationale

    def test_strong_uptrend_produces_high_bull_conviction(self):
        researcher = BullResearcher()
        # Series with steady upward momentum
        prices = [50000.0 + (i * 300.0) for i in range(25)]
        candles = [make_candle(p) for p in prices]
        ob = OrderBook(
            symbol="BTCTMN",
            timestamp=time.time() * 1000.0,
            bids=[(57500.0, 50.0)],
            asks=[(57600.0, 10.0)],  # Strong positive OBI
        )

        thesis = researcher.evaluate("BTCTMN", candles, ob)
        assert thesis.bull_score >= 0.60
        assert thesis.target_price > prices[-1]
        assert thesis.support_level < prices[-1]
        assert any("EMA9" in f for f in thesis.key_factors)

    def test_memory_reinforcement_boosts_conviction(self):
        researcher = BullResearcher()
        prices = [100.0 + i for i in range(20)]
        candles = [make_candle(p) for p in prices]

        thesis_raw = researcher.evaluate("USDTTMN", candles)
        thesis_with_memory = researcher.evaluate(
            "USDTTMN",
            candles,
            memory_insights=["Past breakout on USDTTMN was highly profitable"],
        )
        assert thesis_with_memory.bull_score >= thesis_raw.bull_score


class TestBearResearcher:
    """Verifies bearish downside risk identification, resistance mapping, and volatility alerts."""

    def test_insufficient_data_returns_minimal_conviction(self):
        researcher = BearResearcher()
        thesis = researcher.evaluate("ETHTMN", [])
        assert thesis.bear_score <= 0.15

    def test_breakdown_and_sell_wall_produces_high_bear_score(self):
        researcher = BearResearcher()
        # Series with sharp downward drop
        prices = [100.0] * 15 + [98.0, 95.0, 90.0, 85.0, 80.0]
        candles = [make_candle(p) for p in prices]
        ob = OrderBook(
            symbol="ETHTMN",
            timestamp=time.time() * 1000.0,
            bids=[(80.0, 5.0)],
            asks=[(81.0, 80.0)],  # Strong sell wall
        )

        thesis = researcher.evaluate("ETHTMN", candles, ob)
        assert thesis.bear_score >= 0.50
        assert thesis.resistance_level > prices[-1]

    def test_memory_risk_alert_increases_bear_score(self):
        researcher = BearResearcher()
        prices = [100.0] * 20
        candles = [make_candle(p) for p in prices]

        thesis_raw = researcher.evaluate("BTCTMN", candles)
        thesis_alert = researcher.evaluate(
            "BTCTMN",
            candles,
            memory_insights=["Recent false breakout resulted in being stopped out"],
        )
        assert thesis_alert.bear_score >= thesis_raw.bear_score


class TestRiskArbiter:
    """Verifies adjudication between Bull and Bear theses."""

    def test_high_risk_triggers_defensive_halt(self):
        arbiter = RiskArbiter()
        bull = BullThesis("BTCTMN", 0.40, "Weak bounce", 4000.0, 4500.0, [], "Rationale")
        bear = BearThesis("BTCTMN", 0.75, "Severe volatility expansion", 4600.0, 3900.0, [], "Rationale")

        transcript = arbiter.adjudicate("BTCTMN", bull, bear)
        assert transcript.recommended_regime == MarketRegime.HIGH_VOLATILITY_PANIC
        assert transcript.max_allocation_pct == 0.0
        assert transcript.arbiter_verdict == "DEFENSIVE_HALT"

    def test_clear_bull_superiority_approves_trend(self):
        arbiter = RiskArbiter()
        bull = BullThesis("BTCTMN", 0.80, "Golden alignment", 4200.0, 4800.0, ["EMA golden"], "Strong thesis")
        bear = BearThesis("BTCTMN", 0.25, "Minor resistance", 4700.0, 4100.0, [], "Benign risks")

        transcript = arbiter.adjudicate("BTCTMN", bull, bear)
        assert transcript.recommended_regime == MarketRegime.BULLISH_TREND
        assert transcript.max_allocation_pct == 0.20
        assert transcript.arbiter_verdict == "APPROVE_BULLISH_TREND"

    def test_balanced_debate_approves_range_scalping(self):
        arbiter = RiskArbiter()
        bull = BullThesis("USDTTMN", 0.45, "Lower band bounce", 64000.0, 66000.0, [], "Range bounce")
        bear = BearThesis("USDTTMN", 0.40, "Mid-band resistance", 65500.0, 63800.0, [], "Range ceiling")

        transcript = arbiter.adjudicate("USDTTMN", bull, bear)
        assert transcript.recommended_regime == MarketRegime.RANGING_SIDEWAYS
        assert transcript.max_allocation_pct == 0.15
        assert transcript.arbiter_verdict == "APPROVE_RANGE_SCALPING"

    def test_llm_arbiter_hook_override(self):
        def custom_hook(ctx):
            return {
                "verdict": "CUSTOM_OVERRIDE",
                "recommended_regime": "BULLISH_TREND",
                "confidence": 0.88,
                "max_allocation_pct": 0.18,
                "summary_reasoning": "Overridden by custom reasoning engine",
            }

        arbiter = RiskArbiter(llm_arbiter_hook=custom_hook)
        bull = BullThesis("BTCTMN", 0.50, "", 0.0, 0.0, [], "")
        bear = BearThesis("BTCTMN", 0.50, "", 0.0, 0.0, [], "")

        transcript = arbiter.adjudicate("BTCTMN", bull, bear)
        assert transcript.arbiter_verdict == "CUSTOM_OVERRIDE"
        assert transcript.recommended_regime == MarketRegime.BULLISH_TREND
        assert transcript.max_allocation_pct == 0.18


class TestEpisodicMemoryBuffer:
    """Verifies trade post-mortems and reflective memory buffer."""

    def test_record_winning_and_losing_trades(self):
        buffer = EpisodicMemoryBuffer(max_records=10)

        # 1. Big winning trade
        pm1 = buffer.record_completed_trade(
            symbol="BTCTMN",
            entry_price=4_000_000_000.0,
            exit_price=4_120_000_000.0,
            quantity=0.00025,
            pnl_tmn=30_000.0,
            fee_tmn=2500.0,
            exit_reason="Take profit target reached",
        )
        assert pm1.is_win is True
        assert pm1.pnl_pct > 2.0
        assert "breakout momentum captured" in pm1.lesson_learned.lower()

        # 2. Contained small loss
        pm2 = buffer.record_completed_trade(
            symbol="ETHTMN",
            entry_price=180_000_000.0,
            exit_price=178_500_000.0,
            quantity=0.005,
            pnl_tmn=-7500.0,
            fee_tmn=2250.0,
            exit_reason="Trailing stop triggered",
        )
        assert pm2.is_win is False
        assert "trailing stop prevented deeper capital drawdown" in pm2.lesson_learned.lower()

        assert len(buffer.history) == 2
        assert buffer.get_recent_win_rate() == 50.0

    def test_memory_buffer_capacity_fifo(self):
        buffer = EpisodicMemoryBuffer(max_records=3)
        for i in range(5):
            buffer.record_completed_trade(
                symbol="USDTTMN",
                entry_price=65_000.0,
                exit_price=66_000.0,
                quantity=10.0,
                pnl_tmn=10_000.0 * (i + 1),
                fee_tmn=100.0,
            )
        assert len(buffer.history) == 3
        # Oldest trades dropped
        assert buffer.history[-1].pnl_tmn == 50_000.0

    def test_reflection_summary_and_symbol_filter(self):
        buffer = EpisodicMemoryBuffer()
        buffer.record_completed_trade("BTCTMN", 100.0, 105.0, 1.0, 5.0, 0.5)
        buffer.record_completed_trade("ETHTMN", 10.0, 9.5, 1.0, -0.5, 0.1)

        summary = buffer.generate_reflection_summary()
        assert "2 completed trades" in summary
        assert "Win Rate: 50.0%" in summary

        btc_lessons = buffer.get_recent_lessons("BTCTMN")
        assert len(btc_lessons) == 1
        assert "BTCTMN" in btc_lessons[0]


class TestMacroAgentDebateIntegration:
    """Verifies MacroAgent orchestration of the Bull vs. Bear debate."""

    def test_macro_agent_stores_debate_transcripts(self):
        agent = MacroAgent(allowed_symbols=["BTCTMN", "USDTTMN"], enable_debate=True)
        btc_candles = [make_candle(50000.0 + i * 200) for i in range(25)]
        usdt_candles = [make_candle(65000.0) for i in range(25)]

        policy = agent.evaluate_policy({
            "BTCTMN": btc_candles,
            "USDTTMN": usdt_candles,
        })

        assert policy is not None
        transcript_btc = agent.get_debate_transcript("BTCTMN")
        assert transcript_btc is not None
        assert transcript_btc.bull_thesis.bull_score > 0.0
        assert transcript_btc.bear_thesis.bear_score > 0.0

    def test_macro_agent_learns_from_trade_outcome(self):
        agent = MacroAgent(allowed_symbols=["BTCTMN"])
        # Record a past loss in memory
        agent.record_trade_outcome(
            symbol="BTCTMN",
            entry_price=50000.0,
            exit_price=49000.0,
            quantity=0.01,
            pnl_tmn=-10000.0,
            fee_tmn=250.0,
            exit_reason="False breakout stopped out",
        )

        assert len(agent.memory.history) == 1
        candles = [make_candle(50000.0) for _ in range(20)]
        policy = agent.evaluate_policy({"BTCTMN": candles})

        # Bear thesis should incorporate memory alert
        debate = agent.get_debate_transcript("BTCTMN")
        assert debate is not None
        assert any("Memory risk alert" in r for r in debate.bear_thesis.key_risks)
