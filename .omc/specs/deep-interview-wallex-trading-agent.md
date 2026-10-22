# Deep Interview Spec: Wallex Algorithmic Trading Agent

## Metadata
- Interview ID: wallex-agentic-trading-2026-10-22
- Rounds: 3
- Final Ambiguity Score: 3.8%
- Type: greenfield
- Generated: 2026-10-22T19:37:00Z
- Threshold: 0.2 (20%)
- Threshold Source: default
- Initial Context Summarized: yes
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.98 | 0.40 | 0.392 |
| Constraint Clarity | 0.95 | 0.30 | 0.285 |
| Success Criteria | 0.95 | 0.30 | 0.285 |
| **Total Clarity** | | | **0.962** |
| **Ambiguity** | | | **3.8%** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| `market-ingestion-wallex-client` | active | Wallex REST & MCP client, orderbook & ticker stream, rate limits | Covered in R1, Spec §3.1 |
| `agentic-strategy-engine` | active | Hierarchical Asynchronous Brain (Macro LLM + Latent Regime Classifier) | Covered in R2, Spec §3.2 |
| `risk-capital-sentinel` | active | JEV Decision Gate, Drawdown Sentinel, Stop-Loss, Toman Liquidation | Covered in R3, Spec §3.3 |
| `backtesting-shadow-harness` | active | Deterministic Historical Simulator & Live Shadow Paper-Trading | Covered in R4, Spec §3.4 |
| `decision-logging-deliverables` | active | Structured JSON/MD trade audit logs, README, Demo script | Covered in R5, Spec §3.5 |

## Goal
Build a high-performance, fully autonomous Algorithmic Trading Agent and Engine for the Wallex Hackathon Challenge, utilizing Python 3.12, Wallex REST API / Wallex MCP, a Hierarchical Asynchronous Agentic Architecture with a JEV (Joint Expected Value) decision gate, and an adaptive 3-regime trading strategy operating on a 5,000,000 Toman dedicated account. The engine maximizes net returns, contains drawdown under strict risk barriers, logs immutable rationales for 100% of trades, and liquidates all crypto holdings to Iranian Toman (TMN) prior to the competition deadline.

## Constraints
- **Language & Runtime:** Python 3.12+ (Asyncio, NumPy/Pandas, httpx/aiohttp).
- **Execution Account:** Dedicated Wallex 5M Toman credit account (no shorting; spot only).
- **Red Lines (Non-Negotiable):**
  - Zero manual trading (all trades algorithmic).
  - Zero self-trading or wash trading.
  - Operations restricted strictly to authorized Wallex credit trading markets.
  - 100% portfolio realization in Iranian Toman (TMN) at challenge close (no open coin inventory).
- **Engineering Discipline:** Ponytail YAGNI ladder (minimal dependencies, deletion over addition, minimal runnable checks) and Git Workflow (dual base branches `main`/`develop`, 3-gate approvals).

## Non-Goals
- Graphical User Interface (UI/UX) — explicitly given 0 weight in hackathon rubric.
- High-frequency microsecond arbitrage across external international exchanges (not supported on Wallex credit account).
- Deep complex neural network training from scratch in a 3-day hackathon (avoiding operational fragility).

## Acceptance Criteria
- [ ] 100% automated trade execution via Wallex API / Wallex MCP without human intervention
- [ ] JEV Decision Gate enforces positive mathematical expectancy ($\mathbb{E}[V] > \text{fee} + \text{alpha threshold}$) before orders are placed
- [ ] Maximum position size limit (max 20-30% capital per trade) and dynamic trailing stop-loss actively enforced
- [ ] Hard drawdown lock halts new entries if account drawdown reaches 10%
- [ ] Mandatory liquidation routine automatically converts 100% of holdings to Toman 4 hours prior to deadline
- [ ] Structured decision logs (`decision_logs/`) created for 100% of executed orders with technical metrics + agent rationale
- [ ] 1-command backtesting harness (`python run_backtest.py`) producing deterministic metrics on historical data
- [ ] Live shadow testing mode (`--mode shadow`) validating real-time orderbook execution with zero capital risk
- [ ] Clean documentation (`README.md`, `AGENT.md`) with reproducible step-by-step verification commands

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| Can LLMs trade every millisecond tick directly? | LLM inference latency (1.5-4s) causes severe slippage and rate-limit crashes. | Decoupled into Macro Agent (strategic policy every 5-15 min) + Micro Python Engine (<50ms execution). |
| How to prove real AI usage without simple `if/else`? | Judges give 35 pts for real AI. Simple indicators forfeit this score. | AI Agent performs latent regime classification, adapts risk policy, and powers the JEV probability estimator. |
| How to handle the requirement of realization to Toman? | Leaving holdings in crypto at deadline subjects score to post-challenge coin price volatility. | Scheduled hard liquidation routine 4h before deadline converts all crypto balances to Toman. |

## Technical Context
- Repository: `git@github.com:heliataromi/StarCoach-AgenticAI-Wallex-Challenge.git`
- Core Modules: `wallex/` (Client & MCP), `strategy/` (Macro Agent & Regimes), `risk/` (JEV Gate & Drawdown Sentinel), `engine/` (Micro Async Executor), `backtest/` (Simulator & Shadow Mode), `logger/` (Audit trails).

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| `MarketDataSnapshot` | Core Domain | symbol, timestamp, bid_price, ask_price, spread, orderbook_depth, ohlcv | Generated by WallexClient, consumed by Strategy & Sentinel |
| `MacroTradingPolicy` | Core Domain | regime, target_pairs, risk_multiplier, max_allocation_pct, sentiment_score, valid_until | Generated by MacroBrain, directs MicroSentinel |
| `TradeProposal` | Core Domain | pair, action, entry_price, size_tmn, stop_loss, target_tp, jev_expected_value | Produced by Strategy, vetted by JEV Gate |
| `ExecutedOrder` | Core Domain | order_id, wallex_ref, timestamp, pair, action, fill_price, filled_qty, fee_tmn | Returned by WallexClient on execution |
| `DecisionLogEntry` | Supporting | trade_id, timestamp, signals, macro_rationale, risk_metrics, jev_score | Written to `decision_logs/` by DecisionAuditor |
| `AccountState` | Core Domain | initial_balance (5M), current_balance_tmn, crypto_holdings, total_equity, drawdown_pct | Monitored by DrawdownSentinel |
