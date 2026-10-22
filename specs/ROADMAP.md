# Project Roadmap & Subtask Breakdown: Wallex Trading Engine

**Project:** StarCoach Wallex Algorithmic Trading Challenge  
**Architecture:** Hierarchical Asynchronous Agent + JEV Decision Gate  
**Teamwork Allocation:** Compatible with `/team` & `/teamwork-preview`  
**Discipline Standard:** `/git-workflow` (Dual Base Branches, 3-Gate Approvals) & `/ponytail` (YAGNI Ladder)  

---

## Roadmap Overview & Milestones

```
Phase 0 ───> Phase 1 ───> Phase 2 ───> Phase 3 ───> Phase 4 ───> Phase 5 ───> Phase 6 ───> Phase 7 ───> Phase 8
Repo Init    Wallex Client JEV & Risk  Macro Brain  Execution    Decision Log  Backtest/    Shadow &     Deliverables
& Branches   & Data Feed   Sentinel    & Regimes    Engine       Auditor       Test Harness Live Deploy  & Demo Video
```

---

## Detailed Task Breakdown

### Phase 0: Environment, Git Setup & Industry Skills
- [ ] **Task 0.1: Git Repository & Branch Initialization**
  - Run git initialization and link remote `git@github.com:heliataromi/StarCoach-AgenticAI-Wallex-Challenge.git`.
  - Establish dual base branch architecture: `main` (production/tagged releases) and `develop` (active integration).
  - Configure `.gitignore` for Python 3.12 (exclude `.venv`, `__pycache__`, `.env`, local credentials, runtime logs).
- [ ] **Task 0.2: Author Specialized In-Repo Skills via `/skill-writer`**
  - Create `.agents/skills/wallex-mcp-integration/SKILL.md` (Standard for Wallex REST/MCP).
  - Create `.agents/skills/jev-decision-evaluator/SKILL.md` (Standard for JEV mathematical expectancy gating).
  - Create `.agents/skills/drawdown-sentinel/SKILL.md` (Standard for risk limits and Toman liquidation).
  - Create `.agents/skills/backtest-shadow-runner/SKILL.md` (Standard for historical & shadow simulation).
- [ ] **Task 0.3: Deepinit Hierarchical `AGENTS.md` Setup**
  - Generate nested `AGENTS.md` files across `src/`, `tests/`, `specs/`, and `docs/` per `/deepinit`.

---

### Phase 1: Wallex Client & Ingestion Subsystem (`src/wallex`)
- [ ] **Task 1.1: Wallex REST API & Token Management**
  - Implement async client (`httpx`) for authenticated requests to Wallex endpoints.
  - Token bucket rate limiter preventing 429 errors.
  - Safe credential management reading from environment variables (`WALLEX_API_KEY`, `WALLEX_SECRET`).
- [ ] **Task 1.2: Order Book & Ticker Streamer**
  - Ingest top-10 bid/ask order book depth and 1m/15m OHLCV candle history for permitted credit markets (e.g. `BTC_TMN`, `ETH_TMN`).
  - Calculate real-time spread and Order Book Imbalance ratio:
    $$\text{Imbalance} = \frac{\text{BidVol} - \text{AskVol}}{\text{BidVol} + \text{AskVol}}$$
- [ ] **Task 1.3: Verification & Unit Self-Check**
  - Single runnable check: `tests/test_wallex_client.py` mocking orderbook responses and verifying rate limiter.

---

### Phase 2: Risk Sentinel & JEV Mathematical Gate (`src/risk`)
- [ ] **Task 2.1: JEV (Joint Expected Value) Evaluator**
  - Implement mathematical filter calculating \(\mathbb{E}[V] = P(\text{Win}) \times \text{Gain} - (1-P) \times \text{Loss} - \text{Fees}\).
  - Enforce minimum positive expectancy threshold (\(\mathbb{E}[V] \ge +0.35\%\)).
- [ ] **Task 2.2: Position Sizing & ATR Dynamic Stop-Loss**
  - Cap single-order allocation to max 20% of account equity (max 1,000,000 TMN).
  - Calculate dynamic ATR-based stop-loss and trailing stops.
- [ ] **Task 2.3: Circuit Breaker & Mandatory Toman Liquidation Routine**
  - Circuit Breaker: Halt new trades if drawdown exceeds 10% (preserving the 30-pt drawdown score).
  - Auto-Liquidation: Enforce 100% conversion of all crypto assets back to Toman 4 hours prior to competition deadline.

---

### Phase 3: Macro Agentic Brain & Multi-Regime Strategy (`src/strategy`)
- [ ] **Task 3.1: Latent Market Regime Classifier**
  - Map market features (volatility, momentum, order book depth) into discrete states: `BULLISH_TREND`, `RANGING_SIDEWAYS`, `HIGH_VOLATILITY_PANIC`.
- [ ] **Task 3.2: Macro Agent Reasoner**
  - LLM Agent running on 5–15 min intervals or regime triggers.
  - Outputs structured `MacroTradingPolicy` JSON directing the micro execution engine.
- [ ] **Task 3.3: 3-Regime Signal Generators**
  - Regime 1: EMA crossover + orderbook momentum breakout.
  - Regime 2: Bollinger Band mean-reversion + RSI oversold bounce with bid wall support.
  - Regime 3: Capital preservation cash lock (100% Toman).

---

### Phase 4: Micro Execution Engine (`src/engine`)
- [ ] **Task 4.1: Sub-50ms Async Execution Dispatcher**
  - Match real-time market data against active `MacroTradingPolicy`.
  - Dispatch limit / market orders to Wallex API/MCP with minimal latency.
- [ ] **Task 4.2: Order Lifecycle & State Tracker**
  - Track order placement, pending fills, partial fills, and cancellations.
  - Balance reconciliation with Wallex account state.

---

### Phase 5: Structured Decision Auditor (`src/logger`)
- [ ] **Task 5.1: Immutable JSON/MD Audit Logger**
  - Automatically write a detailed log into `decision_logs/` for 100% of generated orders.
  - Include timestamp, pair, entry price, technical metrics (RSI, EMA, Imbalance), JEV score, and Agentic natural-language rationale.
- [ ] **Task 5.2: Verification Checklist Generator**
  - Tool to verify that all trades have corresponding decision logs (satisfying the 40-pt and 35-pt criteria).

---

### Phase 6: Backtesting Harness & Shadow Testing Engine (`src/test`)
- [ ] **Task 6.1: Historical Backtest Runner (`run_backtest.py`)**
  - Replay archived Wallex market data.
  - Produce deterministic performance reports: Net PnL, Max Drawdown %, Win Rate, Sharpe Ratio.
- [ ] **Task 6.2: Live Shadow Paper-Trading Mode (`--mode shadow`)**
  - Connect live to Wallex streams and simulate executions against real-time books with zero capital risk.
  - Validate system stability and log output in production conditions.

---

### Phase 7: Live Deployment & Competition Operations
- [ ] **Task 7.1: Pre-Flight Production Checklist**
  - Verify dedicated 5M Toman credit account balance.
  - Test connectivity with Wallex API / Wallex MCP.
  - Confirm Toman liquidation timer is active.
- [ ] **Task 7.2: Engine Launch & Live Monitoring**
  - Launch engine with health checks and crash recovery.
  - Monitor drawdown metrics and active trade logs.

---

### Phase 8: Documentation, Demo Video & Packaging
- [ ] **Task 8.1: High-Signal `README.md`**
  - Architecture overview, AI reasoning role, risk management explanation, and 1-command reproduction guide for judges.
- [ ] **Task 8.2: 5-Minute Demo Video Script & Storyboard**
  - Walkthrough of project goals, live shadow execution, decision log inspection, and backtesting metrics.

---

## Multi-Agent Team Roles (`/team` Assignment Matrix)

| Agent Role | Subagent Type | Primary Ownership |
| :--- | :--- | :--- |
| **Team Lead / Architect** | `architect` / `lead` | System interfaces, risk parameters, JEV formulation, PR approvals |
| **API & Ingestion Specialist**| `executor` | `src/wallex/` client, rate-limiter, orderbook streaming |
| **Strategy & AI Specialist** | `executor` / `analyst` | `src/strategy/` Macro Agent, regime classifier, indicator math |
| **Risk & Sentinel Specialist** | `executor` / `debugger` | `src/risk/` JEV filter, drawdown lock, deadline liquidation |
| **Test & Backtest Specialist** | `verifier` / `test-engineer`| `src/test/` Backtesting simulator, shadow runner, unit tests |
| **Docs & Auditor Specialist** | `writer` | `decision_logs/`, `README.md`, Demo script |
