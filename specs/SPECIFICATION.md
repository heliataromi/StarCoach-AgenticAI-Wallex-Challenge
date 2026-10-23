# System Specification: Wallex Algorithmic Trading Engine (StarCoach Hackathon)

**Document Version:** 1.0.0  
**Specification Standard:** Speckit-Plan Standard  
**Target Platform:** Python 3.12+ (Asyncio, NumPy, Pandas, httpx)  
**Execution Environment:** Wallex Dedicated Credit Account (5,000,000 IRT)  
**Author:** Antigravity AI Consultant  

---

## 1. System Architecture & Module Boundaries

The system follows the **Hierarchical Asynchronous Architecture** decoupled into 5 decoupled subsystems:

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                           HIERARCHICAL SYSTEM TOPOLOGY                            │
└───────────────────────────────────────────────────────────────────────────────────┘

           ┌────────────────────────────────────────────────────────┐
           │   1. WALLEX CONNECTOR & DATA INGESTION (`src/wallex`)   │
           │   - Endpoint: `https://api.wallex.ir/mcp` (JSON-RPC 2.0)│
           │   - Tools: `wallex_basic_get_market_depth`,            │
           │     `wallex_basic_candle_history`, `wallex_spot_depth` │
           │   - Credit Sub-account Balance Tracker                 │
           │   - Rate Limit Token Bucket & Reconnect Sentinel       │
           └──────────────────────────┬─────────────────────────────┘
                                      │ MarketDataSnapshot
                                      ▼
           ┌────────────────────────────────────────────────────────┐
           │   2. MACRO AGENTIC BRAIN (`src/strategy/macro_agent`)  │
           │   - Latent Regime Classifier (Bullish, Ranging, Panic)  │
           │   - LLM Reasoning (Every 5-15m or market shift)         │
           │   - Emits: `MacroTradingPolicy`                        │
           └──────────────────────────┬─────────────────────────────┘
                                      │ Directs Policy
                                      ▼
           ┌────────────────────────────────────────────────────────┐
           │   3. JEV RISK & CAPITAL SENTINEL (`src/risk`)          │
           │   - Joint Expected Value Filter: E[V] > Fee + Alpha    │
           │   - Max Position Sizing (≤ 25% of 5M Toman capital)    │
           │   - Dynamic ATR Trailing Stop-Loss                     │
           │   - Drawdown Lock (< 10% Drawdown Ceiling)             │
           │   - Hard Deadline Toman Liquidation Routine            │
           └──────────────────────────┬─────────────────────────────┘
                                      │ Approved Trade Proposals
                                      ▼
           ┌────────────────────────────────────────────────────────┐
           │   4. MICRO EXECUTION ENGINE (`src/engine`)             │
           │   - Sub-50ms Async Order Dispatcher                    │
           │   - Limit / Market Order Execution via Wallex API/MCP  │
           │   - Order Fill & Partial Fill Tracker                  │
           └──────────────────────────┬─────────────────────────────┘
                                      │ ExecutedOrder Event
                                      ▼
           ┌────────────────────────────────────────────────────────┐
           │   5. AUDITOR & TEST HARNESS (`src/logger`, `src/test`) │
           │   - Immutable JSON/MD Decision Logging                 │
           │   - Deterministic Historical Backtester                │
           │   - Live Zero-Capital Shadow Testing Mode              │
           └────────────────────────────────────────────────────────┘
```

---

## 2. Core Data Contracts & Interfaces

### 2.1 Market Data Snapshot
```python
@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str  # e.g., "BTC_TMN", "ETH_TMN"
    timestamp: float  # Unix epoch in milliseconds
    best_bid: float
    best_ask: float
    spread_pct: float
    orderbook_bids: list[tuple[float, float]]  # [(price, volume), ...] Top 10
    orderbook_asks: list[tuple[float, float]]  # [(price, volume), ...] Top 10
    recent_candles: list[dict]  # 15m and 1h OHLCV
    imbalance_ratio: float  # (BidVol - AskVol) / (BidVol + AskVol)
```

### 2.2 Macro Trading Policy
```python
class MarketRegime(str, Enum):
    BULLISH_TREND = "BULLISH_TREND"
    RANGING_SIDEWAYS = "RANGING_SIDEWAYS"
    HIGH_VOLATILITY_PANIC = "HIGH_VOLATILITY_PANIC"
    PRE_DEADLINE_LIQUIDATION = "PRE_DEADLINE_LIQUIDATION"

@dataclass
class MacroTradingPolicy:
    regime: MarketRegime
    target_symbols: list[str]
    max_position_size_pct: float  # e.g., 0.20 (20% of 5M = 1M Toman)
    risk_multiplier: float        # 0.5 (conservative) to 1.5 (aggressive)
    agent_thesis: str             # Natural language explanation of market view
    valid_until: float            # Timestamp expiration
```

### 2.3 JEV Trade Proposal
```python
@dataclass
class TradeProposal:
    proposal_id: str
    symbol: str
    action: Literal["BUY", "SELL"]
    entry_price: float
    size_tmn: float
    stop_loss_price: float
    take_profit_price: float
    win_probability: float        # P(Win) estimated by model
    expected_gain_pct: float      # Potential upside %
    expected_loss_pct: float      # Potential downside %
    fee_pct: float                # Wallex maker/taker fee (e.g. 0.25% x 2 = 0.50%)
    expected_value: float         # JEV result
    is_approved: bool
    rejection_reason: str | None = None
```

---

## 3. Mathematical Strategy & Decision Engine

### 3.1 JEV (Joint Expected Value) Formulation
Every trade candidate must pass through the JEV filter before any execution order is sent:

$$\mathbb{E}[V] = \left[ P(\text{Win}) \times \left( \frac{\text{TakeProfit} - \text{Entry}}{\text{Entry}} \right) \right] - \left[ (1 - P(\text{Win})) \times \left( \frac{\text{Entry} - \text{StopLoss}}{\text{Entry}} \right) \right] - \text{Fees}$$

**Approval Condition:**
$$\mathbb{E}[V] \ge \theta_{\text{min}} \quad (\text{where } \theta_{\text{min}} = +0.0035 \text{ or } +0.35\% \text{ net expected edge})$$

If $\mathbb{E}[V] < \theta_{\text{min}}$, the proposal is **automatically rejected and logged**, preventing low-probability random trades that erode capital through churn and fees.

---

### 3.2 The 3 Strategy Regimes

#### Regime 1: Momentum & Trend Capture (Bullish Regime)
- **Conditions:**
  - Macro Agent confirms `BULLISH_TREND`.
  - EMA 9 > EMA 21 on 15m timeframe.
  - Order Book Imbalance $> +0.25$ (Bid volume dominates Ask volume).
  - 24h Volume > 20-period Average Volume.
- **Action:** Buy with size $= \min(20\% \text{ equity}, 1,000,000 \text{ TMN})$.
- **Exit Strategy:** Trailing Stop-Loss set at $\text{Entry} - (1.5 \times ATR_{14})$. Take-Profit adjusted dynamically as trend accelerates.

#### Regime 2: Micro-Structure Mean Reversion (Ranging Regime)
- **Conditions:**
  - Macro Agent confirms `RANGING_SIDEWAYS`.
  - Price touches Lower Bollinger Band ($2.0 \sigma$).
  - RSI(14) $< 32$ with positive divergence.
  - Strong Bid support wall detected within 0.3% of current price.
- **Action:** Buy with size $= 15\% \text{ equity}$.
- **Exit Strategy:** Limit sell placed at Middle Bollinger Band (SMA 20) with target net profit $+0.8\% \text{ to } +1.4\%$ after fees.

#### Regime 3: Capital Preservation & Panic Guard (Crash Regime)
- **Conditions:**
  - Macro Agent detects market dump, sudden spread blow-out (>1.5%), or BTC drop $>3\%$ in 1 hour.
- **Action:**
  - Immediately liquidate any open crypto positions to Toman at market/tight limit.
  - Block all new buy signals.
  - Maintain 100% Iranian Toman (TMN) balance.

---

## 4. Risk & Capital Management (Drawdown Sentinel)

### 4.1 Strict Allocation Boundaries
1. **Initial Capital:** 5,000,000 TMN.
2. **Max Position Limit:** No single trade may exceed 25% of current equity (max 1,250,000 TMN).
3. **Max Concurrent Positions:** Maximum 2 concurrent open positions across different permitted pairs.
4. **Hard Drawdown Lock (Circuit Breaker):**
   - If Account Equity drops below 4,500,000 TMN (10% Drawdown), all active positions are closed and trading is halted for review.
   - Preserves score for Criterion 2 (Drawdown Control - 30 pts).

### 4.2 Mandatory Deadline Liquidation Protocol
- **Deadline:** Challenge end time $T_{\text{end}}$.
- **$T_{\text{end}} - 4 \text{ hours}$:** Switch to `PRE_DEADLINE_LIQUIDATION`. No new buy orders allowed.
- **$T_{\text{end}} - 2 \text{ hours}$:** All open positions are executed as limit-take-liquidity orders to convert 100% of crypto into Toman (TMN).
- **$T_{\text{end}} - 30 \text{ minutes}$:** If any fractional crypto balance remains, fire market sell. Account state verified: Crypto balance = 0, Toman balance = 100%.

---

## 5. Decision Logging Specification

Every execution generates a corresponding JSON audit file in `decision_logs/` formatted as:

```json
{
  "log_version": "1.0",
  "trade_id": "WALLEX-TX-20261023-00042",
  "timestamp": "2026-10-23T14:45:12.184Z",
  "market_pair": "BTC_TMN",
  "action": "BUY",
  "order_type": "LIMIT",
  "price": 4251200000,
  "quantity": 0.000235,
  "total_tmn": 999032,
  "macro_state": {
    "regime": "BULLISH_TREND",
    "risk_multiplier": 1.0,
    "agent_summary": "Macro trend is upward following 4h EMA breakout. Orderbook bids show strong institutional absorption."
  },
  "micro_signals": {
    "rsi_14": 42.1,
    "ema_9": 4248000000,
    "ema_21": 4235000000,
    "orderbook_imbalance": 0.38,
    "atr_14": 45000000
  },
  "jev_evaluation": {
    "win_prob": 0.68,
    "reward_to_risk": 1.75,
    "expected_value_pct": 0.0084,
    "threshold_pct": 0.0035,
    "approved": true
  },
  "risk_guardrails": {
    "stop_loss_price": 4183700000,
    "stop_loss_pct": 1.58,
    "portfolio_drawdown_current": 1.2
  }
}
```

---

## 6. Backtesting & Shadow Testing Architecture

### 6.1 Backtesting Module (`src/test/backtester.py`)
- Ingests CSV/JSON historical OHLCV and orderbook snapshots from `data/historical/`.
- Simulates realistic fill mechanics (slippage model based on orderbook volume depth, Wallex maker/taker fees).
- Outputs comprehensive benchmark metrics:
  - Total Return (%)
  - Maximum Drawdown (MDD %)
  - Win Rate (%) & Profit Factor
  - Total Trades & Average Hold Duration
  - Sharpe & Sortino Ratios

### 6.2 Shadow Mode (`--mode shadow`)
- Connects live to Wallex ticker and depth endpoints.
- Runs full decision loop and JEV calculations in real-time.
- Tracks virtual portfolio and virtual fills matching actual live bid/ask books.
- Allows team to verify performance before toggling `--mode live`.

---

## 7. Verification & Acceptance Testing Protocol

| Verification Phase | Command | Success Metric |
| :--- | :--- | :--- |
| **Unit Tests** | `pytest tests/unit/ -v` | 100% pass across JEV math, risk caps, and indicator calculations |
| **Backtest Verification** | `python run_backtest.py --sample` | Deterministic metrics match baseline test vector |
| **Shadow Mode Dry-Run** | `python run_engine.py --mode shadow --duration 300` | 5-minute live stream without crashes; decision logs generated |
| **Circuit Breaker Test** | `pytest tests/test_circuit_breaker.py` | Stop-loss and 10% drawdown lock reliably trigger liquidation |
| **Toman Liquidation Test**| `pytest tests/test_deadline_liquidation.py` | 100% crypto balance converts to Toman before deadline |
