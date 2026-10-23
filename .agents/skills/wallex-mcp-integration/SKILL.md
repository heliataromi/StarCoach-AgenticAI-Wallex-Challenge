---
name: wallex-mcp-integration
version: "1.1.0"
description: "Protocol for integrating Wallex REST API and Wallex MCP (https://api.wallex.ir/mcp) for algorithmic trading: authenticating endpoints, streaming live orderbook depth, managing token bucket rate limits, placing spot/credit orders, and handling exchange connection resilience."
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
    - "wallex credit subaccount"
  fa:
    - "اتصال به والکس"
    - "ارسال سفارش والکس"
    - "دریافت اوردر بوک"
    - "ام سی پی والکس"

negative_triggers:
  en:
    - "manual trading"
    - "unauthorized market"
    - "fake order"

lifecycle_status: "active"
---

# Wallex API & MCP Integration Standard

## 1. Scope & Endpoint Reference
- **Wallex MCP Endpoint:** `https://api.wallex.ir/mcp` (Protocol: JSON-RPC 2.0 via HTTP POST)
- **Authentication:** Bearer token / API-key passed in headers or MCP context.
- **Dedicated Account:** Wallex Credit Sub-Account (5,000,000 IRT hackathon credit).

## 2. Verified Wallex MCP Tool Inventory

### A. Market Ingestion Tools
1. `wallex_basic_get_markets`: List all Wallex markets.
2. `wallex_basic_get_market_depth`: Get bid/ask orderbook depth for a symbol (e.g., `symbol: "BTCTMN"`).
3. `wallex_spot_get_depth`: Get full depth for spot markets.
4. `wallex_basic_candle_history`: Retrieve OHLCV candles (`symbol`, `resolution`: "1"|"5"|"15"|"60", `from`, `to`).
5. `wallex_basic_get_latest_trades`: List recent public trades for market volume analysis.

### B. Account & Credit Sub-Account Tools
1. `wallex_credit_active_sub_accounts_list`: Inspect active hackathon credit sub-accounts.
2. `wallex_credit_sub_account_balances_details`: Fetch exact real-time Toman and asset balances on the credit sub-account.
3. `wallex_basic_get_account_fees`: Fetch maker and taker fee rates to calibrate the JEV hurdle.

### C. Execution & Order Lifecycle Tools
1. `wallex_spot_post_account_orders`:
   - Parameters: `symbol`, `side` ("BUY"|"SELL"), `type` ("LIMIT"|"MARKET"|"STOP_LIMIT"), `price`, `quantity`, `client_id`, `stop_Price`.
2. `wallex_spot_get_open_order`: Monitor pending orders.
3. `wallex_spot_get_orders_client`: Query order status by `client_id`.
4. `wallex_spot_delete_account_orders` / `wallex_spot_delete_orders_client`: Cancel orders.

## 3. Operational Protocols

### Token-Bucket Rate Limiter
Wallex enforces request rate limits (monitored via `x-ratelimit-remaining` and `x-ratelimit-limit` headers):
- Standard capacity: 30 requests / 10 seconds.
- On HTTP 429 response: Exponential backoff ($1.5 \times$ delay) with jitter.

### JSON-RPC 2.0 Call Format
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "wallex_basic_get_market_depth",
    "arguments": {
      "symbol": "BTCTMN"
    }
  }
}
```
