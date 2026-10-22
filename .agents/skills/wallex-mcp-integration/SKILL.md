---
name: wallex-mcp-integration
version: "1.0.0"
description: "Protocol for integrating Wallex REST API and Wallex MCP for algorithmic trading: authenticating endpoints, streaming live orderbook depth, managing token bucket rate limits, placing limit/market orders, and handling exchange connection resilience."
skill_tier: functional
author:
  name: heliataromi
tags:
  - wallex
  - mcp
  - api
  - crypto
  - orderbook
  - execution

triggers:
  en:
    - "wallex api"
    - "wallex mcp"
    - "fetch orderbook"
    - "place wallex order"
    - "cancel order"
    - "check wallex balance"
  fa:
    - "اتصال به والکس"
    - "ارسال سفارش والکس"
    - "دریافت اوردر بوک"

negative_triggers:
  en:
    - "manual trading"
    - "unauthorized market"
    - "fake order"

lifecycle_status: "active"
---

# Wallex API & MCP Integration Standard

## 1. Scope & Purpose
Defines the safe, low-latency, rate-limited integration interface with the Wallex exchange for autonomous trading agents.

## 2. Security & Red Lines
1. **Zero Credential Hardcoding:** All API keys and secrets must load exclusively from environment variables (`WALLEX_API_KEY`, `WALLEX_SECRET`).
2. **Strict Market Whitelist:** Only trade in allowed Wallex credit markets specified in the challenge. Never submit orders to unverified pairs.
3. **No Wash Trading or Self-Trading:** Orders must strictly represent legitimate market signals.

## 3. Operational Protocols

### Token-Bucket Rate Limiter
Wallex enforces request rate limits per minute. All requests must pass through an async token bucket:
- Standard capacity: 30 requests / 10 seconds.
- On HTTP 429 response: Exponential backoff ($1.5 \times$ delay) with jitter; never hammer the endpoint.

### Orderbook Ingestion
- Ingest top-10 bids and asks.
- Compute mid-price, spread percentage, and depth imbalance ratio.
- Reject market data with timestamp drift > 5.0 seconds.

### Order Dispatch & Execution
- Prefer LIMIT orders placed at top-of-book or within the spread to minimize taker fee slippage.
- Record Wallex `client_order_id` for tracking.
- Query order status asynchronously to confirm full or partial fills.
