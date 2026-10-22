# src/test/

**Parent context:** `../AGENTS.md`
**Generated:** 2026-10-23 · **Updated:** 2026-10-23

## Purpose
Backtesting and Shadow Paper-Trading Subsystem (Milestone 6).
Provides deterministic evaluation of trading strategies against historical and synthetic market data,
as well as zero-risk live shadow testing against streaming Wallex orderbook depth.

## Modules
| File | Responsibility |
|------|----------------|
| `data_loader.py` | Parses TradingView UDF candle format, loads offline fixtures, and generates deterministic multi-regime synthetic datasets |
| `backtester.py` | `DeterministicBacktester` simulating multi-asset trading, fee drag (0.25% maker/taker), Sharpe, and asserting 100% Iranian Toman (TMN) final liquidation |
| `shadow_runner.py` | `ShadowRunner` executing live paper-trading on real-time Wallex orderbook depth |
| `__init__.py` | Package exports |

## Invariants & Guardrails
- **100% Realization in Toman (TMN):** Every backtest run MUST assert zero coin inventory at completion.
- **Ponytail Minimalism:** Zero pandas/bloat. Pure standard library (`math`, `statistics`, `random`) and native dataclasses.
- **Reproducibility:** Synthetic datasets accept a `seed` parameter guaranteeing byte-identical price action across test runs.
