# Skills Selection Matrix: Wallex Algorithmic Trading Project

**Author:** Antigravity AI Consultant  
**Project:** StarCoach Wallex Hackathon Challenge  
**Date:** 2026-10-22  
**Purpose:** Formal breakdown and mapping of all skills used across every phase of the project lifecycle.

---

## 1. Skill Architecture Overview

To achieve institutional-grade performance and win the hackathon, skills are organized into **three distinct operational tiers**:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           SKILL ECOSYSTEM ARCHITECTURE                          │
└─────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 1: GLOBAL GOVERNANCE & ORCHESTRATION SKILLS                       │
 │ (System-level workflows enforcing engineering and team discipline)    │
 ├────────────────────────────────────────────────────────────────────────┤
 │ • /git-workflow         : Dual base branches (master/develop), 3 gates │
 │ • /ponytail             : YAGNI ladder, lean code, minimal check       │
 │ • /team & /teamwork     : Multi-agent coordination and staged pipeline │
 │ • /deepinit             : Hierarchical AGENTS.md context propagation   │
 │ • /deep-interview       : Socratic mathematical ambiguity gating       │
 │ • /tdd & /verify        : Tracer-bullet test-driven math verification  │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ Governs
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 2: IN-REPO SPECIALIZED DOMAIN SKILLS (Authored via /skill-writer) │
 │ (Custom standard skills living in `.agents/skills/`)                   │
 ├────────────────────────────────────────────────────────────────────────┤
 │ • wallex-mcp-integration: Wallex REST/MCP, rate limiter, depth streamer│
 │ • jev-decision-evaluator: Joint Expected Value filter (E[V] > +0.35%)  │
 │ • drawdown-sentinel     : Sizing (≤20%), stop-loss, Toman liquidation  │
 │ • backtest-shadow-runner: Deterministic backtester & shadow runner     │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ Complemented by
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 3: OPEN SKILLS ECOSYSTEM (Discovered via /find-skills)            │
 │ (High-reputation packages from skills.sh)                              │
 ├────────────────────────────────────────────────────────────────────────┤
 │ • wshobson/agents@backtesting-frameworks (16.6K installs)              │
 │ • okx/agent-skills@okx-cex-trade         (10.2K installs)              │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase-by-Phase Skill Mapping Matrix

| Project Phase | Primary Skills | Role & Exact Responsibility |
| :--- | :--- | :--- |
| **Phase 0: Foundation & Repo Setup** | `/git-workflow`, `/deepinit`, `/ponytail` | Initialize `master` & `develop` branches, configure `.gitignore`, generate hierarchical `AGENTS.md` files. |
| **Phase 1: Wallex Client & Ingestion** | `wallex-mcp-integration`, `okx-cex-trade` | Authenticate API/MCP, implement token bucket rate limiter, stream live orderbook depth and OHLCV. |
| **Phase 2: Risk Sentinel & JEV Gate** | `jev-decision-evaluator`, `drawdown-sentinel`, `/tdd` | Implement mathematical expectancy filter, enforce 20% position cap, build Circuit Breaker and deadline Toman liquidation. |
| **Phase 3: Macro Brain & Strategy** | `/team` (`analyst`/`architect`), `jev-decision-evaluator` | Macro Agent reasoning, latent regime classifier (Bullish, Ranging, Crash), indicator signal generation. |
| **Phase 4: Micro Execution Engine** | `wallex-mcp-integration`, `/ponytail` | Sub-50ms async order execution dispatcher, order lifecycle management without latency lag. |
| **Phase 5: Decision Logging & Audit** | `/ponytail`, `drawdown-sentinel` | Immutable JSON audit logs for 100% of trades, proving non-manual AI execution for the 40-pt and 35-pt criteria. |
| **Phase 6: Backtesting & Shadow Testing**| `backtest-shadow-runner`, `backtesting-frameworks`, `/verify` | Reproducible historical backtesting CLI and live zero-capital shadow paper-trading mode. |
| **Phase 7: Live Deployment & Ops** | `/team` (`executor`), `drawdown-sentinel` | Pre-flight account balance check, engine launch, active drawdown monitoring. |
| **Phase 8: Deliverables & Demo** | `/team` (`writer`) | Technical `README.md`, judge walkthrough guide, 5-minute demo video script. |

---

## 3. Detailed Profile of In-Repo Custom Skills

### 1. `wallex-mcp-integration` (`.agents/skills/wallex-mcp-integration/SKILL.md`)
- **Why Needed:** Connects python trading agents directly to Wallex REST and Wallex MCP endpoints with zero credential leakage, enforces token bucket rate-limiting (30 req / 10s), handles orderbook parsing and order submission.

### 2. `jev-decision-evaluator` (`.agents/skills/jev-decision-evaluator/SKILL.md`)
- **Why Needed:** Implements the core mathematical decision theory:
  $$\mathbb{E}[V] = P(\text{Win}) \times \text{Gain} - (1 - P) \times \text{Loss} - \text{Fees}$$
  Guarantees that every executed trade has an expected value $\ge +0.35\%$, eliminating random trades.

### 3. `drawdown-sentinel` (`.agents/skills/drawdown-sentinel/SKILL.md`)
- **Why Needed:** Directly secures the **30 points for Drawdown Control** and enforces the mandatory **Toman Realization Rule** (Section 5 of `CHALLENGE.md`) by auto-liquidating all crypto to Toman 4 hours before the hackathon deadline.

### 4. `backtest-shadow-runner` (`.agents/skills/backtest-shadow-runner/SKILL.md`)
- **Why Needed:** Secures the **20 points for Backtest/Shadow** and **25 points for Repeatability** by providing a deterministic, 1-command historical backtest runner and live shadow paper-trading harness.
