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
    WALLEX_CREDIT_DISABLED_SYMBOLS,
    WallexAPIError,
    WallexAuthError,
    WallexClientError,
    WallexInsufficientFundsError,
    WallexNetworkError,
    WallexOrderError,
    WallexRateLimitError,
    is_credit_market_supported,
    normalize_symbol,
    validate_credit_market_symbol,
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
    "WALLEX_CREDIT_DISABLED_SYMBOLS",
    "normalize_symbol",
    "is_credit_market_supported",
    "validate_credit_market_symbol",
]
