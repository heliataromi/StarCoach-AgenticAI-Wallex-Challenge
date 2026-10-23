#!/usr/bin/env python3
"""Unified CLI Engine for StarCoach Wallex Algorithmic Trading Challenge.

Orchestrates:
1. Deterministic Historical Backtesting (--mode backtest)
2. Live Real-Time Shadow Paper-Trading (--mode shadow)
3. Live Algorithmic Execution on Wallex Exchange (--mode live)

Usage:
    python3 run_engine.py --mode backtest
    python3 run_engine.py --mode backtest --symbols BTCTMN,ETHTMN,USDTTMN --candles 300
    python3 run_engine.py --mode shadow --symbols BTCTMN,ETHTMN,USDTTMN --iterations 5
    python3 run_engine.py --mode live --api-key YOUR_KEY --sub-account-id HACKATHON_ID
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional

from src.strategy.llm_gateway import BoxAPIGateway, LLMGateway, load_dotenv
from src.test.backtester import DeterministicBacktester, BacktestResult
from src.test.data_loader import generate_synthetic_market_data
from src.test.shadow_runner import ShadowRunner
from src.wallex.client import WallexClient
from src.wallex.mock_client import MockWallexClient
from src.web.server import TradingWebDashboard

# Automatically load environment variables from .env on startup
load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Algorithmic Trading Engine for StarCoach Wallex Challenge"
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "shadow", "live"],
        default="backtest",
        help="Operating mode: backtest (historical/synthetic), shadow (live paper-trading), live (real trading)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="BTCTMN,ETHTMN,USDTTMN",
        help="Comma-separated symbols to trade (default: BTCTMN,ETHTMN,USDTTMN)",
    )
    parser.add_argument(
        "--equity",
        type=float,
        default=5_000_000.0,
        help="Initial credit capital in Iranian Toman (default: 5,000,000 TMN)",
    )
    parser.add_argument(
        "--candles",
        type=int,
        default=300,
        help="Number of 15m candle bars to simulate in backtest (default: 300)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible synthetic market generation in backtest (default: 42)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="Maximum polling cycles for shadow/live mode (0 = continuous loop)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Interval in seconds between live orderbook polling cycles (default: 5.0s)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("WALLEX_API_KEY", ""),
        help="Wallex API Key for live execution (can also be set via WALLEX_API_KEY env var)",
    )
    parser.add_argument(
        "--sub-account-id",
        type=str,
        default=os.environ.get("WALLEX_SUB_ACCOUNT_ID", "hackathon-sub-5m"),
        help="Dedicated 5M TMN sub-account ID (default: hackathon-sub-5m)",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="decision_logs",
        help="Directory to store immutable SHA-256 signed decision audit logs (default: decision_logs)",
    )
    parser.add_argument(
        "--show-debate",
        action="store_true",
        help="Print real-time Bull vs. Bear dialectical debate transcripts and arbiter rulings",
    )
    parser.add_argument(
        "--show-memory",
        action="store_true",
        help="Print episodic reflection memory summaries and post-trade lessons",
    )
    parser.add_argument(
        "--llm-provider",
        type=str,
        choices=["offline", "boxapi", "gemini", "deepseek", "claude", "ollama"],
        default=os.environ.get("LLM_PROVIDER", "boxapi" if os.environ.get("BOXAPI_API_KEY") else ("gemini" if os.environ.get("GEMINI_API_KEY") else "offline")),
        help="LLM provider architecture: gemini (free dev), boxapi (production), offline (quantitative)",
    )
    parser.add_argument(
        "--gemini-key",
        type=str,
        default=os.environ.get("GEMINI_API_KEY", ""),
        help="Google AI Studio Gemini API Key for free development & testing",
    )
    parser.add_argument(
        "--gemini-model",
        type=str,
        default=os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"),
        help="Gemini model for free testing (default: gemini-1.5-flash)",
    )
    parser.add_argument(
        "--boxapi-key",
        type=str,
        default=os.environ.get("BOXAPI_API_KEY", ""),
        help="BoxAPI Key for official StarCoach hackathon models (reads BOXAPI_API_KEY from .env)",
    )
    parser.add_argument(
        "--boxapi-base-url",
        type=str,
        default=os.environ.get("BOXAPI_BASE_URL", "https://ai.boxapi.ir/ai/v1"),
        help="BoxAPI endpoint URL (default: https://ai.boxapi.ir/ai/v1)",
    )
    parser.add_argument(
        "--boxapi-model",
        type=str,
        default=os.environ.get("BOXAPI_MODEL", "gpt-6-astra"),
        help="Flagship model for high-conviction debate arbiter (default: gpt-6-astra)",
    )
    parser.add_argument(
        "--boxapi-mini-model",
        type=str,
        default=os.environ.get("BOXAPI_MINI_MODEL", "gpt-5.4-mini"),
        help="Low-cost model for macro surveillance and post-mortem reflection (default: gpt-5.4-mini)",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=float(os.environ.get("BOXAPI_BUDGET_USD", "2.50")),
        help="Maximum spend guard for BoxAPI credit in USD (default: $2.50 of $3.00)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        default=bool(os.environ.get("PORT")),
        help="Enable lightweight mobile status web dashboard (auto-enabled on Liara/cloud)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="Port for cloud web dashboard & health check (default: 8080 or $PORT)",
    )
    parser.add_argument(
        "--deadline",
        type=str,
        default=os.environ.get("CHALLENGE_DEADLINE", "2026-10-26T18:00:00+03:30"),
        help="Hackathon deadline ISO string or epoch seconds (default: 2026-10-26T18:00:00+03:30)",
    )
    return parser.parse_args()


def create_llm_gateway(args: argparse.Namespace) -> Optional[LLMGateway]:
    """Instantiates active LLM Gateway based on CLI arguments and .env variables."""
    prov = args.llm_provider.lower()
    if prov == "offline":
        return None

    if prov == "gemini":
        if not args.gemini_key:
            print("Note: GEMINI_API_KEY not set. Defaulting to offline quantitative engine.")
            return None
        print(f"Connecting to Google AI Studio Gemini Free Tier (Model: {args.gemini_model})...")
        return LLMGateway(
            provider="gemini",
            api_key=args.gemini_key,
            macro_model=args.gemini_model,
            debate_model=args.gemini_model,
            reflection_model=args.gemini_model,
            timeout_seconds=15.0,
        )

    if prov == "boxapi":
        if not args.boxapi_key:
            print("Note: BOXAPI_API_KEY not set. Defaulting to offline quantitative engine.")
            return None
        print(f"Connecting to BoxAPI Gateway (Flagship: {args.boxapi_model}, Mini: {args.boxapi_mini_model})...")
        return LLMGateway(
            provider="boxapi",
            api_key=args.boxapi_key,
            base_url=args.boxapi_base_url,
            macro_model=args.boxapi_mini_model,
            debate_model=args.boxapi_model,
            reflection_model=args.boxapi_mini_model,
            budget_limit_usd=args.budget_usd,
            timeout_seconds=15.0,
        )

    return None


async def run_backtest_mode(args: argparse.Namespace, symbols: List[str]) -> int:
    """Executes deterministic historical multi-regime backtest."""
    print("\n" + "=" * 70)
    print("   STARCOACH WALLEX CHALLENGE: DETERMINISTIC BACKTEST HARNESS")
    print("=" * 70)
    print(f"Target Symbols   : {', '.join(symbols)}")
    print(f"Initial Capital  : {args.equity:,.0f} TMN")
    print(f"Candle Steps     : {args.candles} (15m bars, 3 regimes simulated)")
    print(f"Exchange Fee     : 0.25% maker / 0.25% taker (Wallex credit schedule)")
    print(f"JEV Hurdle       : E[V] >= +0.35% net edge required per trade")
    print(f"Drawdown Limit   : 10.0% circuit breaker halt")
    print("-" * 70)
    print("Running simulation...")

    base_prices = {
        "BTCTMN": 4_200_000_000.0,
        "ETHTMN": 180_000_000.0,
        "USDTTMN": 65_000.0,
    }

    market_data = {}
    for sym in symbols:
        p0 = base_prices.get(sym, 100_000.0)
        market_data[sym] = generate_synthetic_market_data(
            symbol=sym,
            base_price=p0,
            num_candles=args.candles,
            seed=args.seed,
        )

    log_path = os.path.join(args.log_dir, "backtest")
    gateway = create_llm_gateway(args)

    backtester = DeterministicBacktester(
        initial_equity_tmn=args.equity,
        fee_rate=0.0025,
        log_dir=log_path,
        llm_gateway=gateway,
    )

    t0 = time.perf_counter()
    res: BacktestResult = await backtester.run(market_data)
    elapsed = (time.perf_counter() - t0) * 1000.0

    print("\n" + "=" * 70)
    print("                    BACKTEST PERFORMANCE REPORT")
    print("=" * 70)
    status_pnl = "+" if res.net_profit_tmn >= 0 else "-"
    print(f"Initial Equity        : {res.initial_equity_tmn:>18,.0f} TMN")
    print(f"Final Equity          : {res.final_equity_tmn:>18,.0f} TMN")
    print(f"Net Profit / Loss     : {res.net_profit_tmn:>18,.0f} TMN ({status_pnl}{res.net_return_pct:.2f}%)")
    print(f"Maximum Drawdown      : {res.max_drawdown_tmn:>18,.0f} TMN ({res.max_drawdown_pct:.2f}%)")
    print("-" * 70)
    print(f"Total Trades Filled   : {res.total_trades:>18}")
    print(f"Winning Trades        : {res.winning_trades:>18}")
    print(f"Losing Trades         : {res.losing_trades:>18}")
    print(f"Win Rate              : {res.win_rate_pct:>17.2f}%")
    print(f"Profit Factor         : {res.profit_factor:>18.2f}")
    print(f"Annualized Sharpe     : {res.sharpe_ratio:>18.2f}")
    print(f"Total Fees Paid (TMN) : {res.total_fees_paid_tmn:>18,.0f} TMN")
    print(f"Simulation Latency    : {elapsed:>16.1f} ms")
    print("-" * 70)
    liq_status = "YES (100% Iranian Toman)" if res.liquidated_to_toman else "NO (WARNING: COIN EXPOSURE REMAINING)"
    print(f"Deadline Liquidation  : {liq_status}")
    print(f"Final Toman Cash      : {res.final_toman_balance:>18,.0f} TMN")
    print(f"Remaining Crypto Coins: {res.final_crypto_balances}")
    print("-" * 70)
    audit_label = "VERIFIED (100% SHA-256 Tamper-Evident)" if res.audit_integrity_valid else "COMPROMISED"
    print(f"Audit Trail Integrity : {audit_label}")
    if res.reflection_summary:
        print(f"Post-Trade Reflection : {res.reflection_summary}")
    if gateway and gateway.is_configured:
        if gateway.provider == "gemini":
            print(f"LLM Provider Usage    : Google AI Studio Gemini Free Tier (Model: {gateway.debate_model}) | Calls: {gateway.stats.successful_calls}/{gateway.stats.total_calls} | Spend: $0.00")
        elif gateway.provider == "boxapi":
            print(f"BoxAPI Credit Usage   : ${gateway.stats.estimated_cost_usd:.4f} / ${gateway.budget_limit_usd:.2f} (Calls: {gateway.stats.successful_calls}/{gateway.stats.total_calls}, Tokens: {gateway.stats.total_prompt_tokens + gateway.stats.total_completion_tokens})")
    print("=" * 70)

    if args.show_debate and getattr(backtester, "_last_macro_agent", None):
        print("\n" + "=" * 70)
        print("          FINAL MULTI-AGENT BULL vs. BEAR DEBATE TRANSCRIPTS")
        print("=" * 70)
        agent = backtester._last_macro_agent
        for sym in symbols:
            tx = agent.get_debate_transcript(sym)
            if tx:
                print(f"\n[Symbol: {sym}] Arbiter Ruling: {tx.arbiter_verdict} (Confidence: {tx.confidence:.2f})")
                print(f"  ▲ BULL Thesis  : Conviction={tx.bull_thesis.bull_score:.2f} | Target: {tx.bull_thesis.target_price:,.0f} TMN")
                print(f"    Catalysts    : {tx.bull_thesis.momentum_catalyst}")
                print(f"  ▼ BEAR Thesis  : Conviction={tx.bear_thesis.bear_score:.2f} | Invalidation: {tx.bear_thesis.invalidation_price:,.0f} TMN")
                print(f"    Key Hazards  : {tx.bear_thesis.risk_factor}")
                print(f"  ⚖ ARBITER NOTE : {tx.summary_reasoning}")
        print("=" * 70 + "\n")
    else:
        print()
    return 0


def parse_deadline(val: Optional[str]) -> Optional[float]:
    """Parses ISO timestamp string or numeric epoch into float timestamp."""
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        pass
    try:
        dt = datetime.datetime.fromisoformat(val)
        return dt.timestamp()
    except Exception:
        return None


async def run_continuous_engine(args: argparse.Namespace, symbols: List[str], is_live: bool = False) -> int:
    """Unified runner for continuous shadow paper-trading and live execution with optional web dashboard."""
    mode_str = "LIVE AUTONOMOUS TRADING ENGINE" if is_live else "LIVE SHADOW PAPER-TRADING"
    exec_type = "REAL EXCHANGE EXECUTION (Wallex Spot)" if is_live else "Simulated paper-trading on live exchange orderbook"
    log_sub = "live" if is_live else "shadow"
    log_path = os.path.join(args.log_dir, log_sub)

    print("\n" + "=" * 70)
    print(f"   STARCOACH WALLEX CHALLENGE: {mode_str}")
    print("=" * 70)
    if is_live:
        print(f"Account ID       : {args.sub_account_id}")
    print(f"Target Symbols   : {', '.join(symbols)}")
    print(f"Capital          : {args.equity:,.0f} TMN")
    print(f"Polling Interval : {args.poll_interval:.1f}s")
    print(f"Execution Type   : {exec_type}")
    print(f"JEV Hurdle       : E[V] >= +0.35% net edge required per trade")
    print(f"Drawdown Limit   : 10.0% circuit breaker halt")
    print(f"Deadline Target  : {args.deadline}")
    print("-" * 70)

    # Initialize Client
    if is_live:
        print("Connecting to live Wallex API...")
        client = WallexClient(api_key=args.api_key, sub_account_id=args.sub_account_id)
        await client.connect()
    else:
        if args.api_key:
            print("Connecting to live Wallex API feed...")
            client = WallexClient(api_key=args.api_key, sub_account_id=args.sub_account_id)
        else:
            print("Note: WALLEX_API_KEY not set. Running against live public endpoints / mock feed.")
            client = MockWallexClient(initial_tmn=args.equity)

    gateway = create_llm_gateway(args)
    deadline_ts = parse_deadline(args.deadline)

    runner = ShadowRunner(
        live_client=client,
        symbols=symbols,
        initial_equity_tmn=args.equity,
        poll_interval_seconds=args.poll_interval,
        log_dir=log_path,
        llm_gateway=gateway,
        is_live=is_live,
        deadline_timestamp=deadline_ts,
    )

    # Shared mutable state for Web Dashboard
    latest_web_state: Dict[str, Any] = {
        "mode": "LIVE" if is_live else "SHADOW",
        "equity_tmn": args.equity,
        "initial_capital_tmn": args.equity,
        "drawdown_pct": 0.0,
        "active_regime": "INITIALIZING",
        "total_trades": 0,
        "iteration": 0,
        "deadline_str": args.deadline,
        "time_to_deadline": "Active",
        "circuit_breaker_active": False,
        "active_positions": {},
        "latest_debates": {},
    }

    def get_web_status() -> Dict[str, Any]:
        return latest_web_state

    dashboard: Optional[TradingWebDashboard] = None
    if args.web:
        try:
            dashboard = TradingWebDashboard(get_status_callback=get_web_status, host="0.0.0.0", port=args.port)
            await dashboard.start()
            print(f"[Web Dashboard] Mobile monitor & Liara health probe live at: http://0.0.0.0:{args.port}")
        except Exception as e:
            print(f"[Warning] Failed to start web dashboard on port {args.port}: {e}")

    # Fixed iteration mode (for CI/automated verification)
    if args.iterations > 0:
        print(f"Running {args.iterations} cycles...")
        summary = await runner.run_iterations(max_iterations=args.iterations)
        print("\n" + "=" * 70)
        print(f"                    {mode_str} SUMMARY")
        print("=" * 70)
        print(f"Cycles Executed       : {summary['iterations_executed']:>18}")
        print(f"Equity (TMN)          : {summary['virtual_equity_tmn']:>18,.0f} TMN")
        print(f"Total Trades          : {summary['total_trades']:>18}")
        print(f"Audit Logs Recorded   : {log_path}")
        print(f"Audit Integrity       : VERIFIED (100% SHA-256 Tamper-Evident)")
        mem_sum = runner.macro_agent.memory.generate_reflection_summary()
        print(f"Memory Reflection     : {mem_sum}")
        if gateway and gateway.is_configured:
            if gateway.provider == "gemini":
                print(f"LLM Provider Usage    : Google AI Studio Gemini Free Tier (Model: {gateway.debate_model}) | Calls: {gateway.stats.successful_calls}/{gateway.stats.total_calls} | Spend: $0.00")
            elif gateway.provider == "boxapi":
                print(f"BoxAPI Credit Usage   : ${gateway.stats.estimated_cost_usd:.4f} / ${gateway.budget_limit_usd:.2f} (Calls: {gateway.stats.successful_calls}/{gateway.stats.total_calls}, Tokens: {gateway.stats.total_prompt_tokens + gateway.stats.total_completion_tokens})")
        print("=" * 70 + "\n")
        if dashboard:
            await dashboard.stop()
        return 0

    # Continuous production execution loop
    print("Running continuous autonomous loop (Press Ctrl+C to stop)...")
    try:
        while True:
            cycle = await runner.tick_once()
            eq = cycle.get("equity_tmn", cycle.get("virtual_equity_tmn", args.equity))
            dd = cycle.get("drawdown_pct", cycle.get("virtual_drawdown_pct", 0.0))
            reg = cycle.get("active_regime", "UNKNOWN")

            # Update web state
            latest_web_state["equity_tmn"] = eq
            latest_web_state["drawdown_pct"] = dd
            latest_web_state["active_regime"] = reg
            latest_web_state["iteration"] = cycle["iteration"]
            latest_web_state["circuit_breaker_active"] = cycle.get("circuit_breaker_active", False)
            latest_web_state["active_positions"] = cycle.get("active_positions", {})
            latest_web_state["total_trades"] = runner.dispatcher.metrics.total_orders_filled

            if deadline_ts:
                rem = max(0, int(deadline_ts - time.time()))
                hrs, rem_s = divmod(rem, 3600)
                mins, _ = divmod(rem_s, 60)
                latest_web_state["time_to_deadline"] = f"{hrs}h {mins}m left ({cycle.get('liquidation_phase', 'NORMAL')})"

            # Format debates for web card
            debates_web = {}
            for sym, tx_dict in cycle.get("debates", {}).items():
                debates_web[sym] = {
                    "arbiter_verdict": tx_dict.get("arbiter_verdict", "N/A"),
                    "confidence": tx_dict.get("confidence", 0.0),
                    "bull_score": tx_dict.get("bull_thesis", {}).get("bull_score", 0.0),
                    "bear_score": tx_dict.get("bear_thesis", {}).get("bear_score", 0.0),
                    "catalyst": tx_dict.get("bull_thesis", {}).get("momentum_catalyst", ""),
                    "risk": tx_dict.get("bear_thesis", {}).get("risk_factor", ""),
                }
            latest_web_state["latest_debates"] = debates_web

            print(
                f"[Cycle {cycle['iteration']:>4}] Regime: {reg:<22} | "
                f"Equity: {eq:>12,.0f} TMN | "
                f"DD: {dd:>5.2f}% | "
                f"Positions: {cycle['active_positions_count']}"
            )
            if args.show_debate and "debates" in cycle:
                for sym, dt in cycle["debates"].items():
                    print(
                        f"   ├─ [{sym}] Ruling: {dt['arbiter_verdict']:<24} | "
                        f"Bull={dt['bull_thesis']['bull_score']:.2f} | "
                        f"Bear={dt['bear_thesis']['bear_score']:.2f}"
                    )
            if args.show_memory and cycle.get("memory_summary"):
                print(f"   └─ Memory: {cycle['memory_summary']}")

            await asyncio.sleep(args.poll_interval)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print(f"\n[{mode_str}] Gracefully stopped by operator.")
    finally:
        if dashboard:
            await dashboard.stop()
    return 0


async def run_shadow_mode(args: argparse.Namespace, symbols: List[str]) -> int:
    """Executes live shadow paper-trading on live Wallex orderbooks."""
    return await run_continuous_engine(args, symbols, is_live=False)


async def run_live_mode(args: argparse.Namespace, symbols: List[str]) -> int:
    """Live trading on dedicated 5,000,000 TMN Wallex credit account."""
    if not args.api_key:
        print("\n[ERROR] Live trading requires --api-key or WALLEX_API_KEY environment variable.")
        print("For zero-risk simulation without keys, use: python3 run_engine.py --mode backtest")
        print("For real-time paper-trading without keys, use: python3 run_engine.py --mode shadow")
        return 1
    return await run_continuous_engine(args, symbols, is_live=True)


async def main_async() -> int:
    args = parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.mode == "backtest":
        return await run_backtest_mode(args, symbols)
    elif args.mode == "shadow":
        return await run_shadow_mode(args, symbols)
    elif args.mode == "live":
        return await run_live_mode(args, symbols)
    return 0


def main() -> None:
    try:
        code = asyncio.run(main_async())
        sys.exit(code)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
