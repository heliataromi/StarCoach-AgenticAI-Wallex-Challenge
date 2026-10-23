#!/usr/bin/env python3
"""CLI entrypoint for Deterministic Historical Backtesting.

Usage:
    python run_backtest.py
    python run_backtest.py --symbols BTCTMN,ETHTMN,USDTTMN --candles 300 --equity 5000000
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from src.strategy.llm_gateway import LLMGateway, load_dotenv
from src.test.backtester import DeterministicBacktester
from src.test.data_loader import generate_synthetic_market_data

# Ensure .env is loaded
load_dotenv()


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
    parser.add_argument(
        "--llm-provider",
        type=str,
        choices=["offline", "boxapi", "gemini"],
        default=os.environ.get("LLM_PROVIDER", "gemini" if os.environ.get("GEMINI_API_KEY") else "offline"),
        help="LLM provider: gemini (free dev), boxapi (production), offline (quantitative)",
    )
    parser.add_argument(
        "--gemini-model",
        type=str,
        default=os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
        help="Gemini model (default: gemini-flash-lite-latest)",
    )
    parser.add_argument(
        "--show-debate",
        action="store_true",
        help="Print real-time Bull vs. Bear dialectical debate transcripts",
    )
    parser.add_argument(
        "--jev-hurdle",
        type=float,
        default=0.0050,
        help="Minimum positive JEV net edge hurdle fraction (default: 0.0050 = +0.50%%)",
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
    print(f"JEV Hurdle       : E[V] >= +{args.jev_hurdle * 100:.2f}% net edge required per trade")
    print(f"Drawdown Limit   : 10.0% circuit breaker halt")
    print(f"LLM Provider     : {args.llm_provider.upper()} ({args.gemini_model if args.llm_provider == 'gemini' else 'N/A'})")
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

    gateway = None
    if args.llm_provider == "gemini":
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            print(f"Connecting to Google AI Studio Gemini Free Tier (Model: {args.gemini_model})...")
            gateway = LLMGateway(
                provider="gemini",
                api_key=gemini_key,
                macro_model=args.gemini_model,
                debate_model=args.gemini_model,
                reflection_model=args.gemini_model,
                timeout_seconds=15.0,
            )
        else:
            print("Note: GEMINI_API_KEY not set. Defaulting to offline quantitative engine.")
    elif args.llm_provider == "boxapi":
        boxapi_key = os.environ.get("BOXAPI_API_KEY", "")
        if boxapi_key:
            print("Connecting to BoxAPI Gateway...")
            gateway = LLMGateway(provider="boxapi", api_key=boxapi_key, timeout_seconds=15.0)

    backtester = DeterministicBacktester(
        initial_equity_tmn=args.equity,
        fee_rate=0.0025,
        log_dir="decision_logs/backtest",
        llm_gateway=gateway,
        jev_hurdle_rate_pct=args.jev_hurdle,
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
    print("-" * 70)
    audit_label = "VERIFIED (100% SHA-256 Tamper-Evident)" if res.audit_integrity_valid else "COMPROMISED"
    print(f"Audit Trail Integrity : {audit_label:>18}")
    if gateway and gateway.is_configured:
        print(f"LLM Calls (Success/Tot): {gateway.stats.successful_calls}/{gateway.stats.total_calls}")
        print(f"LLM Spend             : ${gateway.stats.estimated_cost_usd:.4f} USD")
    print("=" * 70 + "\n")

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

    return 0


def main():
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
