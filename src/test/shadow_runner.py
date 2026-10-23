"""Live Shadow Paper-Trading Runner.

Streams real-time live orderbook depth and market data from Wallex API/MCP,
evaluates macro regimes and tactical signals, and simulates execution
with zero financial risk on dedicated virtual 5,000,000 TMN capital.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from src.engine.dispatcher import OrderExecutionDispatcher
from src.engine.portfolio import PortfolioTracker
from src.logger.decision_logger import DecisionAuditor
from src.risk.drawdown import DrawdownSentinel
from src.risk.jev import JEVEvaluator
from src.risk.models import RiskConfig
from src.risk.sizing import PositionSizer
from src.strategy.macro_agent import MacroAgent
from src.strategy.signals import MultiRegimeSignalGenerator
from src.wallex.client import BaseWallexClient
from src.wallex.mock_client import MockWallexClient
from src.wallex.models import Candle, OrderBook


class ShadowRunner:
    """Live shadow runner executing paper-trading on real Wallex market depth feeds."""

    def __init__(
        self,
        live_client: BaseWallexClient,
        symbols: Optional[List[str]] = None,
        initial_equity_tmn: float = 5_000_000.0,
        poll_interval_seconds: float = 2.0,
        macro_interval_seconds: float = 300.0,
        log_dir: str = "decision_logs/shadow",
        llm_gateway: Optional[Any] = None,
    ) -> None:
        self.live_client = live_client
        self.symbols = symbols or ["BTCTMN", "ETHTMN", "USDTTMN"]
        self.initial_equity_tmn = initial_equity_tmn
        self.poll_interval = poll_interval_seconds
        self.macro_interval = macro_interval_seconds
        self.llm_gateway = llm_gateway

        # Paper trading virtual exchange
        self.virtual_client = MockWallexClient(initial_tmn=initial_equity_tmn)
        self.config = RiskConfig(initial_capital_tmn=initial_equity_tmn)
        self.drawdown_sentinel = DrawdownSentinel(config=self.config)
        self.position_sizer = PositionSizer(config=self.config)
        self.portfolio = PortfolioTracker(
            initial_equity_tmn=initial_equity_tmn,
            drawdown_sentinel=self.drawdown_sentinel,
            position_sizer=self.position_sizer,
        )
        self.jev_evaluator = JEVEvaluator(config=self.config)
        self.dispatcher = OrderExecutionDispatcher(
            client=self.virtual_client,
            portfolio=self.portfolio,
            jev_evaluator=self.jev_evaluator,
            position_sizer=self.position_sizer,
            drawdown_sentinel=self.drawdown_sentinel,
        )
        self.macro_agent = MacroAgent(allowed_symbols=self.symbols, llm_gateway=self.llm_gateway)
        self.signal_generator = MultiRegimeSignalGenerator()
        self.auditor = DecisionAuditor(log_dir=log_dir, auto_flush=True)

        self._running: bool = False
        self._iteration: int = 0

    async def run_iterations(self, max_iterations: int = 1) -> Dict[str, Any]:
        """Runs a fixed number of live polling cycles for testing or automated sweeps."""
        results = []
        for _ in range(max_iterations):
            cycle_data = await self.tick_once()
            results.append(cycle_data)
            if max_iterations > 1:
                await asyncio.sleep(self.poll_interval)

        return {
            "iterations_executed": max_iterations,
            "virtual_equity_tmn": self.portfolio.calculate_total_equity(),
            "total_trades": self.dispatcher.metrics.total_orders_filled,
            "metrics": self.dispatcher.metrics,
            "latest_cycle": results[-1] if results else {},
        }

    async def tick_once(self) -> Dict[str, Any]:
        """Executes a single live market assessment and virtual execution cycle."""
        self._iteration += 1
        now = time.time()
        market_candles: Dict[str, List[Candle]] = {}
        market_orderbooks: Dict[str, OrderBook] = {}

        # 1. Fetch live market depth and candles for all symbols
        for sym in self.symbols:
            try:
                # Live depth
                depth_snapshot = await self.live_client.get_market_depth(sym)
                market_orderbooks[sym] = depth_snapshot.orderbook

                # Update prices in virtual exchange
                mid = depth_snapshot.orderbook.mid_price or 1.0
                self.virtual_client.mock_prices[sym] = mid
                self.virtual_client.orderbooks[sym] = {
                    "bids": depth_snapshot.orderbook.bids,
                    "asks": depth_snapshot.orderbook.asks,
                    "timestamp": depth_snapshot.timestamp,
                }
                self.portfolio.update_market_price(sym, mid)

                # Live candles
                raw_candles = await self.live_client.get_candles(sym, resolution="15")
                c_objs: List[Candle] = []
                for rc in raw_candles:
                    if isinstance(rc, dict):
                        c_objs.append(
                            Candle(
                                timestamp=float(rc.get("timestamp", rc.get("t", now * 1000))),
                                open=float(rc.get("open", rc.get("o", mid))),
                                high=float(rc.get("high", rc.get("h", mid))),
                                low=float(rc.get("low", rc.get("l", mid))),
                                close=float(rc.get("close", rc.get("c", mid))),
                                volume=float(rc.get("volume", rc.get("v", 1.0))),
                            )
                        )
                market_candles[sym] = c_objs
            except Exception:
                # Fallback to virtual client data if network drops
                pass

        # 2. Evaluate Macro Policy
        policy = self.macro_agent.get_or_refresh_policy(
            market_candles=market_candles,
            market_orderbooks=market_orderbooks,
            current_time=now,
        )

        # 3. Evaluate and Dispatch Signals for each symbol
        cycle_signals = []
        for sym in self.symbols:
            c_list = market_candles.get(sym, [])
            ob = market_orderbooks.get(sym)
            has_pos = self.portfolio.has_active_position(sym)
            pos = self.portfolio.get_position(sym)
            entry_p = pos.entry_price if pos else 0.0

            sig = self.signal_generator.generate_signal(
                symbol=sym,
                regime=policy.active_regime,
                candles=c_list,
                orderbook=ob,
                has_open_position=has_pos,
                entry_price=entry_p,
            )
            cycle_signals.append(sig)

            # Fast dispatch
            if sig.action.value != "HOLD":
                exec_res = await self.dispatcher.dispatch_signal(
                    signal=sig,
                    policy=policy,
                    current_time=now,
                )
                debate_tx = self.macro_agent.get_debate_transcript(sym)
                memory_sum = self.macro_agent.memory.generate_reflection_summary()
                recent_lessons = self.macro_agent.memory.get_recent_lessons(sym)
                self.auditor.record_decision(
                    signal=sig,
                    execution_result=exec_res,
                    policy=policy,
                    portfolio_equity_tmn=self.portfolio.calculate_total_equity(),
                    debate_transcript=debate_tx,
                    memory_summary=memory_sum,
                    recent_lessons=recent_lessons,
                )

                # Update episodic memory reflection on completed sell trade
                if exec_res.success and sig.action.value == "SELL" and has_pos and entry_p > 0:
                    pnl_tmn = (exec_res.executed_price - entry_p) * exec_res.executed_quantity - exec_res.fee_paid
                    self.macro_agent.record_trade_outcome(
                        symbol=sym,
                        entry_price=entry_p,
                        exit_price=exec_res.executed_price,
                        quantity=exec_res.executed_quantity,
                        pnl_tmn=pnl_tmn,
                        fee_tmn=exec_res.fee_paid,
                        regime=policy.active_regime,
                        exit_reason=sig.rationale,
                        timestamp=now,
                    )

        equity = self.portfolio.calculate_total_equity()
        hwm = self.drawdown_sentinel.high_water_mark
        dd_pct = ((hwm - equity) / hwm * 100.0) if hwm > equity else 0.0
        debates = {}
        for sym in self.symbols:
            tx = self.macro_agent.get_debate_transcript(sym)
            if tx:
                debates[sym] = tx.to_dict()

        return {
            "iteration": self._iteration,
            "timestamp": now,
            "active_regime": policy.active_regime.value,
            "virtual_equity_tmn": equity,
            "virtual_drawdown_pct": dd_pct,
            "active_positions_count": len(self.portfolio.active_positions),
            "signals_evaluated": len(cycle_signals),
            "debates": debates,
            "memory_summary": self.macro_agent.memory.generate_reflection_summary(),
        }
