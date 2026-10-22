# Simplified Architecture, Strategy Algorithm, Reproducibility & Decision Models (JEV/JEPA)

**Author:** Antigravity AI Consultant  
**Project:** StarCoach Wallex Hackathon Challenge  
**Date:** 2026-10-22  

---

## 1. The Middle-Ground Architecture Explained Simply

Think of our trading engine as an **institutional trading desk with two key members**:

```
 ┌─────────────────────────────────────────────────────────────┐
 │  1. THE STRATEGIST (Macro Brain / Agentic AI)               │
 │     "The Chess Grandmaster"                                 │
 │     - Looks at the big picture every 5-15 minutes           │
 │     - Identifies the market climate (Sunny, Stormy, Rainy)  │
 │     - Sets the playbook: which coin, how much risk to take  │
 └──────────────────────────────┬──────────────────────────────┘
                                │ Hands over the Playbook (Policy JSON)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  2. THE EXECUTION TRADER (Micro Sentinel / Python Engine)   │
 │     "The Quick-Draw Gunslinger"                             │
 │     - Watches the Wallex live order book at <50ms speed     │
 │     - Executes orders the instant market hits playbook price│
 │     - Never freezes, never hesitates, zero latency lag      │
 │     - Strictly stops losses if price drops below guardrail  │
 └──────────────────────────────┬──────────────────────────────┘
                                │ Records execution snapshot
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  3. THE AUDITOR (Decision Logger)                           │
 │     "The Compliance Officer"                                │
 │     - Stamps every single trade with why & how it happened  │
 │     - Generates judge-ready proof for all 300 points        │
 └─────────────────────────────────────────────────────────────┘
```

### Why this solves our biggest challenge:
If the AI had to think about every single price change (Method 2 or 3), it would take **2 to 5 seconds per tick**. By the time the AI says "BUY", the market price on Wallex has already moved!
With this middle-ground approach:
- The **AI does all the high-level brainwork and contextual reasoning** (qualifying for the 35 points of Real AI and 40 points of Strategy Logic).
- The **Python engine executes with lightning speed** (securing optimal entry prices, low drawdown, and zero API rate-limit crashes).

---

## 2. What Specific Algorithm Will We Use?

For a 3-day hackathon on Wallex with 5,000,000 Tomans spot credit, we cannot short-sell (we only hold crypto or Iranian Toman). Therefore, the algorithm must be an **Adaptive Multi-Regime Algorithm**:

### Core Trading Logic:
The algorithm switches between **3 distinct modes** governed by the Macro Agent:

1. **Regime 1: Momentum & Trend Capture (Trending Bullish)**
   - **Signal:** EMA 9 crossing above EMA 21 + Volume spike + positive Order Book Bid/Ask Imbalance (more buyers than sellers).
   - **Execution:** Buy allowed coin (e.g., BTC/TMN), set dynamic Trailing Stop-Loss at $1.2 \times ATR$ (Average True Range).
   - **Goal:** Ride multi-hour upward trends to maximize Net Return (90 pts).

2. **Regime 2: Order Book Spread & Mean-Reversion (Sideways / Ranging Market)**
   - **Signal:** Price touches lower Bollinger Band + RSI < 35 with strong bid wall on Wallex orderbook.
   - **Execution:** Buy at support, place limit sell order at middle Bollinger Band (+0.8% to +1.5% profit, well above Wallex trading fees).
   - **Goal:** Steady, consistent small wins keeping the algorithm active (15 pts) without risking major capital.

3. **Regime 3: Capital Preservation Guard (Market Crash / High-Volatility Panic)**
   - **Signal:** Sharp drop across markets, RSI breakdown, or Macro Agent detecting regime shift.
   - **Execution:** **Liquidate immediately to 100% Toman (TMN). Stop all new buys.**
   - **Goal:** Defend against Drawdown (30 pts). In a crash, being 100% in cash gives you top-tier ranking while other teams lose capital!

4. **Hard Red-Line Routine: The Deadline Liquidation Rule**
   - 4 hours before the competition ends, the engine enters liquidation mode: all open positions are sold to Toman, guaranteeing 100% realized returns as required by Section 5 of `CHALLENGE.md`.

---

## 3. How Do We Make It 100% Reproducible for Judges?

Judges award **25 points for Repeatability/Reproducibility**, **20 points for Backtest/Shadow testing**, and **40 points for Strategy Logic**. Here is how we ensure reproducibility:

1. **Deterministic State Machine:**
   - The strategy uses clear mathematical state transitions. Every trade decision is a pure function of:
     $$\text{Action} = f(\text{Macro Policy}, \text{Order Book State}, \text{Technical Indicators}, \text{Risk Limits})$$
   - No random seeds, no opaque black boxes.
2. **The 1-Command Backtesting Suite (`run_backtest.py`):**
   - We package archived Wallex historical candle & orderbook data in `data/historical/`.
   - A single command:
     ```bash
     python run_backtest.py --pair BTC_TMN --start 2026-09-01 --end 2026-10-01
     ```
     Generates an exact tabular report: Total Trades, Win Rate %, Profit Factor, Max Drawdown %, and Net Return %. A judge running this gets identical numbers every time.
3. **Shadow Testing Mode (`--mode shadow`):**
   - Connects live to Wallex API / Wallex MCP.
   - Simulates orders against the real live order book without placing actual orders on the exchange.
   - Proves to judges that the system performs in live conditions before touching real funds.
4. **Structured Decision Logs (`decision_logs/`):**
   - For every single trade, an immutable JSON file is created:
     ```json
     {
       "timestamp": "2026-10-23T14:32:00Z",
       "trade_id": "TX_1042",
       "market": "BTC_TMN",
       "action": "BUY",
       "price": 4250000000,
       "amount_tmn": 1000000,
       "macro_regime": "BULLISH_TREND",
       "signals": { "rsi": 28.4, "ema_cross": true, "ob_imbalance": 0.64 },
       "risk_assessment": { "stop_loss": 4180000000, "portfolio_risk_pct": 2.1 },
       "agent_rationale": "Strong bid support detected on Wallex orderbook with oversold RSI bounce while Macro Agent confirmed bullish trend continuation."
     }
     ```

---

## 4. Analysis of Decision Models (JEV / JEPA) in this Architecture

### A. What is JEV (Joint Expected Value) / Decision-Theoretic EV?
In quantitative finance and algorithmic game theory, **JEV (Joint Expected Value)** models evaluate decisions not on simple signals ("RSI is low"), but on mathematical expectancy across probability distributions:

$$\mathbb{E}[V] = P(\text{Win}) \times \text{Expected Gain} - P(\text{Loss}) \times \text{Expected Loss} - \text{Exchange Fees}$$

#### Can we use JEV in our architecture? **YES, absolutely! It is a massive competitive advantage.**
- **How it fits:**
  1. Instead of the Macro Agent simply saying "Buy BTC", the agent acts as an **Evidential / Probabilistic Estimator**.
  2. Given current volatility, depth, and indicators, the model estimates \(P(\text{Win})\) and expected payoff ratio.
  3. The Micro Sentinel computes \(\mathbb{E}[V]\). If \(\mathbb{E}[V] \le \text{Threshold}\) (e.g. expected return < 0.8% net of fees), the trade is **rejected**.
  4. **Why judges will love this:** It elevates the project from basic hobbyist bot trading to **quant-grade mathematical decision theory**. It directly satisfies Criterion 1 (Strategy Logic - 40 pts) and Criterion 3 (Risk Management - 30 pts).

---

### B. What is JEPA (Joint-Embedding Predictive Architecture)?
Introduced by Yann LeCun, JEPA avoids predicting exact low-level details (e.g. noisy tick prices) and instead **predicts abstract representations in an embedding space**.

#### Can we use JEPA concepts in our architecture?
- **Practical Assessment for a 3-day Hackathon:**
  - Training a full deep neural JEPA from scratch on crypto order books in 3 days carries high overfitting risk and operational fragility.
  - **However, a "JEPA-Inspired Latent Regime Classifier" is brilliantly viable:**
    - We map multi-dimensional market inputs (Price momentum, Orderbook bid/ask slope, Volume acceleration, Volatility) into a **compact latent State Vector**.
    - The Agentic LLM or an embedding model predicts whether the state vector is transitioning toward *Expansion (Profit)*, *Contraction (Range)*, or *Turbulence (Drawdown)*.
    - This provides an intelligent abstraction layer that shields the algorithm from market noise.

---

## 5. Architectural Verdict & Synthesis

Incorporating **JEV (Joint Expected Value)** into our **Hierarchical Asynchronous Architecture** gives us the ultimate formula:

```
[ Macro Agent (LLM + Latent Regime Embedding) ]
                  │
                  ▼
[ JEV Decision Gate (Joint Expected Value Evaluator) ]
   "Does E[Value] > Minimum Alpha Threshold + Fees?"
                  │ If YES
                  ▼
[ Micro Execution Sentinel (<50ms Python Async) ]
                  │
                  ▼
[ Structured Decision Logger (Complete Math + Natural Language Proof) ]
```

This delivers:
1. **Speed & Zero Slippage** (<50ms execution).
2. **True AI Sophistication** (Latent regime classification + Agentic reasoning).
3. **Rigorous Decision Theory** (JEV Expected Value filter).
4. **Guaranteed Reproducibility** (Deterministic backtest & shadow replay).
5. **Rock-solid Risk Control** (Hard drawdown lock & 100% Toman liquidation).
