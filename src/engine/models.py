"""Data contracts and execution models for the Micro Execution Engine.

Adheres to Ponytail minimalism: frozen dataclasses and lightweight enums.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Optional
from src.wallex.models import OrderSide, OrderType


class ExecutionMode(str, Enum):
    """Engine operational mode."""
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    BACKTEST = "BACKTEST"


class ExecutionStatus(str, Enum):
    """Outcome status for order dispatch attempts."""
    SUCCESS = "SUCCESS"
    REJECTED_RISK = "REJECTED_RISK"
    REJECTED_JEV = "REJECTED_JEV"
    REJECTED_CIRCUIT = "REJECTED_CIRCUIT"
    REJECTED_DEADLINE = "REJECTED_DEADLINE"
    FAILED_EXCHANGE = "FAILED_EXCHANGE"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class OrderExecutionResult:
    """Detailed record of an executed or attempted trade dispatch."""
    success: bool
    status: ExecutionStatus
    symbol: str
    side: OrderSide
    order_type: OrderType
    requested_price: float
    requested_quantity: float
    executed_price: float = 0.0
    executed_quantity: float = 0.0
    fee_paid: float = 0.0
    client_order_id: str = ""
    exchange_order_id: Optional[str] = None
    latency_ms: float = 0.0
    message: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class EngineMetrics:
    """Cumulative operational execution metrics."""
    total_orders_dispatched: int = 0
    total_orders_filled: int = 0
    total_orders_rejected: int = 0
    average_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    total_volume_tmn: float = 0.0
    total_fees_paid_tmn: float = 0.0

    def record_execution(self, result: OrderExecutionResult) -> None:
        """Updates runtime metrics with an execution outcome."""
        self.total_orders_dispatched += 1
        if result.success:
            self.total_orders_filled += 1
            cost = result.executed_price * result.executed_quantity
            self.total_volume_tmn += cost
            self.total_fees_paid_tmn += result.fee_paid
        else:
            self.total_orders_rejected += 1

        # Update latency
        if result.latency_ms > 0:
            if self.total_orders_dispatched == 1:
                self.average_latency_ms = result.latency_ms
            else:
                self.average_latency_ms = (
                    (self.average_latency_ms * (self.total_orders_dispatched - 1)) + result.latency_ms
                ) / self.total_orders_dispatched
            self.max_latency_ms = max(self.max_latency_ms, result.latency_ms)
