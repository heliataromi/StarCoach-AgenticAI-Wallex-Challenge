"""Deterministic Backtesting Engine.

Evaluates trading strategies against historical and synthetic market data,
enforcing realistic fee drag (0.25% maker/taker), slippage, JEV expectancy gates,
and mandatory 100% Iranian Toman (TMN) final liquidation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple
from src.engine.dispatcher import OrderExecutionDispatcher
from src.engine.portfolio import PortfolioTracker
from src.logger.decision_logger import DecisionAuditor
from src.risk.drawdown import DrawdownSentinel
from src.risk.jev import JEVEvaluator
from src.risk.models import RiskConfig
from src.risk.sizing import PositionSizer
from src.strategy.macro_agent import MacroAgent
from src.strategy.models import MarketRegime, SignalAction, TradingSignal
from src.strategy.signals import MultiRegimeSignalGenerator
from src.test.data_loader import create_orderbook_for_price
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import Candle, OrderSide, OrderType
from src.wallex.rate_limiter import TokenBucketRateLimiter


@dataclass
class BacktestResult:
    """Institutional quantitative performance metrics from a backtest run."""
    initial_equity_tmn: float
    final_equity_tmn: float
    net_profit_tmn: float
    net_return_pct: float
    max_drawdown_pct: float
    max_drawdown_tmn: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    sharpe_ratio: float
    total_fees_paid_tmn: float
    final_toman_balance: float
    final_crypto_balances: Dict[str, float]
    liquidated_to_toman: bool
    equity_curve: List[Tuple[float, float]] = field(default_factory=list)
    audit_summary: Dict[str, Any] = field(default_factory=dict)
    reflection_summary: str = ""
    audit_integrity_valid: bool = True


class DeterministicBacktester:
    """Historical and synthetic backtesting harness executing end-to-end trading pipeline."""

    def __init__(
        self,
        initial_equity_tmn: float = 5_000_000.0,
        fee_rate: float = 0.0025,  # 0.25% Wallex standard fee
        log_dir: str = "decision_logs/backtest",
        macro_cadence_steps: int = 15,
        llm_gateway: Optional[Any] = None,
        jev_hurdle_rate_pct: float = 0.0050,
        min_reward_to_risk: float = 1.8,
    ) -> None:
        self.initial_equity_tmn = float(initial_equity_tmn)
        self.fee_rate = fee_rate
        self.log_dir = log_dir
        self.macro_cadence_steps = macro_cadence_steps
        self.llm_gateway = llm_gateway
        self.jev_hurdle_rate_pct = jev_hurdle_rate_pct
        self.min_reward_to_risk = min_reward_to_risk

    async def run(
        self,
        market_candles: Dict[str, List[Candle]],
        challenge_deadline_step: Optional[int] = None,
    ) -> BacktestResult:
        """Executes full backtest simulation over multi-asset candle feeds."""
        symbols = list(market_candles.keys())
        if not symbols:
            raise ValueError("market_candles cannot be empty")

        # 1. Initialize Pipeline Components
        mock_rate_limiter = TokenBucketRateLimiter(capacity=1_000_000.0, refill_rate=1_000_000.0)
        mock_client = MockWallexClient(
            initial_tmn=self.initial_equity_tmn,
            fee_rate=self.fee_rate,
            rate_limiter=mock_rate_limiter,
        )
        config = RiskConfig(
            initial_capital_tmn=self.initial_equity_tmn,
            max_position_size_pct=0.20,
            round_trip_fee_pct=self.fee_rate * 2.0,
            jev_hurdle_rate_pct=self.jev_hurdle_rate_pct,
            min_reward_to_risk=self.min_reward_to_risk,
        )
        drawdown_sentinel = DrawdownSentinel(config=config)
        position_sizer = PositionSizer(config=config)
        portfolio = PortfolioTracker(
            initial_equity_tmn=self.initial_equity_tmn,
            drawdown_sentinel=drawdown_sentinel,
            position_sizer=position_sizer,
        )
        jev_evaluator = JEVEvaluator(config=config)
        dispatcher = OrderExecutionDispatcher(
            client=mock_client,
            portfolio=portfolio,
            jev_evaluator=jev_evaluator,
            position_sizer=position_sizer,
            drawdown_sentinel=drawdown_sentinel,
        )
        macro_agent = MacroAgent(
            allowed_symbols=symbols,
            default_policy_ttl_seconds=14400.0,
            llm_gateway=self.llm_gateway,
        )
        signal_generator = MultiRegimeSignalGenerator()
        auditor = DecisionAuditor(log_dir=self.log_dir, auto_flush=False)

        # 2. Determine Simulation Length
        total_steps = min(len(candles) for candles in market_candles.values())
        if total_steps < 30:
            raise ValueError(f"Insufficient candle history: {total_steps} < 30")

        equity_curve: List[Tuple[float, float]] = []
        periodic_returns: List[float] = []
        prev_equity = self.initial_equity_tmn
        peak_equity = self.initial_equity_tmn
        max_dd_pct = 0.0
        max_dd_tmn = 0.0

        current_policy = None
        deadline_step = challenge_deadline_step or (total_steps - 10)

        # 3. Step-by-Step Historical Simulation Loop
        for step in range(25, total_steps):
            window_candles = {sym: market_candles[sym][: step + 1] for sym in symbols}
            step_time = window_candles[symbols[0]][-1].timestamp / 1000.0

            # Macro Cadence: Re-evaluate policy periodically
            if step % self.macro_cadence_steps == 0 or current_policy is None:
                current_policy = macro_agent.evaluate_policy(
                    market_candles=window_candles,
                    current_time=step_time,
                )

            # Process each symbol
            for sym in symbols:
                c_history = window_candles[sym]
                curr_price = c_history[-1].close

                # Update mock client price & construct orderbook
                mock_client.mock_prices[sym] = curr_price
                ob = create_orderbook_for_price(
                    symbol=sym,
                    price=curr_price,
                    spread_pct=0.0015,
                    imbalance=0.0,
                    timestamp_ms=c_history[-1].timestamp,
                )
                mock_client.orderbooks[sym] = {
                    "bids": ob.bids,
                    "asks": ob.asks,
                    "timestamp": ob.timestamp,
                }

                # Update portfolio
                portfolio.update_market_price(sym, curr_price, atr_value=curr_price * 0.01)
                has_pos = portfolio.has_active_position(sym)
                pos = portfolio.get_position(sym)
                entry_p = pos.entry_price if pos else 0.0

                # Check deadline liquidation condition
                if step >= deadline_step and has_pos:
                    # Enforce final liquidation to Toman
                    sig = TradingSignal(
                        symbol=sym,
                        action=SignalAction.SELL,
                        regime=MarketRegime.HIGH_VOLATILITY_PANIC,
                        current_price=curr_price,
                        target_price=curr_price,
                        stop_loss_price=curr_price * 1.05,
                        estimated_win_probability=1.0,
                        confidence=1.0,
                        rationale="Mandatory deadline liquidation to Iranian Toman",
                    )
                else:
                    # Generate tactical signal
                    sig = signal_generator.generate_signal(
                        symbol=sym,
                        regime=current_policy.active_regime,
                        candles=c_history,
                        orderbook=ob,
                        has_open_position=has_pos,
                        entry_price=entry_p,
                    )

                # Dispatch signal if actionable
                if sig.action != SignalAction.HOLD:
                    exec_res = await dispatcher.dispatch_signal(
                        signal=sig,
                        policy=current_policy,
                        current_time=step_time,
                    )
                    debate_tx = macro_agent.get_debate_transcript(sym)
                    memory_sum = macro_agent.memory.generate_reflection_summary()
                    recent_lessons = macro_agent.memory.get_recent_lessons(sym)
                    auditor.record_decision(
                        signal=sig,
                        execution_result=exec_res,
                        policy=current_policy,
                        portfolio_equity_tmn=portfolio.calculate_total_equity(),
                        debate_transcript=debate_tx,
                        memory_summary=memory_sum,
                        recent_lessons=recent_lessons,
                    )

                    # Update episodic memory reflection on completed sell trade
                    if exec_res.success and sig.action == SignalAction.SELL and has_pos and entry_p > 0:
                        pnl_tmn = (exec_res.executed_price - entry_p) * exec_res.executed_quantity - exec_res.fee_paid
                        macro_agent.record_trade_outcome(
                            symbol=sym,
                            entry_price=entry_p,
                            exit_price=exec_res.executed_price,
                            quantity=exec_res.executed_quantity,
                            pnl_tmn=pnl_tmn,
                            fee_tmn=exec_res.fee_paid,
                            regime=current_policy.active_regime,
                            exit_reason=sig.rationale,
                            timestamp=step_time,
                        )

            # Record Mark-to-Market Equity
            eq = portfolio.calculate_total_equity()
            equity_curve.append((step_time, eq))

            # Track Drawdown
            if eq > peak_equity:
                peak_equity = eq
            dd_tmn = max(0.0, peak_equity - eq)
            dd_pct = (dd_tmn / peak_equity) if peak_equity > 0 else 0.0
            max_dd_tmn = max(max_dd_tmn, dd_tmn)
            max_dd_pct = max(max_dd_pct, dd_pct)

            # Track Returns for Sharpe
            if prev_equity > 0:
                step_ret = (eq - prev_equity) / prev_equity
                periodic_returns.append(step_ret)
            prev_equity = eq

        # 4. Mandatory Post-Backtest Liquidation Assertion
        # Assert 100% conversion of any remaining crypto back to Iranian Toman
        for sym in symbols:
            base = sym.replace("TMN", "")
            qty_rem = portfolio.crypto_balances.get(base, 0.0)
            if qty_rem > 0:
                p = mock_client.mock_prices.get(sym, 1.0)
                sell_res = await mock_client.post_order(
                    symbol=sym,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    price=p,
                    quantity=qty_rem,
                    client_order_id=f"liq-final-{sym}",
                )
                from src.engine.models import ExecutionStatus, OrderExecutionResult
                portfolio.record_fill(
                    OrderExecutionResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        symbol=sym,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        requested_price=p,
                        requested_quantity=qty_rem,
                        executed_price=p,
                        executed_quantity=qty_rem,
                        fee_paid=p * qty_rem * self.fee_rate,
                    )
                )

        final_equity = portfolio.calculate_total_equity()
        net_profit_tmn = final_equity - self.initial_equity_tmn
        net_return_pct = (net_profit_tmn / self.initial_equity_tmn) * 100.0

        # Calculate Win/Loss and Profit Factor from realized round-trip trades
        closed_trades = getattr(portfolio, "trade_history", [])
        winning_trades = sum(1 for t in closed_trades if t.get("pnl_tmn", 0.0) > 0)
        losing_trades = sum(1 for t in closed_trades if t.get("pnl_tmn", 0.0) <= 0)
        gross_profit = sum(t["pnl_tmn"] for t in closed_trades if t.get("pnl_tmn", 0.0) > 0)
        gross_loss = sum(abs(t["pnl_tmn"]) for t in closed_trades if t.get("pnl_tmn", 0.0) < 0)

        total_trades = dispatcher.metrics.total_orders_filled
        win_rate = (winning_trades / max(1, len(closed_trades))) * 100.0 if closed_trades else 0.0
        profit_factor = (gross_profit / max(1e-4, gross_loss)) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

        # Annualized Sharpe Ratio
        if len(periodic_returns) > 1 and statistics.stdev(periodic_returns) > 0:
            mean_ret = statistics.mean(periodic_returns)
            std_ret = statistics.stdev(periodic_returns)
            # Assuming 15m candles: ~35,040 steps/year
            sharpe = (mean_ret / std_ret) * math.sqrt(35040)
        else:
            sharpe = 0.0

        # Assert zero coin inventory
        final_crypto = {k: v for k, v in portfolio.crypto_balances.items() if v > 1e-8}
        is_fully_liquidated = (len(final_crypto) == 0)

        is_audit_valid, _, _ = auditor.verify_all_records()
        self._last_auditor = auditor
        self._last_portfolio = portfolio
        self._last_macro_agent = macro_agent

        return BacktestResult(
            initial_equity_tmn=self.initial_equity_tmn,
            final_equity_tmn=final_equity,
            net_profit_tmn=net_profit_tmn,
            net_return_pct=net_return_pct,
            max_drawdown_pct=max_dd_pct * 100.0,
            max_drawdown_tmn=max_dd_tmn,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            total_fees_paid_tmn=dispatcher.metrics.total_fees_paid_tmn,
            final_toman_balance=portfolio.tmn_available,
            final_crypto_balances=final_crypto,
            liquidated_to_toman=is_fully_liquidated,
            equity_curve=equity_curve,
            audit_summary=auditor.get_summary_stats(),
            reflection_summary=macro_agent.memory.generate_reflection_summary(),
            audit_integrity_valid=is_audit_valid,
        )
