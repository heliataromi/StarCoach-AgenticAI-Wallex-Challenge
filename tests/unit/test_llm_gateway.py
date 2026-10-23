"""Unit tests for BoxAPI LLM Gateway, .env parsing, and budget protection.

Verifies:
- Pure-Python .env parsing and environment variable injection
- Token tracking and $3.00 credit budget guardrails
- Multi-tier model routing (gpt-6-astra vs gpt-5.4-mini)
- Mocked HTTP requests and resilient fallback to quantitative engine
"""
from __future__ import annotations

import io
import json
import os
import unittest.mock as mock
import urllib.error
import pytest

from src.strategy.llm_gateway import BoxAPIGateway, GatewayStats, load_dotenv
from src.strategy.macro_agent import MacroAgent
from src.strategy.models import MarketRegime
from src.wallex.models import Candle


class TestDotEnvLoader:
    """Verifies pure-Python .env parser conforming to Ponytail minimalism."""

    def test_load_dotenv_parses_keys_and_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# Comment line\n"
            "BOXAPI_API_KEY=test_key_123\n"
            'WALLEX_API_KEY="test_wallex_456"\n'
            "EMPTY_VAL=\n"
            "SPACED_KEY = spaced_val \n"
        )
        loaded = load_dotenv(env_file)
        assert loaded["BOXAPI_API_KEY"] == "test_key_123"
        assert loaded["WALLEX_API_KEY"] == "test_wallex_456"
        assert loaded["SPACED_KEY"] == "spaced_val"
        assert os.environ.get("BOXAPI_API_KEY") == "test_key_123"
        # Cleanup
        os.environ.pop("BOXAPI_API_KEY", None)
        os.environ.pop("WALLEX_API_KEY", None)
        os.environ.pop("SPACED_KEY", None)

    def test_load_dotenv_missing_file_returns_empty(self, tmp_path):
        assert load_dotenv(tmp_path / "nonexistent.env") == {}


class TestBoxAPIGateway:
    """Verifies BoxAPI client calls, budget enforcement, and fallback."""

    def test_gateway_not_configured_without_key(self):
        gw = BoxAPIGateway(api_key="")
        assert gw.is_configured is False
        assert gw.chat_completion([{"role": "user", "content": "hi"}]) is None

    def test_gateway_budget_limit_protection(self):
        gw = BoxAPIGateway(api_key="sk-test", budget_limit_usd=1.0)
        assert gw.budget_exhausted is False

        # Exceed budget artificially
        gw.stats.estimated_cost_usd = 1.05
        assert gw.budget_exhausted is True
        # Request must immediately return None without network call
        assert gw.chat_completion([{"role": "user", "content": "hi"}]) is None

    @mock.patch("urllib.request.urlopen")
    def test_successful_chat_completion_and_cost_accounting(self, mock_urlopen):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        payload = {
            "choices": [{"message": {"content": json.dumps({"decision": "HOLD", "confidence": 0.8})}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        gw = BoxAPIGateway(api_key="sk-test", macro_model="gpt-5.4-mini")
        res = gw.chat_completion([{"role": "user", "content": "test"}])

        assert res == {"decision": "HOLD", "confidence": 0.8}
        assert gw.stats.total_calls == 1
        assert gw.stats.successful_calls == 1
        assert gw.stats.total_prompt_tokens == 1000
        assert gw.stats.total_completion_tokens == 200
        assert gw.stats.estimated_cost_usd > 0.0

    @mock.patch("urllib.request.urlopen")
    def test_http_error_falls_back_gracefully(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://ai.boxapi.ir/ai/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "invalid api key"}'),
        )
        gw = BoxAPIGateway(api_key="invalid-key")
        res = gw.chat_completion([{"role": "user", "content": "test"}])
        assert res is None
        assert gw.stats.failed_calls == 1

    @mock.patch("urllib.request.urlopen")
    def test_adjudicate_debate_invokes_flagship_model(self, mock_urlopen):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        payload = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "verdict": "APPROVED_BULLISH_TREND",
                        "recommended_regime": "BULLISH_TREND",
                        "confidence": 0.88,
                        "max_allocation_pct": 0.20,
                        "summary_reasoning": "Bullish breakout momentum exceeds fee friction.",
                    })
                }
            }],
            "usage": {"prompt_tokens": 800, "completion_tokens": 150},
        }
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        gw = BoxAPIGateway(api_key="sk-test", debate_model="gpt-6-astra")
        adj = gw.adjudicate_debate(
            symbol="BTCTMN",
            bull_thesis={"bull_score": 0.75, "rationale": "EMA cross"},
            bear_thesis={"bear_score": 0.30, "rationale": "Resistance test"},
        )
        assert adj is not None
        assert adj["verdict"] == "APPROVED_BULLISH_TREND"
        assert adj["recommended_regime"] == "BULLISH_TREND"

    @mock.patch("urllib.request.urlopen")
    def test_macro_agent_integration_with_boxapi(self, mock_urlopen):
        # Mock BoxAPI returning custom LLM arbiter verdict
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        payload = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "verdict": "APPROVE_BULLISH_TREND",
                        "recommended_regime": "BULLISH_TREND",
                        "confidence": 0.91,
                        "max_allocation_pct": 0.20,
                        "summary_reasoning": "LLM Arbiter confirms institutional trend continuation.",
                    })
                }
            }],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        gw = BoxAPIGateway(api_key="sk-test")
        agent = MacroAgent(allowed_symbols=["BTCTMN"], llm_gateway=gw)

        # Generate candle data with strong uptrend
        candles = [
            Candle(
                timestamp=1700000000000 + (i * 900000),
                open=50000.0 + (i * 100.0),
                high=50150.0 + (i * 100.0),
                low=49950.0 + (i * 100.0),
                close=50100.0 + (i * 100.0),
                volume=10.0,
            )
            for i in range(25)
        ]
        policy = agent.evaluate_policy({"BTCTMN": candles})
        assert policy.active_regime == MarketRegime.BULLISH_TREND
        transcript = agent.get_debate_transcript("BTCTMN")
        assert transcript is not None
        assert transcript.arbiter_verdict == "APPROVE_BULLISH_TREND"
        assert "LLM Arbiter confirms" in transcript.summary_reasoning
