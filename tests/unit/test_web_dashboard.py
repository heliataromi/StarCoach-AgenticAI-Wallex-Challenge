"""Unit tests for Web Dashboard and Cloud Health Check Server (Liara Deployment)."""
from __future__ import annotations

import asyncio
import time
import httpx
import pytest
from run_engine import parse_deadline
from src.web.server import TradingWebDashboard


class TestWebDashboard:
    """Verifies health probes, status endpoint, and HTML dashboard rendering."""

    def test_parse_deadline(self):
        assert parse_deadline(None) is None
        assert parse_deadline("") is None
        # Numeric epoch
        assert parse_deadline("1700000000") == 1700000000.0
        # ISO timestamp
        ts = parse_deadline("2026-10-26T18:00:00+03:30")
        assert isinstance(ts, float)
        assert ts > 0

    def test_web_dashboard_endpoints(self):
        async def _test():
            sample_state = {
                "mode": "SHADOW",
                "equity_tmn": 5_042_000.0,
                "initial_capital_tmn": 5_000_000.0,
                "drawdown_pct": 0.25,
                "active_regime": "BULLISH_TREND",
                "total_trades": 3,
                "iteration": 12,
                "deadline_str": "2026-10-26T18:00:00+03:30",
                "time_to_deadline": "48h 12m remaining",
                "circuit_breaker_active": False,
                "active_positions": {"BTC": 0.00025},
                "latest_debates": {
                    "BTCTMN": {
                        "arbiter_verdict": "APPROVE_BULLISH_TREND",
                        "confidence": 0.85,
                        "bull_score": 0.90,
                        "bear_score": 0.15,
                        "catalyst": "EMA golden cross",
                        "risk": "Overhead resistance",
                    }
                },
            }

            dashboard = TradingWebDashboard(
                get_status_callback=lambda: sample_state,
                host="127.0.0.1",
                port=9123,
            )
            await dashboard.start()

            try:
                async with httpx.AsyncClient(base_url="http://127.0.0.1:9123") as client:
                    # 1. Health check probe
                    res_health = await client.get("/health")
                    assert res_health.status_code == 200
                    data_health = res_health.json()
                    assert data_health["status"] == "UP"
                    assert "uptime_seconds" in data_health

                    # 2. Status JSON endpoint
                    res_status = await client.get("/status")
                    assert res_status.status_code == 200
                    data_status = res_status.json()
                    assert data_status["equity_tmn"] == 5_042_000.0
                    assert data_status["mode"] == "SHADOW"
                    assert data_status["active_regime"] == "BULLISH_TREND"

                    # 3. HTML Dashboard
                    res_index = await client.get("/")
                    assert res_index.status_code == 200
                    assert "StarCoach Wallex Agent" in res_index.text
                    assert "5,042,000" in res_index.text
                    assert "SHADOW MODE" in res_index.text
                    assert "BULLISH_TREND" in res_index.text
            finally:
                await dashboard.stop()

        asyncio.run(_test())
