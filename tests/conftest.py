"""Shared test fixtures for the Wallex Algorithmic Trading Agent test suite.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

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

@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(start_timestamp=1700000000.0)


@pytest.fixture
def mock_wallex_client(fake_clock: FakeClock) -> MockWallexClient:
    return MockWallexClient(clock=fake_clock)


@pytest.fixture
def decision_auditor(tmp_path: Path) -> MockDecisionAuditor:
    log_dir = tmp_path / "decision_logs"
    return MockDecisionAuditor(log_dir=log_dir)


@pytest.fixture
def sample_bullish_orderbook() -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    bids = [(4_250_000_000.0, 0.20), (4_245_000_000.0, 0.30)]  # Total 0.50
    asks = [(4_255_000_000.0, 0.10), (4_260_000_000.0, 0.10)]  # Total 0.20
    return bids, asks


@pytest.fixture
def sample_bearish_orderbook() -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    bids = [(4_250_000_000.0, 0.10), (4_245_000_000.0, 0.10)]  # Total 0.20
    asks = [(4_255_000_000.0, 0.20), (4_260_000_000.0, 0.30)]  # Total 0.50
    return bids, asks
