# tests/

**Parent context:** `../AGENT.md`
**Generated:** 2026-10-22 · **Updated:** 2026-10-22

## Purpose
Test suites, backtesting harness, shadow-testing execution scripts, and verification test vectors.

## Subdirectories
| Subdirectory / File | Purpose |
|---------------------|---------|
| `unit/` | Unit tests for JEV math, indicator formulas, and risk limits |
| `backtest/` | Historical replay simulator and performance metric calculators |
| `shadow/` | Real-time live orderbook paper-trading test scripts |

## Testing Requirements
- Single command run: `pytest tests/ -v`.
- Every mathematical model (JEV, ATR, Drawdown) must have deterministic unit tests with known input/output vectors.
- Ensure the backtesting harness can be invoked cleanly with `python run_backtest.py`.
