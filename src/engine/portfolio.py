"""Real-time portfolio and position state tracker.

Maintains in-memory position lifecycle, mark-to-market equity calculations,
and synchronization with Wallex account state.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional
from src.risk.drawdown import DrawdownSentinel
from src.risk.models import AccountSnapshot, PositionRiskState, RiskConfig
from src.risk.sizing import PositionSizer
from src.engine.models import OrderExecutionResult
from src.wallex.models import AccountBalance, OrderSide


class PortfolioTracker:
    """Manages active positions, mark-to-market equity, and risk sentinels."""

    def __init__(
        self,
        initial_equity_tmn: float = 5_000_000.0,
        drawdown_sentinel: Optional[DrawdownSentinel] = None,
        position_sizer: Optional[PositionSizer] = None,
    ) -> None:
        self.initial_equity_tmn = float(initial_equity_tmn)
        self.config = RiskConfig(initial_capital_tmn=self.initial_equity_tmn)
        self.drawdown_sentinel = drawdown_sentinel or DrawdownSentinel(config=self.config)
        self.position_sizer = position_sizer or PositionSizer(config=self.config)

        self.tmn_available = float(initial_equity_tmn)
        self.tmn_locked = 0.0
        self.crypto_balances: Dict[str, float] = {}
        self.crypto_locked: Dict[str, float] = {}
        self.active_positions: Dict[str, PositionRiskState] = {}
        self.latest_prices: Dict[str, float] = {
            "BTCTMN": 4_200_000_000.0,
            "ETHTMN": 180_000_000.0,
            "USDTTMN": 65_000.0,
        }

        self.total_realized_pnl_tmn: float = 0.0
        self.trade_count: int = 0
        self.trade_history: List[Dict[str, Any]] = []

    def sync_from_exchange_balance(self, balance: AccountBalance) -> None:
        """Synchronizes internal balances from exchange query."""
        self.tmn_available = balance.toman_available
        self.tmn_locked = balance.toman_locked
        for base, amount in balance.crypto_balances.items():
            self.crypto_balances[base] = amount

    def update_market_price(self, symbol: str, price: float, atr_value: float = 0.0) -> None:
        """Updates current price and refreshes position trailing stops."""
        if price <= 0:
            return
        self.latest_prices[symbol] = price
        if symbol in self.active_positions:
            pos = self.active_positions[symbol]
            self.position_sizer.update_trailing_stop(pos, price, atr_value)

    def calculate_total_equity(self) -> float:
        """Calculates exact mark-to-market portfolio equity in Iranian Toman (TMN)."""
        holdings: Dict[str, float] = {}
        for base, qty in self.crypto_balances.items():
            locked = self.crypto_locked.get(base, 0.0)
            holdings[base] = qty + locked

        snapshot = self.drawdown_sentinel.update_equity(
            tmn_available=self.tmn_available,
            tmn_locked=self.tmn_locked,
            crypto_holdings=holdings,
            market_prices=self.latest_prices,
        )
        return snapshot.total_equity_tmn

    def record_fill(self, result: OrderExecutionResult) -> None:
        """Updates internal portfolio state upon an order execution fill."""
        if not result.success or result.executed_quantity <= 0:
            return

        self.trade_count += 1
        symbol = result.symbol
        base_asset = symbol.replace("TMN", "")
        price = result.executed_price
        qty = result.executed_quantity
        fee = result.fee_paid
        cost_tmn = price * qty

        if result.side == OrderSide.BUY:
            # Update balances
            self.tmn_available = max(0.0, self.tmn_available - (cost_tmn + fee))
            self.crypto_balances[base_asset] = self.crypto_balances.get(base_asset, 0.0) + qty

            # Open or update position
            if symbol in self.active_positions:
                prev = self.active_positions[symbol]
                new_qty = prev.quantity + qty
                new_entry = ((prev.entry_price * prev.quantity) + (price * qty)) / new_qty
                stop = max(prev.stop_loss_price, price * (1.0 - self.config.max_stop_loss_pct))
                self.active_positions[symbol] = PositionRiskState(
                    symbol=symbol,
                    side="BUY",
                    entry_price=new_entry,
                    quantity=new_qty,
                    cost_tmn=new_entry * new_qty,
                    current_price=price,
                    stop_loss_price=stop,
                    take_profit_price=price * 1.03,
                    highest_price_seen=max(prev.highest_price_seen, price),
                    opened_at=prev.opened_at,
                )
            else:
                self.active_positions[symbol] = PositionRiskState(
                    symbol=symbol,
                    side="BUY",
                    entry_price=price,
                    quantity=qty,
                    cost_tmn=cost_tmn,
                    current_price=price,
                    stop_loss_price=price * (1.0 - self.config.max_stop_loss_pct),
                    take_profit_price=price * 1.03,
                    highest_price_seen=price,
                    opened_at=result.timestamp,
                )
        elif result.side == OrderSide.SELL:
            # Update balances
            proceeds = max(0.0, cost_tmn - fee)
            self.tmn_available += proceeds
            self.crypto_balances[base_asset] = max(
                0.0, self.crypto_balances.get(base_asset, 0.0) - qty
            )

            # Close or reduce position
            if symbol in self.active_positions:
                pos = self.active_positions[symbol]
                closed_qty = min(pos.quantity, qty)
                pnl = (price - pos.entry_price) * closed_qty - fee
                self.total_realized_pnl_tmn += pnl
                self.trade_history.append({
                    "symbol": symbol,
                    "entry_price": pos.entry_price,
                    "exit_price": price,
                    "quantity": closed_qty,
                    "pnl_tmn": pnl,
                    "fee_tmn": fee,
                    "timestamp": result.timestamp,
                })

                if qty >= pos.quantity:
                    del self.active_positions[symbol]
                else:
                    rem_qty = pos.quantity - qty
                    self.active_positions[symbol] = PositionRiskState(
                        symbol=symbol,
                        side="BUY",
                        entry_price=pos.entry_price,
                        quantity=rem_qty,
                        cost_tmn=pos.entry_price * rem_qty,
                        current_price=price,
                        stop_loss_price=pos.stop_loss_price,
                        take_profit_price=pos.take_profit_price,
                        highest_price_seen=pos.highest_price_seen,
                        opened_at=pos.opened_at,
                    )

        # Sync active positions to sentinel for monitoring
        self.drawdown_sentinel.active_positions = self.active_positions
        self.calculate_total_equity()

    def get_position(self, symbol: str) -> Optional[PositionRiskState]:
        """Returns the active position for a given symbol, if any."""
        return self.active_positions.get(symbol)

    def has_active_position(self, symbol: str) -> bool:
        """Checks if there is an active open position for a symbol."""
        return symbol in self.active_positions and self.active_positions[symbol].quantity > 0
