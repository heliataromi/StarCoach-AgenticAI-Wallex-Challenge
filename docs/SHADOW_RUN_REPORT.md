# StarCoach Wallex Challenge: Live Shadow Paper-Trading Report
**Operating Mode:** Live Real-Time Shadow Paper-Trading  
**Live Data Provider:** Wallex Production API / MCP (`https://api.wallex.ir`)  
**AI Inference Provider:** Google AI Studio Gemini Free Tier  
**Execution Timestamp:** 2026-10-23T22:44:57+03:30  
**Virtual Capital:** 5,000,000 Iranian Toman (TMN)  
**Execution Risk:** 0.00% Live Capital Risk (Simulated Paper Matching on Real Live Orderbooks)  

---

## 1. Executive Summary

The trading engine successfully connected to the live Wallex exchange infrastructure, streaming real-time orderbook depth and historical candle feeds across all permitted trading pairs (`BTCTMN`, `ETHTMN`, `USDTTMN`).

| Metric | Measured Live Result | Protocol Invariant | Status |
| :--- | :---: | :---: | :---: |
| **Shadow Cycles Executed** | 5 Polling Cycles | Real-Time Live Orderbook Streaming | **Verified** |
| **Live Exchange Connectivity** | Wallex REST & MCP API | Latency: ~180ms per Depth Fetch | **Connected** |
| **Virtual Starting Capital** | 5,000,000 TMN | Dedicated Hackathon Virtual Base | Exact Base |
| **Virtual Ending Equity** | 5,000,000 TMN | 100% Capital Preservation | **Protected** |
| **Real Live Orderbook Prices** | BTC: ~21.81B TMN, ETH: ~643M TMN, USDT: ~268k TMN | Live Real-Time Exchange Data | **Audited** |
| **AI Inference Calls** | 3 / 3 Successful | Google AI Studio Free Tier | **\$0.0000 Spend** |
| **Audit Integrity** | 100% Verified | SHA-256 Tamper-Evident Chaining | **Intact** |

---

## 2. Live Market Conditions & Multi-Agent Debate Adjudications

During the shadow execution, the dialectical debate system evaluated real live Wallex market depth and price action:

### A. `BTCTMN` Live Adjudication
* **Live Market Price:** 21,811,000,050 TMN
* **Live Orderbook Imbalance (OBI):** `-0.98` (Extreme ask-side sell wall dominating live bids)
* **Arbiter Ruling:** `DEFENSIVE_HALT` (Confidence: 0.95)
* **Bull Thesis:** Score = 0.20 | Catalyst: Deeply oversold RSI bounce potential (30.7)
* **Bear Thesis:** Score = 0.80 | Hazard: EMA9 (21.83B) below EMA21 (21.85B) downward pressure
* **Arbiter Synthesis:**
  > *"Bear conviction is dominant at 0.80, backed by severe downside risks including EMA bearish alignment, weak RSI momentum failing to rally, and extreme ask book depth dominance (OBI: -0.98) signaling aggressive institutional selling. Round-trip fee friction and downside hazards far outweigh the meager 0.20 bull score, mandating a strict defensive halt."*

### B. `ETHTMN` Live Adjudication
* **Live Market Price:** 643,208,388 TMN
* **Live Orderbook Imbalance (OBI):** `-0.70` (Heavy institutional ask volume)
* **Arbiter Ruling:** `DEFENSIVE_HALT` (Confidence: 0.95)
* **Bull Thesis:** Score = 0.20 | Catalyst: RSI oversold bounce potential (7.5)
* **Bear Thesis:** Score = 0.80 | Hazard: EMA9 (643.2M) below EMA21 (644.0M) downward pressure
* **Arbiter Synthesis:**
  > *"The bear thesis dominates with a conviction score of 0.80 against a weak bull score of 0.20. Severe downside hazards are present: EMA9 is below EMA21 signaling downward pressure, RSI is at an extremely weak 7.5 failing to sustain rallies, and the order book imbalance (OBI: -0.70) reflects heavy institutional selling pressure. With round-trip fee friction at 0.50% requiring a net edge above +0.35% JEV, the current risk profile fails to clear the hurdle, necessitating a defensive halt."*

### C. `USDTTMN` Live Adjudication
* **Live Market Price:** 268,315 TMN
* **Arbiter Ruling:** `DEFENSIVE_HALT` (Confidence: 0.85)
* **Bull Thesis:** Score = 0.45 | Catalyst: RSI oversold bounce potential (6.1)
* **Bear Thesis:** Score = 0.55 | Hazard: EMA9 (268,315) below EMA21 (268,537) downward pressure
* **Arbiter Synthesis:**
  > *"The bear thesis score (0.55) outweighs the bull score (0.45), driven by negative EMA alignment and immediate overhead resistance. Furthermore, the round-trip fee friction of 0.50% cannot be cleared given the weak momentum and severe downside risk. Halting deployment to protect capital."*

---

## 3. Key Observations & Invariant Verification

1. **Zero False-Positive Entries on Real Hostile Orderbooks:**
   * Live Wallex orderbooks currently show heavy sell pressure (`OBI = -0.98` on BTC).
   * Rather than naively entering because of oversold RSI, the agentic arbiter correctly identified the institutional sell walls and refused to buy into falling knives.
2. **Zero Capital Risk:**
   * Virtual balance remained at exactly **5,000,000 TMN**, fully preserving capital during adverse market conditions.
3. **Zero Financial Cost:**
   * Live shadow evaluations used the Gemini Free Tier with **\$0.00 spend**.
