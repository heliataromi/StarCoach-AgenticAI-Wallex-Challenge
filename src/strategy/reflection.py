"""Post-Trade Reflection & Episodic Memory Subsystem.

Implements the FinMem self-reflective learning pattern:
- Conducts post-mortem analysis on closed round-trip trades
- Maintains an in-memory rolling episodic memory buffer
- Extracts actionable behavioral lessons to condition subsequent Bull vs. Bear debates
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import time
from typing import Any, Deque, Dict, List, Optional

from src.strategy.models import MarketRegime


@dataclass(frozen=True)
class TradePostMortem:
    """Quantitative and qualitative reflection on a completed round-trip trade."""
    trade_id: str
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_tmn: float
    pnl_pct: float
    fee_tmn: float
    regime: MarketRegime
    exit_reason: str
    lesson_learned: str
    is_win: bool
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        """Converts post-mortem into a JSON-serializable dictionary for audit logging."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(self.exit_price, 2),
            "quantity": round(self.quantity, 6),
            "pnl_tmn": round(self.pnl_tmn, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "fee_tmn": round(self.fee_tmn, 2),
            "regime": self.regime.value,
            "exit_reason": self.exit_reason,
            "lesson_learned": self.lesson_learned,
            "is_win": self.is_win,
            "timestamp": self.timestamp,
        }


class EpisodicMemoryBuffer:
    """Rolling memory buffer storing trade post-mortems and distilling lessons."""

    def __init__(self, max_records: int = 50) -> None:
        self.max_records = max_records
        self._history: Deque[TradePostMortem] = deque(maxlen=max_records)

    def record_completed_trade(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        pnl_tmn: float,
        fee_tmn: float,
        regime: MarketRegime = MarketRegime.BULLISH_TREND,
        exit_reason: str = "Target or stop executed",
        trade_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> TradePostMortem:
        """Analyzes a closed trade, generates a structured post-mortem, and appends to memory."""
        now = timestamp if timestamp is not None else time.time()
        t_id = trade_id or f"pm-{symbol}-{int(now * 1000)}"

        cost_basis = entry_price * quantity if (entry_price * quantity) > 0 else 1.0
        pnl_pct = (pnl_tmn / cost_basis) * 100.0
        is_win = (pnl_tmn > 0)

        # Distill qualitative lesson
        reason_str = f" [{exit_reason}]" if exit_reason else ""
        if is_win:
            if pnl_pct >= 2.0:
                lesson = f"Strong breakout momentum captured on {symbol} (+{pnl_pct:.2f}%){reason_str}. Trend entry rules validated."
            else:
                lesson = f"Modest mean-reversion gain on {symbol} (+{pnl_pct:.2f}%){reason_str}. Quick profit-taking avoided re-entry whipsaw."
        else:
            if abs(pnl_pct) <= 1.0:
                lesson = f"Small contained loss on {symbol} ({pnl_pct:.2f}%){reason_str}. Dynamic trailing stop prevented deeper capital drawdown."
            else:
                lesson = f"Adverse move on {symbol} ({pnl_pct:.2f}%){reason_str}. False breakout stopped out; tighten volatility buffer before trading against resistance."

        pm = TradePostMortem(
            trade_id=t_id,
            symbol=symbol,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            pnl_tmn=pnl_tmn,
            pnl_pct=pnl_pct,
            fee_tmn=fee_tmn,
            regime=regime,
            exit_reason=exit_reason,
            lesson_learned=lesson,
            is_win=is_win,
            timestamp=now,
        )

        self._history.append(pm)
        return pm

    def get_recent_lessons(self, symbol: Optional[str] = None, limit: int = 3) -> List[str]:
        """Retrieves most recent lessons for the given symbol to condition the debate."""
        lessons: List[str] = []
        for pm in reversed(self._history):
            if symbol is None or pm.symbol == symbol:
                lessons.append(pm.lesson_learned)
                if len(lessons) >= limit:
                    break
        return lessons

    def get_recent_win_rate(self, limit: int = 10) -> float:
        """Calculates win rate over recent trades."""
        if not self._history:
            return 0.0
        sample = list(self._history)[-limit:]
        wins = sum(1 for pm in sample if pm.is_win)
        return (wins / len(sample)) * 100.0

    def generate_reflection_summary(self) -> str:
        """Returns executive performance reflection for macro decision context."""
        if not self._history:
            return "No closed trades in episodic memory. Strategy starting from baseline posture."

        total = len(self._history)
        wins = sum(1 for pm in self._history if pm.is_win)
        total_pnl = sum(pm.pnl_tmn for pm in self._history)
        win_rate = (wins / total) * 100.0

        latest = self._history[-1]
        return (
            f"Episodic memory: {total} completed trades | Win Rate: {win_rate:.1f}% | "
            f"Net PnL: {total_pnl:,.0f} TMN. Latest insight: {latest.lesson_learned}"
        )

    @property
    def history(self) -> List[TradePostMortem]:
        """Returns read-only list of recorded post-mortems."""
        return list(self._history)
