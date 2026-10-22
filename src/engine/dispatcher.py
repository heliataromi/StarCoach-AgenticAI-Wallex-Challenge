"""Order Execution Dispatcher.

Sub-50ms async order execution dispatcher integrating:
- Wallex Client / MCP routing
- JEV (Joint Expected Value) decision gate
- Position Sizer with hard 20% equity ceiling
- Drawdown Sentinel with 10% circuit breaker and deadline liquidation
- Real-time Portfolio Tracker
"""
from __future__ import annotations

import time
from typing import Optional
from src.engine.models import EngineMetrics, ExecutionStatus, OrderExecutionResult
from src.engine.portfolio import PortfolioTracker
from src.risk.drawdown import DrawdownSentinel
from src.risk.jev import JEVEvaluator
from src.risk.models import LiquidationPhase
from src.risk.sizing import PositionSizer
from src.strategy.models import MacroTradingPolicy, MarketRegime, SignalAction, TradingSignal
from src.wallex.client import BaseWallexClient
from src.wallex.models import OrderRequest, OrderSide, OrderStatus, OrderType


class OrderExecutionDispatcher:
    """Orchestrates fast-path pre-trade risk validation and exchange order dispatch."""

    def __init__(
        self,
        client: BaseWallexClient,
        portfolio: PortfolioTracker,
        jev_evaluator: Optional[JEVEvaluator] = None,
        position_sizer: Optional[PositionSizer] = None,
        drawdown_sentinel: Optional[DrawdownSentinel] = None,
        min_order_tmn: float = 50_000.0,  # Wallex 50,000 TMN minimum trade rule
    ) -> None:
        self.client = client
        self.portfolio = portfolio
        self.jev_evaluator = jev_evaluator or JEVEvaluator()
        self.position_sizer = position_sizer or portfolio.position_sizer
        self.drawdown_sentinel = drawdown_sentinel or portfolio.drawdown_sentinel
        self.min_order_tmn = min_order_tmn
        self.metrics = EngineMetrics()

    async def dispatch_signal(
        self,
        signal: TradingSignal,
        policy: Optional[MacroTradingPolicy] = None,
        current_time: Optional[float] = None,
    ) -> OrderExecutionResult:
        """Executes a trading signal through full risk & JEV gates to Wallex."""
        t0 = time.perf_counter()
        now = current_time if current_time is not None else time.time()

        # Step 0: Ignore HOLD signals immediately
        if signal.action == SignalAction.HOLD:
            return OrderExecutionResult(
                success=True,
                status=ExecutionStatus.SKIPPED,
                symbol=signal.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                requested_price=signal.current_price,
                requested_quantity=0.0,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                message="Signal action is HOLD; no order dispatched",
                timestamp=now,
            )

        # Step 1: Macro Policy Compliance Gate
        if policy is not None:
            if policy.is_expired(now):
                return self._reject(
                    ExecutionStatus.REJECTED_RISK,
                    signal,
                    t0,
                    now,
                    "Macro policy expired; awaiting policy refresh",
                )
            if signal.action == SignalAction.BUY:
                if signal.symbol not in policy.allowed_symbols:
                    return self._reject(
                        ExecutionStatus.REJECTED_RISK,
                        signal,
                        t0,
                        now,
                        f"Symbol {signal.symbol} not in policy allowed_symbols",
                    )
                if policy.active_regime == MarketRegime.HIGH_VOLATILITY_PANIC:
                    return self._reject(
                        ExecutionStatus.REJECTED_RISK,
                        signal,
                        t0,
                        now,
                        "Macro policy active regime is HIGH_VOLATILITY_PANIC; BUYS prohibited",
                    )

        # Step 2: Drawdown Sentinel & Deadline Gate
        if signal.action == SignalAction.BUY:
            can_trade, block_reason = self.drawdown_sentinel.can_open_position()
            if not can_trade:
                if self.drawdown_sentinel.circuit_breaker_active:
                    return self._reject(
                        ExecutionStatus.REJECTED_CIRCUIT,
                        signal,
                        t0,
                        now,
                        f"Circuit breaker active: {block_reason}",
                    )
                return self._reject(
                    ExecutionStatus.REJECTED_DEADLINE,
                    signal,
                    t0,
                    now,
                    f"Drawdown sentinel blocked: {block_reason}",
                )

            # Check liquidation phase
            phase = self.drawdown_sentinel.get_liquidation_phase()
            if phase != LiquidationPhase.NORMAL_TRADING:
                return self._reject(
                    ExecutionStatus.REJECTED_DEADLINE,
                    signal,
                    t0,
                    now,
                    f"Deadline liquidation phase active ({phase.value}); BUYS prohibited",
                )

        # Step 3: Mathematical JEV Gate (For BUY entry opportunities)
        if signal.action == SignalAction.BUY:
            jev_res = self.jev_evaluator.evaluate(
                entry_price=signal.current_price,
                take_profit_price=signal.target_price,
                stop_loss_price=signal.stop_loss_price,
                win_probability=signal.estimated_win_probability,
                side="BUY",
            )
            if not jev_res.is_approved:
                return self._reject(
                    ExecutionStatus.REJECTED_JEV,
                    signal,
                    t0,
                    now,
                    f"JEV hurdle rejected: E[V]={jev_res.expected_value * 100:.3f}% (min +0.35%), R:R={jev_res.reward_to_risk:.2f}. Reason: {jev_res.rejection_reason}",
                )

        # Step 4: Position Sizing & Allocation
        equity = self.portfolio.calculate_total_equity()
        if signal.action == SignalAction.BUY:
            size_tmn, qty, rejection_reason = self.position_sizer.calculate_position_size(
                current_equity_tmn=equity,
                available_tmn=self.portfolio.tmn_available,
                active_positions_count=len(self.portfolio.active_positions),
                price=signal.current_price,
            )
            if rejection_reason is not None or size_tmn <= 0:
                return self._reject(
                    ExecutionStatus.REJECTED_RISK,
                    signal,
                    t0,
                    now,
                    f"Position sizing rejected: {rejection_reason}",
                )

            if size_tmn < self.min_order_tmn:
                return self._reject(
                    ExecutionStatus.REJECTED_RISK,
                    signal,
                    t0,
                    now,
                    f"Allocated capital ({size_tmn:.0f} TMN) below minimum order threshold ({self.min_order_tmn:.0f} TMN)",
                )

            req_quantity = qty
            req_side = OrderSide.BUY
            req_type = OrderType.LIMIT
            req_price = signal.current_price
        else:
            # SELL / LIQUIDATE
            pos = self.portfolio.get_position(signal.symbol)
            base_asset = signal.symbol.replace("TMN", "")
            avail_crypto = self.portfolio.crypto_balances.get(base_asset, 0.0)

            qty_to_sell = pos.quantity if pos else avail_crypto
            if qty_to_sell <= 0:
                return self._reject(
                    ExecutionStatus.REJECTED_RISK,
                    signal,
                    t0,
                    now,
                    f"No active position or crypto available to sell for {signal.symbol}",
                )

            req_quantity = qty_to_sell
            req_side = OrderSide.SELL
            req_type = OrderType.MARKET if signal.regime == MarketRegime.HIGH_VOLATILITY_PANIC else OrderType.LIMIT
            req_price = signal.current_price

        # Step 5: Exchange Order Dispatch
        client_order_id = f"cl-{signal.symbol}-{int(now * 1000)}"
        order_req = OrderRequest(
            symbol=signal.symbol,
            side=req_side,
            order_type=req_type,
            price=req_price,
            quantity=req_quantity,
            client_order_id=client_order_id,
        )

        try:
            order_resp = await self.client.post_order(order_req)
            latency = (time.perf_counter() - t0) * 1000.0

            if order_resp.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING):
                exec_price = float(
                    getattr(order_resp, "average_fill_price", None)
                    or getattr(order_resp, "executed_price", None)
                    or getattr(order_resp, "price", None)
                    or req_price
                )
                exec_qty = float(
                    getattr(order_resp, "filled_quantity", None)
                    or getattr(order_resp, "executed_quantity", None)
                    or getattr(order_resp, "quantity", None)
                    or req_quantity
                )
                fee = float(
                    getattr(order_resp, "fee_paid_tmn", None)
                    or getattr(order_resp, "fee_paid", None)
                    or (exec_price * exec_qty * 0.0015)
                )
                exch_id = getattr(order_resp, "exchange_order_id", None) or getattr(order_resp, "order_id", None)

                res = OrderExecutionResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    symbol=signal.symbol,
                    side=req_side,
                    order_type=req_type,
                    requested_price=req_price,
                    requested_quantity=req_quantity,
                    executed_price=exec_price,
                    executed_quantity=exec_qty,
                    fee_paid=fee,
                    client_order_id=client_order_id,
                    exchange_order_id=exch_id,
                    latency_ms=latency,
                    message=f"Order executed successfully in {latency:.2f}ms",
                    timestamp=now,
                )

                # Update portfolio and runtime metrics
                self.portfolio.record_fill(res)
                self.metrics.record_execution(res)
                return res
            else:
                return self._reject(
                    ExecutionStatus.FAILED_EXCHANGE,
                    signal,
                    t0,
                    now,
                    f"Exchange returned status: {order_resp.status}",
                )
        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000.0
            fail_res = OrderExecutionResult(
                success=False,
                status=ExecutionStatus.FAILED_EXCHANGE,
                symbol=signal.symbol,
                side=req_side,
                order_type=req_type,
                requested_price=req_price,
                requested_quantity=req_quantity,
                latency_ms=latency,
                message=f"Exchange error: {str(e)}",
                timestamp=now,
            )
            self.metrics.record_execution(fail_res)
            return fail_res

    def _reject(
        self,
        status: ExecutionStatus,
        signal: TradingSignal,
        t0: float,
        timestamp: float,
        message: str,
    ) -> OrderExecutionResult:
        latency = (time.perf_counter() - t0) * 1000.0
        res = OrderExecutionResult(
            success=False,
            status=status,
            symbol=signal.symbol,
            side=OrderSide.BUY if signal.action == SignalAction.BUY else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            requested_price=signal.current_price,
            requested_quantity=0.0,
            latency_ms=latency,
            message=message,
            timestamp=timestamp,
        )
        self.metrics.record_execution(res)
        return res
