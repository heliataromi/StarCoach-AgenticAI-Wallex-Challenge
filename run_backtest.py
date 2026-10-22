#!/usr/bin/env python3
"""CLI entrypoint for Deterministic Historical Backtesting.

Usage:
    python run_backtest.py
    python run_backtest.py --symbols BTCTMN,ETHTMN,USDTTMN --candles 300 --equity 5000000
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from src.test.backtester import DeterministicBacktester
from src.test.data_loader import generate_synthetic_market_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic algorithmic backtest for StarCoach Wallex Challenge"
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
        help="Number of 15m candle steps to simulate (default: 300)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible synthetic market generation",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

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

    backtester = DeterministicBacktester(
        initial_equity_tmn=args.equity,
        fee_rate=0.0025,
        log_dir="decision_logs/backtest",
    )

    res = await backtester.run(market_data)

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
    print("-" * 70)
    liq_str = "YES (100% Iranian Toman)" if res.liquidated_to_toman else "NO (Violation: coin inventory remains)"
    print(f"Deadline Liquidation  : {liq_str:>18}")
    print(f"Final Toman Cash      : {res.final_toman_balance:>18,.0f} TMN")
    print(f"Remaining Crypto Coins: {res.final_crypto_balances}")
    print("=" * 70 + "\n")

    return 0


def main():
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
