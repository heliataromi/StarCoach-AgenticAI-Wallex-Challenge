"""Unit tests for Unified LLM Gateway (Gemini Free Tier & BoxAPI Production).

Verifies:
- .env parsing and environment variable injection
- Google AI Studio Gemini free tier configuration ($0.00 spend accounting)
- BoxAPI hackathon credit budget guardian
- Multi-tier model routing (gpt-6-astra vs gpt-5.4-mini)
- Mocked HTTP requests and resilient fallback to quantitative engine
"""
from __future__ import annotations

import json
import os
import unittest.mock as mock
import pytest

from src.strategy.llm_gateway import LLMGateway, BoxAPIGateway, GatewayStats, load_dotenv
from src.strategy.macro_agent import MacroAgent
from src.strategy.models import MarketRegime
from src.wallex.models import Candle


class TestDotEnvLoader:
    """Verifies .env parser and environment variable loading."""

    def test_load_dotenv_parses_keys_and_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# Comment line\n"
            "BOXAPI_API_KEY=test_key_123\n"
            'GEMINI_API_KEY="test_gemini_456"\n'
            "EMPTY_VAL=\n"
            "SPACED_KEY = spaced_val \n"
        )
        loaded = load_dotenv(env_file)
        assert loaded.get("BOXAPI_API_KEY") == "test_key_123"
        assert loaded.get("GEMINI_API_KEY") == "test_gemini_456"
        assert loaded.get("SPACED_KEY") == "spaced_val"
        # Cleanup
        os.environ.pop("BOXAPI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("SPACED_KEY", None)

    def test_load_dotenv_missing_file_returns_empty(self, tmp_path):
        assert load_dotenv(tmp_path / "nonexistent.env") == {}


class TestLLMGatewayProviders:
    """Verifies multi-provider routing (Gemini Free Tier vs BoxAPI)."""

    def test_gemini_provider_configuration_and_zero_cost(self):
        gw = LLMGateway(provider="gemini", api_key="test-gemini-key")
        assert gw.is_configured is True
        assert gw.provider == "gemini"
        assert "generativelanguage.googleapis.com" in gw.base_url
        assert gw.macro_model in ["gemini-1.5-flash", "gemini-3.8-flash", os.environ.get("GEMINI_MODEL", "")]
        # Free tier is never budget-exhausted
        assert gw.budget_exhausted is False

    def test_boxapi_provider_configuration_and_budget_cap(self):
        gw = LLMGateway(provider="boxapi", api_key="sk-test", budget_limit_usd=1.0)
        assert gw.is_configured is True
        assert gw.provider == "boxapi"
        assert "ai.boxapi.ir" in gw.base_url
        assert gw.debate_model == "gpt-6-astra"
        assert gw.macro_model == "gpt-5.4-mini"

        # Exceed budget
        gw.stats.estimated_cost_usd = 1.05
        assert gw.budget_exhausted is True
        assert gw.chat_completion([{"role": "user", "content": "hi"}]) is None

    def test_gateway_not_configured_without_key(self):
        gw = LLMGateway(provider="gemini", api_key="")
        assert gw.is_configured is False
        assert gw.chat_completion([{"role": "user", "content": "hi"}]) is None

    @mock.patch("requests.post")
    def test_gemini_successful_chat_completion(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"decision": "BUY", "confidence": 0.85})}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }
        mock_post.return_value = mock_resp

        gw = LLMGateway(provider="gemini", api_key="test-key")
        res = gw.chat_completion([{"role": "user", "content": "test"}])

        assert res == {"decision": "BUY", "confidence": 0.85}
        assert gw.stats.total_calls == 1
        assert gw.stats.successful_calls == 1
        # Free tier: cost remains $0.00
        assert gw.stats.estimated_cost_usd == 0.0

    @mock.patch("requests.post")
    def test_boxapi_successful_chat_completion_and_cost_accounting(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"decision": "HOLD", "confidence": 0.80})}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }
        mock_post.return_value = mock_resp

        gw = LLMGateway(provider="boxapi", api_key="sk-boxapi")
        res = gw.chat_completion([{"role": "user", "content": "test"}])

        assert res == {"decision": "HOLD", "confidence": 0.80}
        assert gw.stats.total_calls == 1
        assert gw.stats.successful_calls == 1
        assert gw.stats.estimated_cost_usd > 0.0

    @mock.patch("requests.post")
    def test_http_error_falls_back_gracefully(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        gw = LLMGateway(provider="boxapi", api_key="invalid-key")
        res = gw.chat_completion([{"role": "user", "content": "test"}])
        assert res is None
        assert gw.stats.failed_calls == 1

    @mock.patch("requests.post")
    def test_adjudicate_debate_invokes_debate_model(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "verdict": "APPROVED_BULLISH_TREND",
                        "recommended_regime": "BULLISH_TREND",
                        "confidence": 0.88,
                        "max_allocation_pct": 0.20,
                        "summary_reasoning": "Breakout validated above resistance.",
                    })
                }
            }],
            "usage": {"prompt_tokens": 800, "completion_tokens": 150},
        }
        mock_post.return_value = mock_resp

        gw = LLMGateway(provider="gemini", api_key="test-key")
        adj = gw.adjudicate_debate(
            symbol="BTCTMN",
            bull_thesis={"bull_score": 0.75, "rationale": "EMA cross"},
            bear_thesis={"bear_score": 0.30, "rationale": "Resistance test"},
        )
        assert adj is not None
        assert adj["verdict"] == "APPROVED_BULLISH_TREND"
        assert adj["recommended_regime"] == "BULLISH_TREND"

    @mock.patch("requests.post")
    def test_macro_agent_integration_with_llm_gateway(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
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
        mock_post.return_value = mock_resp

        gw = LLMGateway(provider="gemini", api_key="test-key")
        agent = MacroAgent(allowed_symbols=["BTCTMN"], llm_gateway=gw)

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
