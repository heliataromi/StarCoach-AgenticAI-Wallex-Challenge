# src/

**Parent context:** `../AGENT.md`
**Generated:** 2026-10-22 · **Updated:** 2026-10-22

## Purpose
Core source code directory for the Wallex Algorithmic Trading Engine.

## Subdirectories & Modules
| Subdirectory | Purpose |
|--------------|---------|
| `wallex/` | REST API client, Wallex MCP integration, rate-limiter, orderbook streamer |
| `strategy/` | Macro Agentic Brain, latent regime classifier, 3-regime quantitative signal generators |
| `risk/` | JEV Decision Gate, Drawdown Sentinel, ATR trailing stop, deadline Toman liquidation |
| `engine/` | Sub-50ms async execution dispatcher, order lifecycle management |
| `logger/` | Structured decision auditor generating immutable trade logs |

## For AI Agents
- Follow Python 3.12 modern idioms: type annotations, async/await, dataclasses/pydantic.
- Enforce the Ponytail ladder: no unnecessary dependencies, shortest correct diffs, single runnable test check per module.
- Never place LLM network calls directly in the sub-50ms micro-execution hot path.
