"""Wallex Market Ingestion & Client Subsystem.
Provides production JSON-RPC 2.0 MCP & REST Wallex clients, token bucket rate limiting,
immutable quant-grade data models, and high-fidelity mock testing harnesses.
"""
from __future__ import annotations

from src.wallex.client import BaseWallexClient, WallexClient
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import (
    AccountBalance,
    Candle,
    CorruptMarketDataError,
    DepthLevel,
    MarketDataSnapshot,
    OrderBook,
    OrderExecutionResult,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WallexAPIError,
    WallexAuthError,
    WallexClientError,
    WallexInsufficientFundsError,
    WallexNetworkError,
    WallexOrderError,
    WallexRateLimitError,
)
from src.wallex.rate_limiter import PriorityWaiter, TokenBucketRateLimiter

__all__ = [
    "BaseWallexClient",
    "WallexClient",
    "MockWallexClient",
    "TokenBucketRateLimiter",
    "PriorityWaiter",
    "MarketDataSnapshot",
    "DepthLevel",
    "OrderBook",
    "Candle",
    "AccountBalance",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "OrderRequest",
    "OrderExecutionResult",
    "WallexClientError",
    "WallexRateLimitError",
    "WallexAuthError",
    "WallexNetworkError",
    "WallexOrderError",
    "WallexInsufficientFundsError",
    "WallexAPIError",
    "CorruptMarketDataError",
]
