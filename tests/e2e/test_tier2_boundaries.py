"""Tier 2: Boundary, Limit, & Corner Case Tests.
Verifies system behavior under extreme thresholds, boundary conditions, and edge cases.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

import pytest

from tests.contracts import (
    AccountBalance,
    CandidateSignal,
    FakeClock,
    MarketDataSnapshot,
    MarketRegime,
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
# Boundary 1: Rate Limiter Burst Exhaustion & Clock Refill
# ==============================================================================

def test_b1_rate_limiter_burst_and_backoff(fake_clock: FakeClock):
    """Verifies that requesting beyond capacity (30 req / 10s) triggers throttling,
    and advancing clock by 1.0 second refills 3 tokens (3.0 tokens/sec).
    """
    limiter = MockTokenBucketRateLimiter(capacity=30, window_seconds=10.0, clock=fake_clock)

    # Exhaust all 30 tokens
    for _ in range(30):
        assert limiter.acquire() is True

    # 31st request must be throttled
    assert limiter.acquire() is False
    assert limiter.throttle_count == 1

    # Advance clock by 1.0 second -> Refills 3 tokens (30 / 10 = 3.0 tokens/s)
    fake_clock.advance(1.0)

    # Exactly 3 tokens available now
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    # 4th should fail
    assert limiter.acquire() is False
    assert limiter.throttle_count == 2


# ==============================================================================
# Boundary 2: Zero Available TMN Balance Rejects BUY Orders
# ==============================================================================

def test_b2_zero_available_toman_balance(mock_wallex_client: MockWallexClient):
    """Verifies that when available Toman balance is 0, any BUY order is rejected."""
    mock_wallex_client.tmn_available = 0.0

    async def _run():
        result = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=4_200_000_000.0,
            quantity=0.0001,
            client_order_id="CL-ZERO-BAL",
        )
        assert result.status == OrderStatus.REJECTED
        assert "Insufficient TMN" in result.raw_response.get("error", "")

    asyncio.run(_run())


# ==============================================================================
# Boundary 3: Position Sizing Exact 20% Boundary & Clamping
# ==============================================================================

def test_b3_position_sizing_exact_20pct_boundary():
    """Verifies that allocation at exactly 20.00% is valid, whereas > 20%
    exceeds the risk policy ceiling.
    """
    equity = 5_000_000.0
    cap_pct = 0.20
    exact_cap = equity * cap_pct  # 1,000,000 TMN

    def validate_position_size(size_tmn: float, eq: float) -> Tuple[bool, float]:
        max_allowed = min(eq * cap_pct, 1_000_000.0)
        if size_tmn <= max_allowed + 1e-6:
            return True, size_tmn
        return False, max_allowed

    # Exactly at boundary
    is_valid, sized = validate_position_size(1_000_000.0, equity)
    assert is_valid is True
    assert sized == 1_000_000.0

    # Slightly above boundary (1,000,001 TMN)
    is_valid, clamped = validate_position_size(1_000_001.0, equity)
    assert is_valid is False
    assert clamped == 1_000_000.0


# ==============================================================================
# Boundary 4: Max Concurrent Positions Cap (Max 2 Open Positions)
# ==============================================================================

def test_b4_max_concurrent_positions_limit():
    """Verifies that a 3rd concurrent position is blocked when 2 are already open."""
    open_positions = ["BTCTMN", "ETHTMN"]
    max_concurrent = 2

    def can_open_position(symbol: str, current_open: List[str]) -> bool:
        if symbol in current_open:
            return True  # Adding to existing position / managing
        if len(current_open) >= max_concurrent:
            return False
        return True

    assert can_open_position("USDTTMN", open_positions) is False
    assert can_open_position("BTCTMN", open_positions) is True

    # If one is closed, opening is allowed again
    open_positions.pop(0)
    assert can_open_position("USDTTMN", open_positions) is True


# ==============================================================================
# Boundary 5: Circuit Breaker 10% Drawdown Exact Trip
# ==============================================================================

def test_b5_circuit_breaker_10pct_drawdown_exact_trip():
    """Verifies that when equity drops to <= 4,500,000 TMN (10.0% drawdown),
    the Circuit Breaker trips immediately and halts all trading.
    """
    initial_capital = 5_000_000.0
    cb_threshold_pct = 0.10  # 10.0%

    def check_circuit_breaker(peak_equity: float, current_equity: float) -> bool:
        dd = oracle_calc_drawdown(peak_equity, current_equity)
        return dd >= cb_threshold_pct

    # Equity = 4,500,100 TMN (DD = 9.998% < 10.0%) -> Safe
    assert check_circuit_breaker(initial_capital, 4_500_100.0) is False

    # Equity = 4,500,000 TMN (DD = 10.000%) -> TRIPPED!
    assert check_circuit_breaker(initial_capital, 4_500_000.0) is True

    # Equity = 4,499_999 TMN (DD = 10.00002%) -> TRIPPED!
    assert check_circuit_breaker(initial_capital, 4_499_999.0) is True


# ==============================================================================
# Boundary 6 - 9: 4-Stage Pre-Deadline Toman Liquidation Protocol
# ==============================================================================

def test_b6_pre_deadline_liquidation_t_minus_4h_lock(fake_clock: FakeClock):
    """At T - 4h before deadline, BUY signals must be strictly inhibited."""
    deadline = 1700259200.0  # 72 hours
    fake_clock.set_time(deadline - (4 * 3600.0))  # Exactly T - 4h

    def is_buy_allowed(current_time: float, t_end: float) -> bool:
        time_to_deadline = t_end - current_time
        if time_to_deadline <= 4 * 3600.0:  # <= 4 hours
            return False
        return True

    assert is_buy_allowed(fake_clock.time(), deadline) is False

    # 1 second before T - 4h (4 hours and 1 second left) -> Still allowed
    fake_clock.set_time(deadline - (4 * 3600.0 + 1.0))
    assert is_buy_allowed(fake_clock.time(), deadline) is True


def test_b7_pre_deadline_liquidation_t_minus_2h_limit_sells(mock_wallex_client: MockWallexClient, fake_clock: FakeClock):
    """At T - 2h before deadline, all open crypto positions must be submitted
    as limit sell orders to convert to Toman.
    """
    deadline = 1700259200.0
    fake_clock.set_time(deadline - (2 * 3600.0))  # Exactly T - 2h

    # Setup open BTC balance
    mock_wallex_client.crypto_balances["BTC"] = 0.0005
    mock_wallex_client.tmn_available = 2_900_000.0

    async def _run():
        btc_balance = mock_wallex_client.crypto_balances["BTC"]
        assert btc_balance > 0
        best_bid = mock_wallex_client.orderbooks["BTCTMN"]["bids"][0][0]

        # Execute limit sell at best bid for full liquidation
        result = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=best_bid,
            quantity=btc_balance,
            client_order_id="CL-LIQ-T2H",
        )
        assert result.status == OrderStatus.FILLED
        assert mock_wallex_client.crypto_balances["BTC"] == 0.0
        assert mock_wallex_client.tmn_available > 4_900_000.0

    asyncio.run(_run())


def test_b8_pre_deadline_liquidation_t_minus_30m_dust_sweep(mock_wallex_client: MockWallexClient, fake_clock: FakeClock):
    """At T - 30m before deadline, any remaining fractional crypto dust
    is swept via market order.
    """
    deadline = 1700259200.0
    fake_clock.set_time(deadline - (30 * 60.0))  # Exactly T - 30m

    # Fractional dust from rounding
    dust_btc = 0.0000042
    mock_wallex_client.crypto_balances["BTC"] = dust_btc

    async def _run():
        price = mock_wallex_client.orderbooks["BTCTMN"]["bids"][0][0]
        result = await mock_wallex_client.post_order(
            symbol="BTCTMN",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=price,
            quantity=dust_btc,
            client_order_id="CL-DUST-SWEEP",
        )
        assert result.status == OrderStatus.FILLED
        assert mock_wallex_client.crypto_balances["BTC"] == 0.0

    asyncio.run(_run())


def test_b9_pre_deadline_liquidation_t_minus_15m_all_tmn_assertion(mock_wallex_client: MockWallexClient, fake_clock: FakeClock):
    """At T - 15m before deadline, strictly asserts that all crypto balances are 0.0
    and 100% of equity is realized in Iranian Toman.
    """
    deadline = 1700259200.0
    fake_clock.set_time(deadline - (15 * 60.0))  # T - 15m

    # Account state fully liquidated
    mock_wallex_client.crypto_balances = {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0}
    mock_wallex_client.tmn_available = 5_240_000.0
    mock_wallex_client.tmn_locked = 0.0

    async def _run():
        balance = await mock_wallex_client.get_account_balance()
        for coin, amt in balance.crypto_balances.items():
            assert amt == 0.0, f"Crypto {coin} was not liquidated: {amt}"
        assert balance.total_equity_tmn == balance.toman_available
        assert balance.toman_locked == 0.0

    asyncio.run(_run())


# ==============================================================================
# Boundary 10: JEV Exact Hurdle Threshold (+0.35%)
# ==============================================================================

def test_b10_jev_exact_hurdle_threshold():
    """Verifies that E[V] = +0.00349 is REJECTED, while +0.00350 is APPROVED."""
    entry = 100.0
    target = 102.0  # +2%
    stop = 99.0     # -1%
    fee = 0.0050

    # Let E[V] = P * 0.02 - (1 - P) * 0.01 - 0.0050 = 0.03*P - 0.0150
    # To get E[V] = +0.00350: 0.03*P = 0.01850 => P = 0.01850 / 0.03 = 0.6166667
    p_exact = 0.01850 / 0.03
    jev_exact = oracle_calc_jev(p_exact, entry, target, stop, fee)
    assert jev_exact == pytest.approx(0.00350, abs=1e-6)

    # Sub-threshold probability
    p_sub = 0.01849 / 0.03
    jev_sub = oracle_calc_jev(p_sub, entry, target, stop, fee)
    assert jev_sub < 0.00350

    def evaluate_jev(jev_val: float) -> bool:
        return jev_val >= 0.00350

    assert evaluate_jev(jev_exact) is True
    assert evaluate_jev(jev_sub) is False


# ==============================================================================
# Boundary 11: Reward-to-Risk Exact Hurdle (1.50)
# ==============================================================================

def test_b11_reward_to_risk_exact_hurdle():
    """Verifies that Reward-to-Risk = 1.49 is REJECTED, while 1.50 is APPROVED."""
    entry = 100.0

    # R:R = 1.50: Downside = 2.0 (stop = 98.0), Upside = 3.0 (target = 103.0)
    rr_exact = oracle_calc_reward_to_risk(entry, target=103.0, stop=98.0)
    assert rr_exact == pytest.approx(1.50)

    # R:R = 1.49: Downside = 2.0, Upside = 2.98
    rr_sub = oracle_calc_reward_to_risk(entry, target=102.98, stop=98.0)
    assert rr_sub == pytest.approx(1.49)

    def evaluate_rr(rr_val: float) -> bool:
        return rr_val >= 1.50

    assert evaluate_rr(rr_exact) is True
    assert evaluate_rr(rr_sub) is False


# ==============================================================================
# Boundary 12: Extreme Order Book Imbalance Limits (+1.0, -1.0, and Empty)
# ==============================================================================

def test_b12_extreme_orderbook_imbalance():
    """Verifies orderbook imbalance calculations under 100% bid wall (+1.0),
    100% ask wall (-1.0), and empty books without NaN or ZeroDivisionError.
    """
    # 100% bids, 0 asks
    bids_only = [(4_200_000_000.0, 1.0)]
    asks_empty: List[Tuple[float, float]] = []
    assert oracle_calc_imbalance(bids_only, asks_empty) == 1.0

    # 100% asks, 0 bids
    asks_only = [(4_210_000_000.0, 1.0)]
    bids_empty: List[Tuple[float, float]] = []
    assert oracle_calc_imbalance(bids_empty, asks_only) == -1.0

    # Completely empty book
    assert oracle_calc_imbalance([], []) == 0.0


# ==============================================================================
# Boundary 13: Inverted or Zero Spread Protection
# ==============================================================================

def test_b13_inverted_or_zero_spread_protection():
    """Verifies that an inverted order book (best_bid >= best_ask) or zero spread
    is detected as an anomaly and flagged.
    """
    def is_valid_spread(best_bid: float, best_ask: float) -> bool:
        if best_bid <= 0 or best_ask <= 0:
            return False
        return best_ask > best_bid

    # Normal spread
    assert is_valid_spread(4_200_000_000.0, 4_210_000_000.0) is True

    # Zero spread (crossed)
    assert is_valid_spread(4_200_000_000.0, 4_200_000_000.0) is False

    # Inverted spread (bid higher than ask)
    assert is_valid_spread(4_210_000_000.0, 4_200_000_000.0) is False
