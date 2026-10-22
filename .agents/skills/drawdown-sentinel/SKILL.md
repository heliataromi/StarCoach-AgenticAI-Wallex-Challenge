---
name: drawdown-sentinel
version: "1.0.0"
description: "Capital preservation and drawdown protection protocol: enforces maximum position sizing (≤20-25%), dynamic trailing stop-loss, circuit breaker locks on 10% drawdown, and mandatory 100% portfolio liquidation back to Iranian Toman (TMN) before hackathon deadline."
skill_tier: functional
author:
  name: heliataromi
tags:
  - risk
  - drawdown
  - capital-management
  - circuit-breaker
  - liquidation
  - wallex

triggers:
  en:
    - "drawdown control"
    - "circuit breaker"
    - "stop loss"
    - "liquidate to toman"
    - "position sizing"
  fa:
    - "کنترل دراداون"
    - "مدیریت سرمایه"
    - "نقد کردن به تومان"
    - "حد ضرر"

negative_triggers:
  en:
    - "all-in"
    - "unlimited risk"
    - "overnight coin holding"

lifecycle_status: "active"
---

# Drawdown Sentinel & Capital Preservation Protocol

## 1. Scope & Objective
Protects the 5,000,000 Toman account from severe drawdowns (worth 30 points in hackathon evaluation) and ensures complete compliance with the mandatory Toman realization rule (Section 5 of `CHALLENGE.md`).

## 2. Invariant Rules

### Invariant 1: Position Size Ceiling
- No single order may exceed **20% of account equity** (max 1,000,000 TMN on initial 5M capital).
- Max 2 concurrent open positions across separate allowed trading pairs.

### Invariant 2: Dynamic Stop-Loss
- Every open position must have an active stop-loss order placed at or monitored by the engine.
- Stop-loss distance is determined dynamically by $1.2 \times ATR_{14}$ or max $-2.0\%$, whichever is tighter.

### Invariant 3: Circuit Breaker
- If peak-to-trough account drawdown reaches **10.0%** (Equity $\le 4,500,000$ TMN):
  1. Immediately close all open positions.
  2. Lock trading engine into safe mode.
  3. Require human review before resuming.

### Invariant 4: Mandatory Toman Liquidation Schedule
- **$T - 4$ hours:** Enter `PRE_DEADLINE_LIQUIDATION`. Inhibit all new BUY signals.
- **$T - 2$ hours:** Execute orderly limit-sell orders for all crypto assets.
- **$T - 30$ minutes:** Sweep any remaining fractional coin dust to Toman via market order.
- **$T - 15$ minutes:** Verify and assert: $\text{Crypto Balance} = 0$, $\text{Toman Balance} = 100\%$.
