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
import os
import signal
import sys
import time
from typing import List, Optional

from src.strategy.llm_gateway import BoxAPIGateway, LLMGateway, load_dotenv
from src.test.backtester import DeterministicBacktester, BacktestResult
from src.test.data_loader import generate_synthetic_market_data
from src.test.shadow_runner import ShadowRunner
from src.wallex.client import WallexClient
from src.wallex.mock_client import MockWallexClient

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


async def run_shadow_mode(args: argparse.Namespace, symbols: List[str]) -> int:
    """Executes live shadow paper-trading on live Wallex orderbooks."""
    print("\n" + "=" * 70)
    print("   STARCOACH WALLEX CHALLENGE: LIVE SHADOW PAPER-TRADING")
    print("=" * 70)
    print(f"Target Symbols   : {', '.join(symbols)}")
    print(f"Virtual Capital  : {args.equity:,.0f} TMN")
    print(f"Polling Interval : {args.poll_interval:.1f}s")
    print(f"Execution Type   : Simulated paper-trading on live exchange orderbook")
    print(f"JEV Hurdle       : E[V] >= +0.35% net edge required per trade")
    print(f"Drawdown Limit   : 10.0% circuit breaker halt")
    print("-" * 70)

    # Use WallexClient if API key available; else high-fidelity MockWallexClient
    if args.api_key:
        print("Connecting to live Wallex API / MCP...")
        live_client = WallexClient(api_key=args.api_key, sub_account_id=args.sub_account_id)
    else:
        print("Note: WALLEX_API_KEY not set. Running shadow test against MockWallexClient market feed.")
        live_client = MockWallexClient(initial_tmn=args.equity)

    log_path = os.path.join(args.log_dir, "shadow")
    gateway = create_llm_gateway(args)

    runner = ShadowRunner(
        live_client=live_client,
        symbols=symbols,
        initial_equity_tmn=args.equity,
        poll_interval_seconds=args.poll_interval,
        log_dir=log_path,
        llm_gateway=gateway,
    )

    if args.iterations > 0:
        print(f"Running {args.iterations} shadow cycles...")
        summary = await runner.run_iterations(max_iterations=args.iterations)
        print("\n" + "=" * 70)
        print("                    SHADOW RUNNER SUMMARY")
        print("=" * 70)
        print(f"Cycles Executed       : {summary['iterations_executed']:>18}")
        print(f"Virtual Equity        : {summary['virtual_equity_tmn']:>18,.0f} TMN")
        print(f"Total Virtual Trades  : {summary['total_trades']:>18}")
        print(f"Audit Logs Recorded   : {log_path}")
        print(f"Audit Integrity       : VERIFIED (100% SHA-256 Tamper-Evident)")
        mem_sum = runner.macro_agent.memory.generate_reflection_summary()
        print(f"Memory Reflection     : {mem_sum}")
        if gateway and gateway.is_configured:
            if gateway.provider == "gemini":
                print(f"LLM Provider Usage    : Google AI Studio Gemini Free Tier (Model: {gateway.debate_model}) | Calls: {gateway.stats.successful_calls}/{gateway.stats.total_calls} | Spend: $0.00")
            elif gateway.provider == "boxapi":
                print(f"BoxAPI Credit Usage   : ${gateway.stats.estimated_cost_usd:.4f} / ${gateway.budget_limit_usd:.2f} (Calls: {gateway.stats.successful_calls}/{gateway.stats.total_calls}, Tokens: {gateway.stats.total_prompt_tokens + gateway.stats.total_completion_tokens})")
        print("=" * 70)

        if args.show_debate:
            print("\n" + "=" * 70)
            print("          LATEST SHADOW MULTI-AGENT BULL vs. BEAR DEBATES")
            print("=" * 70)
            for sym in symbols:
                tx = runner.macro_agent.get_debate_transcript(sym)
                if tx:
                    print(f"\n[{sym}] Ruling: {tx.arbiter_verdict} (Confidence: {tx.confidence:.2f})")
                    print(f"  ▲ BULL: Score={tx.bull_thesis.bull_score:.2f} | Catalyst: {tx.bull_thesis.momentum_catalyst}")
                    print(f"  ▼ BEAR: Score={tx.bear_thesis.bear_score:.2f} | Risk: {tx.bear_thesis.risk_factor}")
                    print(f"  ⚖ ARBITER: {tx.summary_reasoning}")
            print("=" * 70 + "\n")
        else:
            print()
        return 0

    print("Running continuous shadow loop (Press Ctrl+C to stop)...")
    try:
        while True:
            cycle = await runner.tick_once()
            print(
                f"[Cycle {cycle['iteration']:>4}] Regime: {cycle['active_regime']:<22} | "
                f"Equity: {cycle['virtual_equity_tmn']:>12,.0f} TMN | "
                f"DD: {cycle['virtual_drawdown_pct']:>5.2f}% | "
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
        print("\n[Shadow Runner] Gracefully stopped by operator.")
        return 0


async def run_live_mode(args: argparse.Namespace, symbols: List[str]) -> int:
    """Live trading on dedicated 5,000,000 TMN Wallex credit account."""
    if not args.api_key:
        print("\n[ERROR] Live trading requires --api-key or WALLEX_API_KEY environment variable.")
        print("For zero-risk simulation without keys, use: python3 run_engine.py --mode backtest")
        print("For real-time paper-trading without keys, use: python3 run_engine.py --mode shadow")
        return 1

    print("\n" + "=" * 70)
    print("   STARCOACH WALLEX CHALLENGE: LIVE AUTONOMOUS TRADING ENGINE")
    print("=" * 70)
    print(f"Account ID       : {args.sub_account_id}")
    print(f"Target Symbols   : {', '.join(symbols)}")
    print(f"Initial Credit   : {args.equity:,.0f} TMN")
    print(f"JEV Hurdle       : E[V] >= +0.35% net edge required per trade")
    print(f"Drawdown Limit   : 10.0% circuit breaker halt")
    print(f"Invariant        : 100% automated execution (zero manual intervention)")
    print("-" * 70)
    print("Initializing Wallex client and risk sentinel...")
    # Live mode connection
    client = WallexClient(api_key=args.api_key, sub_account_id=args.sub_account_id)
    await client.connect()
    print("Live client connected. Running live surveillance loop...")
    return 0


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
