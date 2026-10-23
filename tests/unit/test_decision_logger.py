"""Unit tests for Structured Decision Auditor and cryptographic tamper-evident logging.

Verifies SHA-256 digest validation, JSON/JSONL persistence, query filters,
and tamper detection.
"""
from __future__ import annotations

import json
import time
import pytest
from src.engine.models import ExecutionStatus, OrderExecutionResult
from src.logger.decision_logger import DecisionAuditor
from src.logger.models import DecisionRecord
from src.strategy.models import MacroTradingPolicy, MarketRegime, SignalAction, TradingSignal
from src.wallex.models import OrderSide, OrderType


@pytest.fixture
def temp_log_dir(tmp_path):
    log_dir = tmp_path / "decision_logs"
    log_dir.mkdir()
    return str(log_dir)


@pytest.fixture
def auditor(temp_log_dir):
    return DecisionAuditor(log_dir=temp_log_dir, auto_flush=True)


@pytest.fixture
def sample_signal():
    return TradingSignal(
        symbol="USDTTMN",
        action=SignalAction.BUY,
        regime=MarketRegime.BULLISH_TREND,
        current_price=65_000.0,
        target_price=67_000.0,
        stop_loss_price=64_000.0,
        estimated_win_probability=0.72,
        confidence=0.85,
        rationale="Bullish EMA momentum breakout",
        indicators={"ema9": 65_200.0, "ema21": 64_800.0, "rsi": 62.0, "obi": 0.25},
    )


@pytest.fixture
def sample_execution():
    return OrderExecutionResult(
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
        client_order_id="cl-USDTTMN-01",
        exchange_order_id="ex-998877",
        latency_ms=12.4,
        message="Order executed in 12.40ms",
    )


class TestDecisionAuditor:
    """Tests cryptographic integrity, persistence, and audit queries."""

    def test_record_hashing_and_tamper_detection(self, sample_signal, sample_execution):
        record = DecisionRecord(
            record_id="aud-test-01",
            timestamp_iso="2026-10-23T00:00:00Z",
            timestamp_epoch=1792700000.0,
            symbol="USDTTMN",
            market_regime="BULLISH_TREND",
            action_proposed="BUY",
            execution_outcome="SUCCESS",
            price=65_000.0,
            quantity=10.0,
            cost_tmn=650_000.0,
            fee_tmn=975.0,
            latency_ms=12.4,
            jev_metrics={"expected_value_net_pct": 0.015, "is_approved": True},
            risk_metrics={"portfolio_equity_tmn": 5_000_000.0, "allocation_pct": 0.13},
            market_indicators={"rsi": 62.0},
            agentic_reasoning={"tactical_rationale": "Breakout"},
        )
        # Unsigned record has no hash
        assert not record.verify_integrity()

        # Signed record must verify cleanly
        signed = record.with_hash()
        assert len(signed.hash_sha256) == 64
        assert signed.verify_integrity() is True

        # Tampering with any field invalidates the cryptographic digest
        tampered_data = dict(signed.__dict__)
        tampered_data["price"] = 65_001.0  # modified price
        tampered_record = DecisionRecord(**tampered_data)
        assert tampered_record.verify_integrity() is False

    def test_persistence_json_and_jsonl(self, auditor, sample_signal, sample_execution):
        policy = MacroTradingPolicy(
            active_regime=MarketRegime.BULLISH_TREND,
            allowed_symbols=["USDTTMN"],
            reasoning_summary="Uptrend confirmed",
        )
        rec = auditor.record_decision(
            signal=sample_signal,
            execution_result=sample_execution,
            policy=policy,
            jev_metrics={"expected_value_net_pct": 0.012, "is_approved": True},
            portfolio_equity_tmn=5_000_000.0,
        )
        assert rec.verify_integrity() is True

        # Verify JSON file exists on disk and is readable
        json_files = list(auditor.log_dir.glob("audit_*.json"))
        assert len(json_files) == 1
        with open(json_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["record_id"] == rec.record_id
        assert data["hash_sha256"] == rec.hash_sha256

        # Verify daily JSONL stream file exists and contains valid JSON
        jsonl_files = list(auditor.log_dir.glob("audit_stream_*.jsonl"))
        assert len(jsonl_files) == 1
        with open(jsonl_files[0], "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
        assert len(lines) == 1
        assert lines[0]["record_id"] == rec.record_id

    def test_querying_and_filtering(self, auditor, sample_signal, sample_execution):
        # 1. Record BUY success
        auditor.record_decision(sample_signal, sample_execution)

        # 2. Record REJECTED_JEV signal
        sig_weak = TradingSignal(
            symbol="BTCTMN",
            action=SignalAction.BUY,
            regime=MarketRegime.RANGING_SIDEWAYS,
            current_price=4_000_000_000.0,
            target_price=4_010_000_000.0,
            stop_loss_price=3_900_000_000.0,
            estimated_win_probability=0.35,
            confidence=0.40,
            rationale="Sub-hurdle setup",
        )
        exec_weak = OrderExecutionResult(
            success=False,
            status=ExecutionStatus.REJECTED_JEV,
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            requested_price=4_000_000_000.0,
            requested_quantity=0.0,
            message="JEV hurdle rejected",
        )
        auditor.record_decision(sig_weak, exec_weak)

        # Test Queries
        assert len(auditor.query_records(symbol="USDTTMN")) == 1
        assert len(auditor.query_records(symbol="BTCTMN")) == 1
        assert len(auditor.query_records(outcome="REJECTED_JEV")) == 1
        assert len(auditor.query_records(outcome="SUCCESS")) == 1
        assert len(auditor.query_records(symbol="NONEXISTENT")) == 0

    def test_verify_all_records_and_detect_tampering(self, auditor, sample_signal, sample_execution):
        rec1 = auditor.record_decision(sample_signal, sample_execution)
        is_valid, count, tampered = auditor.verify_all_records()
        assert is_valid is True
        assert count == 1
        assert len(tampered) == 0

        # Now simulate malicious alteration on disk
        json_file = next(auditor.log_dir.glob("audit_*.json"))
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["cost_tmn"] = 999_999_999.0  # illicit tampering
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # Re-verify directory: must flag tampered file
        is_valid_after, count_after, tampered_after = auditor.verify_all_records()
        assert is_valid_after is False
        assert len(tampered_after) == 1

    def test_summary_stats(self, auditor, sample_signal, sample_execution):
        auditor.record_decision(sample_signal, sample_execution)
        stats = auditor.get_summary_stats()
        assert stats["total_decisions"] == 1
        assert stats["approved_trades"] == 1
        assert stats["total_volume_tmn"] == 650_000.0
        assert stats["total_fees_paid_tmn"] == 975.0
        assert stats["approval_rate_pct"] == 100.0

    def test_debate_transcript_and_reflection_audit_logging(self, auditor, sample_signal, sample_execution):
        from src.strategy.debate import BullThesis, BearThesis, DebateTranscript

        bull = BullThesis(
            symbol="USDTTMN",
            bull_score=0.75,
            momentum_catalyst="EMA golden cross with 0.25 orderbook depth imbalance",
            support_level=64_500.0,
            target_price=67_500.0,
            key_factors=["EMA9 > EMA21", "Orderbook bid wall support"],
            rationale="Strong buyer absorption at support",
        )
        bear = BearThesis(
            symbol="USDTTMN",
            bear_score=0.30,
            risk_factor="Overhead resistance at 67,000",
            resistance_level=67_000.0,
            invalidation_price=64_000.0,
            key_risks=["Upper Bollinger Band test"],
            rationale="Manageable risk with protective stop",
        )
        transcript = DebateTranscript(
            symbol="USDTTMN",
            timestamp=1792700000.0,
            bull_thesis=bull,
            bear_thesis=bear,
            arbiter_verdict="APPROVED_BULLISH_TREND",
            recommended_regime=MarketRegime.BULLISH_TREND,
            confidence=0.82,
            max_allocation_pct=0.20,
            summary_reasoning="Bull thesis demonstrates superior risk-adjusted expectancy.",
        )

        rec = auditor.record_decision(
            signal=sample_signal,
            execution_result=sample_execution,
            debate_transcript=transcript,
            memory_summary="Episodic memory: 5 completed trades | Win Rate: 80.0%",
            recent_lessons=["Breakout validated on USDTTMN (+2.4%)"],
        )

        assert rec.verify_integrity() is True
        assert "debate_transcript" in rec.agentic_reasoning
        dt = rec.agentic_reasoning["debate_transcript"]
        assert dt["symbol"] == "USDTTMN"
        assert dt["bull_thesis"]["bull_score"] == 0.75
        assert dt["bear_thesis"]["bear_score"] == 0.30
        assert dt["arbiter_verdict"] == "APPROVED_BULLISH_TREND"
        assert rec.agentic_reasoning["episodic_memory_summary"] == "Episodic memory: 5 completed trades | Win Rate: 80.0%"
        assert len(rec.agentic_reasoning["recent_lessons"]) == 1

        # Check saved JSON on disk
        json_file = next(auditor.log_dir.glob("audit_*.json"))
        with open(json_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert disk_data["agentic_reasoning"]["debate_transcript"]["arbiter_verdict"] == "APPROVED_BULLISH_TREND"
