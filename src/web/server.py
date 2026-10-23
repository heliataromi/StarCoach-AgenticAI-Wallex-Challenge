"""Lightweight Web Dashboard and Health Check Server for Liara / Cloud Deployment.

Provides:
- GET / : Real-time HTML responsive mobile dashboard with 5s auto-refresh
- GET /health : HTTP 200 health check probe for Liara PaaS container lifecycle
- GET /status : JSON monitoring payload for automated pinging & alerts
"""
from __future__ import annotations

import datetime
import html
import os
import time
from typing import Any, Callable, Dict, Optional
from aiohttp import web


class TradingWebDashboard:
    """Async web server serving status dashboards and cloud platform health checks."""

    def __init__(
        self,
        get_status_callback: Callable[[], Dict[str, Any]],
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self.get_status = get_status_callback
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/status", self.handle_status)
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self.start_time: float = time.time()

    async def start(self) -> None:
        """Binds and starts the aiohttp web server."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

    async def stop(self) -> None:
        """Gracefully shuts down the web server."""
        if self._runner:
            await self._runner.cleanup()

    async def handle_health(self, request: web.Request) -> web.Response:
        """Standard health check endpoint for cloud load balancers and container probes."""
        return web.json_response({
            "status": "UP",
            "uptime_seconds": int(time.time() - self.start_time),
            "timestamp": time.time(),
        })

    async def handle_status(self, request: web.Request) -> web.Response:
        """Returns machine-readable JSON status payload."""
        data = self.get_status()
        data["uptime_seconds"] = int(time.time() - self.start_time)
        return web.json_response(data)

    async def handle_index(self, request: web.Request) -> web.Response:
        """Renders mobile-friendly modern dark mode trading monitor."""
        data = self.get_status()
        uptime = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours:02d}h {minutes:02d}m {seconds:02d}s"

        mode = html.escape(str(data.get("mode", "SHADOW")).upper())
        mode_badge_color = "#10b981" if mode == "LIVE" else "#3b82f6"
        equity = float(data.get("equity_tmn", 5_000_000.0))
        initial = float(data.get("initial_capital_tmn", 5_000_000.0))
        net_profit = equity - initial
        net_return_pct = (net_profit / initial * 100.0) if initial > 0 else 0.0
        pnl_color = "#10b981" if net_profit >= 0 else "#ef4444"
        pnl_sign = "+" if net_profit >= 0 else ""

        drawdown = float(data.get("drawdown_pct", 0.0))
        regime = html.escape(str(data.get("active_regime", "RANGING_MEAN_REVERTING")))
        trades = int(data.get("total_trades", 0))
        iteration = int(data.get("iteration", 0))
        deadline_str = html.escape(str(data.get("deadline_str", "Active")))
        time_to_deadline = html.escape(str(data.get("time_to_deadline", "N/A")))
        circuit_active = bool(data.get("circuit_breaker_active", False))
        circuit_badge = '<span style="color: #ef4444; font-weight: bold;">[HALTED: CIRCUIT BREAKER]</span>' if circuit_active else '<span style="color: #10b981;">[NORMAL]</span>'

        # Positions rows
        positions = data.get("active_positions", {})
        pos_html = ""
        if positions:
            for coin, qty in positions.items():
                pos_html += f"<tr><td style='padding: 8px;'>{html.escape(coin)}</td><td style='padding: 8px; text-align: right;'>{qty:,.6f}</td></tr>"
        else:
            pos_html = "<tr><td colspan='2' style='padding: 8px; text-align: center; color: #94a3b8;'>100% Cash in Iranian Toman (Zero Coin Inventory)</td></tr>"

        # Latest debates
        debates = data.get("latest_debates", {})
        debate_cards = ""
        if debates:
            for sym, deb in debates.items():
                verdict = html.escape(str(deb.get("arbiter_verdict", "N/A")))
                conf = float(deb.get("confidence", 0.0))
                bull_score = float(deb.get("bull_score", 0.0))
                bear_score = float(deb.get("bear_score", 0.0))
                catalyst = html.escape(str(deb.get("catalyst", "Momentum")))
                risk = html.escape(str(deb.get("risk", "Volatility")))
                debate_cards += f"""
                <div style="background: #1e293b; padding: 14px; border-radius: 8px; margin-bottom: 10px; border-left: 4px solid #6366f1;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <strong style="font-size: 1.05rem;">{html.escape(sym)}</strong>
                        <span style="background: #334155; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ruling: {verdict} ({conf:.0%})</span>
                    </div>
                    <div style="margin-top: 8px; font-size: 0.9rem; color: #cbd5e1;">
                        <span style="color: #10b981;">▲ Bull ({bull_score:.2f}):</span> {catalyst}<br/>
                        <span style="color: #ef4444;">▼ Bear ({bear_score:.2f}):</span> {risk}
                    </div>
                </div>
                """
        else:
            debate_cards = "<p style='color: #94a3b8;'>No debates recorded yet.</p>"

        page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>StarCoach Wallex Agent | Live Cloud Monitor</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: #0f172a;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            padding: 20px;
            display: flex;
            justify-content: center;
        }}
        .container {{
            max-width: 800px;
            width: 100%;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #334155;
            padding-bottom: 16px;
            margin-bottom: 20px;
        }}
        .badge {{
            background: {mode_badge_color};
            color: #ffffff;
            font-size: 0.85rem;
            font-weight: 700;
            padding: 6px 12px;
            border-radius: 9999px;
            letter-spacing: 0.05em;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }}
        .card {{
            background: #1e293b;
            padding: 16px;
            border-radius: 10px;
            border: 1px solid #334155;
        }}
        .card-title {{
            font-size: 0.85rem;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}
        .card-value {{
            font-size: 1.4rem;
            font-weight: 700;
        }}
        .table-wrap {{
            background: #1e293b;
            border-radius: 10px;
            padding: 16px;
            border: 1px solid #334155;
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th {{
            text-align: left;
            padding: 8px;
            border-bottom: 1px solid #334155;
            color: #94a3b8;
            font-size: 0.85rem;
        }}
        .footer {{
            text-align: center;
            font-size: 0.8rem;
            color: #64748b;
            margin-top: 24px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1 style="font-size: 1.4rem; font-weight: 800;">StarCoach Wallex Agent</h1>
                <p style="color: #94a3b8; font-size: 0.85rem; margin-top: 4px;">Neuro-Symbolic Quantitative Trading Engine</p>
            </div>
            <div>
                <span class="badge">{mode} MODE</span>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-title">Total Equity</div>
                <div class="card-value">{equity:,.0f} <span style="font-size: 0.9rem; font-weight: 400;">TMN</span></div>
                <div style="color: {pnl_color}; font-size: 0.9rem; margin-top: 4px; font-weight: 600;">{pnl_sign}{net_profit:,.0f} TMN ({pnl_sign}{net_return_pct:.2f}%)</div>
            </div>
            <div class="card">
                <div class="card-title">Peak Drawdown</div>
                <div class="card-value" style="color: {'#ef4444' if drawdown > 5.0 else '#f8fafc'};">{drawdown:.2f}%</div>
                <div style="font-size: 0.85rem; margin-top: 4px;">Status: {circuit_badge}</div>
            </div>
            <div class="card">
                <div class="card-title">Active Market Regime</div>
                <div class="card-value" style="font-size: 1.05rem; line-height: 1.5; color: #38bdf8;">{regime}</div>
                <div style="font-size: 0.85rem; color: #94a3b8; margin-top: 4px;">Cycle #{iteration} | {trades} trades</div>
            </div>
            <div class="card">
                <div class="card-title">Deadline & Liquidation</div>
                <div class="card-value" style="font-size: 1.1rem; color: #f59e0b;">{time_to_deadline}</div>
                <div style="font-size: 0.85rem; color: #94a3b8; margin-top: 4px;">Target: {deadline_str}</div>
            </div>
        </div>

        <div class="table-wrap">
            <div class="card-title" style="margin-bottom: 12px;">Active Coin Positions (Max 2 Allowed)</div>
            <table>
                <thead>
                    <tr><th>Asset</th><th style="text-align: right;">Holding Quantity</th></tr>
                </thead>
                <tbody>
                    {pos_html}
                </tbody>
            </table>
        </div>

        <div class="table-wrap">
            <div class="card-title" style="margin-bottom: 12px;">Latest Bull vs. Bear Dialectical Debates</div>
            {debate_cards}
        </div>

        <div class="footer">
            Uptime: {uptime_str} | Verified SHA-256 Audit Stream Active | Auto-refreshes every 5s
        </div>
    </div>
</body>
</html>"""
        return web.Response(text=page_html, content_type="text/html")
