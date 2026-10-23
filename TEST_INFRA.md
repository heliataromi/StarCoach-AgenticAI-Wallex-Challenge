# TEST_INFRA.md — Wallex Algorithmic Trading Agent Test Infrastructure

**Version:** 1.0.0  
**Project:** Autonomous Quant-Grade Algorithmic Trading Agent & Engine (StarCoach Wallex Challenge)  
**Standard:** Dual-Track Opaque-Box E2E Testing Protocol  
**Python Version:** Python 3.10+ (Standard Pytest, Zero Third-Party Test Plugin Dependencies)

---

## 1. Testing Philosophy & Architecture

This repository adopts the **Dual-Track Testing Architecture**, where the test suite is engineered independently from the implementation code according to explicit interface contracts, mathematical specifications, and competition rules:

1. **Opaque-Box Requirement-Driven Verification:**
   Tests verify observable external behavior, state transitions, output data contracts, and audit trails without coupling to internal private methods.
2. **Zero External Network Dependencies:**
   All network requests to Wallex REST and Wallex MCP (`https://api.wallex.ir/mcp`) are intercepted and simulated by a high-fidelity in-memory `MockWallexClient` and deterministic fixture ecosystem. Tests execute 100% offline.
3. **Deterministic Clock & Time Manipulation:**
   Time-sensitive behaviors (rate limiting token refill, TTL order expirations, and the 4-stage pre-deadline liquidation schedule) are driven by a deterministic clock fixture (`FakeClock`) rather than non-deterministic `time.sleep()`.
4. **Standard Pytest Portability:**
   To adhere to the **Ponytail** engineering discipline and ensure execution on any standard Python environment without `pytest-asyncio` plugin dependency, all asynchronous operations are executed cleanly using `asyncio.run()` within standard test functions.
5. **Progressive Testability & Contract Shims:**
   Tests are written against the interface contracts defined in `PROJECT.md` and `SPECIFICATION.md`. Fixtures in `tests/conftest.py` dynamically bind to `src/` modules when present, and provide contract-compliant reference models and calculation oracles to guarantee that the test suite is verifiable at every stage.

---

## 2. Test Directory Layout

```
tests/
├── AGENTS.md                                # Testing scope and conventions
├── conftest.py                              # Shared fixtures, MockWallexClient, contracts, and math oracles
├── unit/                                    # Unit tests (M1-M6 individual modules)
│   ├── test_wallex_client.py
│   ├── test_risk_and_jev.py
│   ├── test_strategy.py
│   ├── test_execution_engine.py
│   └── test_decision_auditor.py
└── e2e/                                     # 4-Tier Opaque-Box E2E Test Suite
    ├── test_tier1_features.py               # Tier 1: Isolated Happy Path Tests (R1 to R6)
    ├── test_tier2_boundaries.py             # Tier 2: Boundary, Limit, & Corner Cases
    ├── test_tier3_combinations.py           # Tier 3: Cross-Module Pipeline Integration
    └── test_tier4_scenarios.py              # Tier 4: Multi-Day Realistic Lifecycle & Stress Scenarios
```

---

## 3. Four-Tier Test Suite Specification

### Tier 1: Isolated Feature Contract Tests (`tests/e2e/test_tier1_features.py`)
Focuses on the primary happy path behavior of each requirement (R1 through R6) in isolation:
- **R1 (Market Ingestion & Client):** Verifies orderbook depth streaming, top-10 bid/ask extraction, spread % calculation, orderbook imbalance ratio $\in [-1.0, 1.0]$, and balance retrieval for the 5M TMN dedicated account.
- **R2 (Strategy Engine & Regimes):** Validates macro trading policy generation, Regime 1 (Momentum breakout), Regime 2 (Mean reversion scalping), and Regime 3 (Capital preservation cash guard).
- **R3 (JEV Risk Gate & Drawdown Sentinel):** Verifies JEV math filter approval ($\mathbb{E}[V] \ge +0.35\%$), position sizing cap ($\le 20\%$ equity), and dynamic ATR trailing stop-loss.
- **R4 (Micro Execution Engine):** Tests async order dispatching, fill tracking (`PENDING` -> `FILLED`), fill price and fee accounting.
- **R5 (Backtesting & Shadow Testing):** Verifies historical replay metrics (Net Return, Max Drawdown, Sharpe) and live shadow runner paper-trading with zero capital risk.
- **R6 (Decision Auditor):** Verifies structured, immutable JSON audit logging to `decision_logs/` and cross-verifies trade execution against logs.

### Tier 2: Boundary, Limit, & Corner Cases (`tests/e2e/test_tier2_boundaries.py`)
Validates system robustness under extreme limits and edge conditions:
- **Rate Limit Boundary:** Verifies token bucket capacity (30 req / 10s) and throttling when burst exceeds threshold.
- **Zero Balance Guard:** Asserts that BUY orders are rejected when available Toman balance is 0.
- **Exact Position Cap:** Verifies single trade size at exactly 20.00% is allowed; $>20.00\%$ is blocked/clamped.
- **Max Concurrent Positions:** Enforces maximum 2 concurrent open positions.
- **10% Circuit Breaker Trip:** Asserts that an account equity drop to $\le 4,500,000$ TMN (10.0% drawdown) immediately trips the circuit breaker, halts trading, and closes positions.
- **4-Stage Pre-Deadline Liquidation Schedule:**
  - $T - 4\text{h}$: Inhibit all BUY signals (`PRE_DEADLINE_LIQUIDATION`).
  - $T - 2\text{h}$: Orderly limit-sell liquidation of all crypto assets.
  - $T - 30\text{m}$: Dust sweep of fractional balances via market order.
  - $T - 15\text{m}$: Strict assertion that Crypto Balance = 0 and Toman Balance = 100%.
- **Exact JEV Hurdle:** Verifies $\mathbb{E}[V] = +0.00349$ is rejected, $\mathbb{E}[V] = +0.00350$ is approved.
- **Exact Reward-to-Risk:** Verifies $R:R = 1.49$ is rejected, $1.50$ is approved.
- **Extreme Orderbook Conditions:** Handles 100% bid wall ($+1.0$), 100% ask wall ($-1.0$), and inverted/zero spread book without crashes.

### Tier 3: Cross-Module Pipeline Integration (`tests/e2e/test_tier3_combinations.py`)
Validates complete asynchronous data flow across subsystem boundaries:
- **Pipeline 1 (Bullish Trend):** Snapshot -> Macro Agent (`BULLISH_TREND`) -> Momentum Signal -> JEV Gate Approval -> Dispatcher Execution -> Immutable Decision Log.
- **Pipeline 2 (Ranging Scalp):** Snapshot -> Mean Reversion Signal -> JEV Approval -> Order Filled -> Price reaches Middle BB -> Take Profit executed -> Realized PnL logged.
- **Pipeline 3 (JEV Gate Interception):** Sub-threshold candidate signal intercepted by JEV Gate -> Zero order dispatched -> Rejection audit log recorded.
- **Pipeline 4 (ATR Dynamic Stop-Loss):** Active trade -> Sudden price drop exceeding $1.2 \times ATR_{14}$ -> Dynamic trailing stop triggers sell -> Position closed safely.
- **Pipeline 5 (TTL Order Cancellation):** Unfilled limit order after TTL (e.g. 20s) automatically cancelled and exchange balance reconciled.
- **Pipeline 6 (100% Decision Audit Verification):** Batch of simulated orders verified against `decision_logs/` with zero missing logs.

### Tier 4: Realistic Multi-Day Scenarios (`tests/e2e/test_tier4_scenarios.py`)
Validates realistic end-to-end system operations and stress lifecycles:
- **Scenario 1 (Full 3-Day Hackathon Simulation):** Replays 72 hours across 5M TMN capital through Bullish, Ranging, and Pre-Deadline Liquidation regimes, asserting positive return, drawdown $<10\%$, 100% Toman cash at end, and 100% decision logs.
- **Scenario 2 (Market Flash Crash Panic):** 5% BTC dump in 30 minutes with spread blowout triggers Regime 3 Capital Preservation, closes open crypto positions, halts buying, and preserves capital.
- **Scenario 3 (Circuit Breaker Stress):** Consecutive loss sequence drops equity to 4.5M TMN, triggering instant halt and safety lock.
- **Scenario 4 (Live Shadow Stream Soak Test):** Multi-tick continuous orderbook streaming in Shadow Mode verifying zero capital deployed, zero exceptions, and stable metric tracking.

---

## 4. Authoritative Mathematical Oracles

Every test assertion is derived from explicit mathematical formulas specified in `PROJECT.md`, `CHALLENGE.md`, and `SPECIFICATION.md`:

| Metric | Formula | Hurdle / Threshold |
| :--- | :--- | :--- |
| **Joint Expected Value (JEV)** | $\mathbb{E}[V] = P(\text{Win})\Delta\text{Gain} - (1-P)\Delta\text{Loss} - \text{Fees}$ | $\ge +0.0035$ (+0.35%) |
| **Reward-to-Risk Ratio** | $\frac{\text{Gain}}{\text{Loss}} = \frac{\text{TP} - \text{Entry}}{\text{Entry} - \text{SL}}$ | $\ge 1.50$ |
| **Position Sizing Cap** | $\text{Size}_{\text{TMN}} \le \min(0.20 \times \text{Equity}, 1,000,000 \text{ TMN})$ | Max 20% equity |
| **Dynamic ATR Stop Distance** | $\text{StopDistance} = \min(1.2 \times ATR_{14}, 0.020 \times \text{Price})$ | Dynamic trailing |
| **Account Drawdown** | $\text{DD} = \frac{\text{PeakEquity} - \text{CurrentEquity}}{\text{PeakEquity}}$ | $\le 0.100$ (10.0% Max) |
| **Order Book Imbalance** | $I = \frac{\sum \text{BidVol} - \sum \text{AskVol}}{\sum \text{BidVol} + \sum \text{AskVol}}$ | Range $[-1.0, +1.0]$ |
| **Rate Limit Token Bucket** | Refill rate $= \frac{30 \text{ tokens}}{10 \text{ seconds}} = 3.0 \text{ tokens/sec}$ | Max 30 tokens |

---

## 5. Execution Commands & Verification

### Run Entire E2E Test Suite:
```bash
pytest tests/e2e/ -v
```

### Run by Specific Tier:
```bash
# Tier 1: Isolated Feature Contract Tests
pytest tests/e2e/test_tier1_features.py -v

# Tier 2: Boundary, Limit, & Corner Cases
pytest tests/e2e/test_tier2_boundaries.py -v

# Tier 3: Cross-Module Pipeline Integration
pytest tests/e2e/test_tier3_combinations.py -v

# Tier 4: Multi-Day Realistic Scenarios
pytest tests/e2e/test_tier4_scenarios.py -v
```

### Run with Summary & Timing:
```bash
pytest tests/e2e/ --durations=10 -v
```
