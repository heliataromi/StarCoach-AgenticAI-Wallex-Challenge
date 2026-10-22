# decision_logs/

**Parent context:** `../AGENT.md`
**Generated:** 2026-10-22 · **Updated:** 2026-10-22

## Purpose
Directory storing immutable, structured JSON and Markdown audit logs for every trade executed or evaluated by the trading engine. Serves as direct proof of non-manual, algorithmic AI execution for hackathon judges.

## Structure
Every log file follows the naming convention: `trade_<timestamp>_<pair>_<action>.json`.

## Key Fields Required for Each Log
- `timestamp`: UTC ISO-8601 timestamp
- `trade_id`: Unique identifier
- `market_pair`: Trading symbol (e.g., `BTC_TMN`)
- `action`: `BUY` or `SELL`
- `macro_state`: Market regime classification and agent summary
- `micro_signals`: Indicator snapshot (RSI, EMA, Order Book Imbalance, ATR)
- `jev_evaluation`: Estimated win probability, expected value %, approval status
- `risk_guardrails`: Stop-loss price, portfolio risk %, current drawdown

## For AI Agents
- Never manually edit or delete decision log files.
- Ensure 100% of orders dispatched by the engine generate a corresponding log entry.
