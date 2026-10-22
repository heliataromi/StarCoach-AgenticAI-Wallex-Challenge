---
name: jev-decision-evaluator
version: "1.0.0"
description: "Mathematical decision theory gate implementing Joint Expected Value (JEV) analysis: calculates probabilistic expectancy, reward-to-risk ratio, and exchange fee friction before any trade order is approved."
skill_tier: functional
author:
  name: heliataromi
tags:
  - jev
  - decision-theory
  - expected-value
  - risk-management
  - quant
  - probability

triggers:
  en:
    - "evaluate jev"
    - "calculate expected value"
    - "check trade expectancy"
    - "jev decision gate"
  fa:
    - "محاسبه امید ریاضی"
    - "فیلتر تصمیم معاملاتی"

negative_triggers:
  en:
    - "random entry"
    - "gamble trade"

lifecycle_status: "active"
---

# JEV (Joint Expected Value) Decision Evaluator

## 1. Purpose
Applies rigorous mathematical decision theory to trading proposals. Ensures that no trade is executed unless the statistical expected value exceeds exchange fees and a positive alpha hurdle.

## 2. Mathematical Formulation

$$\mathbb{E}[V] = P(\text{Win}) \times \left( \frac{\text{TP} - \text{Entry}}{\text{Entry}} \right) - (1 - P(\text{Win})) \times \left( \frac{\text{Entry} - \text{SL}}{\text{Entry}} \right) - \text{RoundTripFees}$$

Where:
- $P(\text{Win})$: Conditional probability of reaching Take Profit before Stop Loss, derived from technical alignment, orderbook depth imbalance, and macro regime consensus ($0.0 \le P \le 1.0$).
- $\text{TP}$: Take Profit target price.
- $\text{SL}$: Stop Loss exit price.
- $\text{RoundTripFees}$: Sum of maker/taker fees for entry and exit (typically $0.25\% \times 2 = 0.50\%$).

## 3. Decision Rules
1. **Approval Hurdle:**
   $$\mathbb{E}[V] \ge +0.0035 \quad (+0.35\% \text{ net edge})$$
2. **Reward-to-Risk Requirement:**
   $$\frac{\text{Gain}}{\text{Loss}} \ge 1.5$$
3. **Rejection Handling:**
   If $\mathbb{E}[V] < +0.35\%$, immediately discard the proposal, increment rejected proposal counter, and write the mathematical breakdown to audit logs.
