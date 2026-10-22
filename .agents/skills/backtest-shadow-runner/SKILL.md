---
name: backtest-shadow-runner
version: "1.0.0"
description: "Protocol for running deterministic historical backtests and live real-time shadow paper-trading on Wallex orderbook data, generating verifiable metrics for hackathon technical evaluation."
skill_tier: functional
author:
  name: heliataromi
tags:
  - backtesting
  - shadow-testing
  - simulation
  - verification
  - metrics
  - wallex

triggers:
  en:
    - "run backtest"
    - "start shadow mode"
    - "verify historical performance"
    - "test repeatability"
  fa:
    - "بک تست"
    - "تست سایه"
    - "ارزیابی تکرارپذیری"

lifecycle_status: "active"
---

# Backtest & Shadow Testing Runner Standard

## 1. Scope
Ensures that all trading strategies can be deterministically audited by judges (earning the 20 points for Backtest/Shadow and 25 points for Repeatability).

## 2. Historical Backtesting Protocol
1. **Data Sourcing:** Historical OHLCV candles and orderbook depth files stored in `data/historical/`.
2. **Realistic Fill Simulation:**
   - Orders are simulated with realistic orderbook depth matching (not naive mid-price fills).
   - Wallex fees (0.25% maker/taker) applied to all trades.
   - Slippage model applied based on trade size vs available liquidity.
3. **Execution Command:**
   ```bash
   python run_backtest.py --pair BTC_TMN --start 2026-09-01 --end 2026-10-01
   ```
4. **Standard Output Metrics:**
   - Net Profit / Return (%)
   - Maximum Drawdown (%)
   - Profit Factor & Win Rate (%)
   - Sharpe Ratio & Sortino Ratio
   - Total Trades Executed

## 3. Shadow Paper-Trading Protocol
1. **Live Connection:** Connects directly to Wallex live orderbook and ticker feeds.
2. **Zero Capital Execution:** Trades are matched against real live orderbook liquidity, tracking virtual portfolio balance and virtual fills without sending live capital orders.
3. **Audit Verification:** Verifies that signals trigger reliably under live network conditions and that decision logs are formatted properly.
