"""Drawdown Sentinel, Circuit Breaker, and Mandatory Deadline Toman Liquidation.
Protects the 30-pt drawdown score and guarantees 100% realized Iranian Toman balance.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.risk.models import AccountSnapshot, LiquidationPhase, PositionRiskState, RiskConfig


class DrawdownSentinel:
    """
    Monitors peak-to-trough account drawdown in real time, activates hard circuit breakers,
    and coordinates the multi-stage deadline liquidation protocol.
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        deadline_timestamp: Optional[float] = None,
        clock_func: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config or RiskConfig()
        self.deadline_timestamp = deadline_timestamp
        self._clock = clock_func or time.time

        self.high_water_mark: float = self.config.initial_capital_tmn
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_triggered_at: Optional[float] = None
        self.circuit_breaker_reason: Optional[str] = None
        self.active_positions: Dict[str, PositionRiskState] = {}

    def _now(self) -> float:
        return self._clock()

    def update_equity(
        self,
        tmn_available: float,
        tmn_locked: float,
        crypto_holdings: Dict[str, float],
        market_prices: Dict[str, float],
    ) -> AccountSnapshot:
        """
        Updates portfolio high-water mark, calculates peak-to-trough drawdown %,
        and checks circuit breaker boundaries.
        """
        now = self._now()

        # Calculate crypto equity in Toman
        crypto_equity_tmn = 0.0
        for symbol, qty in crypto_holdings.items():
            if qty > 0:
                price = market_prices.get(symbol, market_prices.get(f"{symbol}TMN", 0.0))
                crypto_equity_tmn += (qty * price)

        total_equity = tmn_available + tmn_locked + crypto_equity_tmn

        # Update high-water mark
        if total_equity > self.high_water_mark:
            self.high_water_mark = total_equity

        # Drawdown calculation
        drawdown_tmn = max(0.0, self.high_water_mark - total_equity)
        drawdown_pct = (drawdown_tmn / self.high_water_mark) if self.high_water_mark > 0 else 0.0

        # Circuit Breaker Gating: 10% drawdown ceiling or equity <= 4.5M TMN
        if not self.circuit_breaker_active:
            if drawdown_pct >= self.config.max_drawdown_limit_pct:
                self.circuit_breaker_active = True
                self.circuit_breaker_triggered_at = now
                self.circuit_breaker_reason = (
                    f"Max allowable drawdown breached: {drawdown_pct * 100:.2f}% "
                    f"(limit: {self.config.max_drawdown_limit_pct * 100:.1f}%)"
                )
            elif total_equity <= self.config.circuit_breaker_equity_tmn:
                self.circuit_breaker_active = True
                self.circuit_breaker_triggered_at = now
                self.circuit_breaker_reason = (
                    f"Account equity dropped to {total_equity:,.0f} TMN "
                    f"(circuit breaker threshold: {self.config.circuit_breaker_equity_tmn:,.0f} TMN)"
                )

        return AccountSnapshot(
            timestamp=now,
            tmn_available=tmn_available,
            tmn_locked=tmn_locked,
            crypto_equity_tmn=crypto_equity_tmn,
            total_equity_tmn=total_equity,
            high_water_mark_tmn=self.high_water_mark,
            drawdown_tmn=drawdown_tmn,
            drawdown_pct=drawdown_pct,
            circuit_breaker_active=self.circuit_breaker_active,
        )

    def get_liquidation_phase(self) -> LiquidationPhase:
        """
        Determines current operational liquidation phase relative to the hackathon deadline.
        T - 4h: Inhibit new buys
        T - 2h: Orderly liquidation of crypto positions
        T - 30m: Sweep fractional dust via market order
        T - 15m: Completed assertion
        """
        if self.deadline_timestamp is None:
            return LiquidationPhase.NORMAL_TRADING

        now = self._now()
        time_left_sec = self.deadline_timestamp - now

        if time_left_sec <= 15 * 60:
            return LiquidationPhase.COMPLETED
        elif time_left_sec <= 30 * 60:
            return LiquidationPhase.SWEEP_DUST
        elif time_left_sec <= 2 * 3600:
            return LiquidationPhase.ORDERLY_LIQUIDATION
        elif time_left_sec <= 4 * 3600:
            return LiquidationPhase.INHIBIT_NEW_BUYS

        return LiquidationPhase.NORMAL_TRADING

    def can_open_position(self) -> Tuple[bool, Optional[str]]:
        """
        Determines whether new trade entries are permitted.
        Checks circuit breaker status and pre-deadline liquidation windows.
        """
        if self.circuit_breaker_active:
            return False, f"Trading halted by Circuit Breaker: {self.circuit_breaker_reason}"

        phase = self.get_liquidation_phase()
        if phase != LiquidationPhase.NORMAL_TRADING:
            return False, f"New entries prohibited during liquidation phase: {phase.value}"

        return True, None
