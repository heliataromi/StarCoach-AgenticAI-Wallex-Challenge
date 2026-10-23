"""Unit tests for Backtesting and Shadow Testing Subsystems (Milestone 6).

Verifies historical UDF parsing, synthetic multi-regime generator reproducibility,
deterministic backtest metric calculations, mandatory 100% Iranian Toman (TMN)
deadline liquidation assertion, and shadow runner paper-trading ticks.
"""
from __future__ import annotations

import asyncio
import time
import pytest
from src.test.data_loader import (
    create_orderbook_for_price,
    generate_synthetic_market_data,
    parse_udf_candles,
    parse_wallex_depth,
)
from src.test.backtester import DeterministicBacktester, BacktestResult
from src.test.shadow_runner import ShadowRunner
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import Candle, OrderSide, OrderType


class TestHistoricalDataLoader:
    """Verifies UDF parsing and deterministic synthetic market generation."""

    def test_parse_udf_candles_valid_payload(self):
        payload = {
            "s": "ok",
            "t": [1700000000, 1700000900],
            "o": [50000.0, 50100.0],
            "h": [50200.0, 50300.0],
            "l": [49900.0, 50050.0],
            "c": [50100.0, 50250.0],
            "v": [10.5, 12.3],
        }
        candles = parse_udf_candles(payload, symbol="BTCTMN")
        assert len(candles) == 2
        assert candles[0].open == 50000.0
        assert candles[0].close == 50100.0
        assert candles[0].high == 50200.0
        assert candles[0].low == 49900.0
        assert candles[0].volume == 10.5
        assert candles[0].timestamp == 1700000000 * 1000.0

    def test_parse_udf_candles_empty_or_invalid(self):
        assert parse_udf_candles({}) == []
        assert parse_udf_candles({"s": "no_data"}) == []
        assert parse_udf_candles({"s": "error"}) == []

    def test_generate_synthetic_market_data_reproducibility(self):
        c1 = generate_synthetic_market_data(symbol="BTCTMN", num_candles=100, seed=123)
        c2 = generate_synthetic_market_data(symbol="BTCTMN", num_candles=100, seed=123)
        c3 = generate_synthetic_market_data(symbol="BTCTMN", num_candles=100, seed=999)

        assert len(c1) == 100
        assert len(c2) == 100
        # Same seed yields identical close prices
        for a, b in zip(c1, c2):
            assert a.close == b.close
            assert a.high == b.high
            assert a.low == b.low
        # Different seeds produce different prices
        diffs = sum(1 for a, b in zip(c1, c3) if a.close != b.close)
        assert diffs > 80

    def test_generate_synthetic_market_data_three_regimes(self):
        # 300 candles: 0-100 range, 100-220 trend, 220-300 panic
        candles = generate_synthetic_market_data(symbol="BTCTMN", base_price=100_000.0, num_candles=300, seed=42)
        assert len(candles) == 300

        # Phase 1: Range stays near base price within +/- 3%
        phase1_closes = [c.close for c in candles[:100]]
        for p in phase1_closes:
            assert 95_000.0 <= p <= 105_000.0

        # Phase 2: Bullish trend reaches higher peak
        phase2_closes = [c.close for c in candles[100:220]]
        assert max(phase2_closes) > max(phase1_closes)

        # High/low envelopes are valid
        for c in candles:
            assert c.high >= max(c.open, c.close)
            assert c.low <= min(c.open, c.close)

    def test_create_orderbook_for_price(self):
        ob = create_orderbook_for_price(
            symbol="USDTTMN",
            price=65_000.0,
            spread_pct=0.002,
            imbalance=0.2,
        )
        assert ob.symbol == "USDTTMN"
        assert len(ob.bids) == 5
        assert len(ob.asks) == 5
        # Bids strictly descending
        for i in range(len(ob.bids) - 1):
            assert ob.bids[i][0] > ob.bids[i + 1][0]
        # Asks strictly ascending
        for i in range(len(ob.asks) - 1):
            assert ob.asks[i][0] < ob.asks[i + 1][0]
        # Bid-ask spread strictly positive
        assert ob.asks[0][0] > ob.bids[0][0]

    def test_parse_wallex_depth_formats(self):
        # Live Wallex format
        live_payload = {
            "result": {
                "bid": [{"price": "65000", "quantity": "100"}, {"price": "64900", "quantity": "200"}],
                "ask": [{"price": "65100", "quantity": "80"}, {"price": "65200", "quantity": "150"}],
            }
        }
        ob = parse_wallex_depth(live_payload, symbol="USDTTMN")
        assert ob.symbol == "USDTTMN"
        assert len(ob.bids) == 2
        assert ob.bids[0] == (65000.0, 100.0)
        assert ob.asks[0] == (65100.0, 80.0)

        # Offline fixture format
        fixture_payload = {
            "bids": [[65000.0, 100.0], [64900.0, 200.0]],
            "asks": [[65100.0, 80.0], [65200.0, 150.0]],
        }
        ob2 = parse_wallex_depth(fixture_payload, symbol="USDTTMN")
        assert ob2.bids[0] == (65000.0, 100.0)


class TestDeterministicBacktester:
    """Verifies backtest execution, metrics calculation, and mandatory 100% Toman liquidation."""

    def test_backtest_run_and_mandatory_liquidation(self, tmp_path):
        async def _test():
            market_data = {
                "BTCTMN": generate_synthetic_market_data("BTCTMN", 4_200_000_000.0, num_candles=80, seed=10),
                "USDTTMN": generate_synthetic_market_data("USDTTMN", 65_000.0, num_candles=80, seed=20),
            }

            bt = DeterministicBacktester(
                initial_equity_tmn=5_000_000.0,
                fee_rate=0.0025,
                log_dir=str(tmp_path / "logs"),
            )
            result = await bt.run(market_data, challenge_deadline_step=75)

            assert isinstance(result, BacktestResult)
            assert result.initial_equity_tmn == 5_000_000.0
            assert result.final_equity_tmn > 0.0
            # Drawdown must not breach the 10% hard stop
            assert result.max_drawdown_pct < 10.0
            # Mandatory 100% Toman liquidation invariant
            assert result.liquidated_to_toman is True
            assert len(result.final_crypto_balances) == 0
            assert result.final_toman_balance == pytest.approx(result.final_equity_tmn, abs=1e-2)
            # Verify cryptographic audit validity and episodic reflection summary
            assert result.audit_integrity_valid is True
            assert isinstance(result.reflection_summary, str)

        asyncio.run(_test())

    def test_backtest_insufficient_history_raises(self):
        async def _test():
            short_data = {
                "BTCTMN": generate_synthetic_market_data("BTCTMN", 1000.0, num_candles=15),
            }
            bt = DeterministicBacktester()
            with pytest.raises(ValueError, match="Insufficient candle history"):
                await bt.run(short_data)

        asyncio.run(_test())

    def test_backtest_empty_symbols_raises(self):
        async def _test():
            bt = DeterministicBacktester()
            with pytest.raises(ValueError, match="market_candles cannot be empty"):
                await bt.run({})

        asyncio.run(_test())


class TestShadowRunner:
    """Verifies live paper-trading runner ticks and virtual execution."""

    def test_shadow_runner_single_tick(self, tmp_path):
        async def _test():
            mock_live = MockWallexClient(initial_tmn=10_000_000.0)
            runner = ShadowRunner(
                live_client=mock_live,
                symbols=["BTCTMN", "USDTTMN"],
                initial_equity_tmn=5_000_000.0,
                log_dir=str(tmp_path / "shadow_logs"),
            )

            cycle = await runner.tick_once()
            assert cycle["iteration"] == 1
            assert "active_regime" in cycle
            assert "virtual_equity_tmn" in cycle
            assert "debates" in cycle
            assert "memory_summary" in cycle
            assert cycle["virtual_equity_tmn"] == pytest.approx(5_000_000.0, rel=0.02)

        asyncio.run(_test())

    def test_shadow_runner_run_iterations(self, tmp_path):
        async def _test():
            mock_live = MockWallexClient(initial_tmn=10_000_000.0)
            runner = ShadowRunner(
                live_client=mock_live,
                symbols=["USDTTMN"],
                initial_equity_tmn=5_000_000.0,
                poll_interval_seconds=0.01,
                log_dir=str(tmp_path / "shadow_logs"),
            )

            summary = await runner.run_iterations(max_iterations=3)
            assert summary["iterations_executed"] == 3
            assert summary["virtual_equity_tmn"] > 0.0

        asyncio.run(_test())
