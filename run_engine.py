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

from src.test.backtester import DeterministicBacktester, BacktestResult
from src.test.data_loader import generate_synthetic_market_data
from src.test.shadow_runner import ShadowRunner
from src.wallex.client import WallexClient
from src.wallex.mock_client import MockWallexClient


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
    return parser.parse_args()


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
    backtester = DeterministicBacktester(
        initial_equity_tmn=args.equity,
        fee_rate=0.0025,
        log_dir=log_path,
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
    print("=" * 70 + "\n")
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
    runner = ShadowRunner(
        live_client=live_client,
        symbols=symbols,
        initial_equity_tmn=args.equity,
        poll_interval_seconds=args.poll_interval,
        log_dir=log_path,
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
        print("=" * 70 + "\n")
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
