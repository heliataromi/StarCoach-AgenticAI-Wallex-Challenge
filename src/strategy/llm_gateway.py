"""Unified LLM Gateway for StarCoach Wallex Algorithmic Trading Challenge.

Supports:
1. Google AI Studio Gemini (100% Free Development & Testing Tier):
   - Base URL: https://generativelanguage.googleapis.com/v1beta/openai
   - Models: gemini-1.5-flash, gemini-2.0-flash
   - Preserves 100% of competition credit during development.
2. BoxAPI (Official StarCoach Hackathon Production Tier):
   - Base URL: https://ai.boxapi.ir/ai/v1
   - Models: gpt-6-astra (flagship debate arbiter), gpt-5.4-mini (macro surveillance & reflection)
   - $3.00 credit management with active Budget Guardian.
3. Offline Deterministic Mode:
   - Zero network dependency, instant sub-millisecond execution for testing and CI.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from dotenv import load_dotenv as _py_load_dotenv
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.error
    import urllib.request

logger = logging.getLogger("llm_gateway")


def load_dotenv(dotenv_path: Optional[Union[str, Path]] = None) -> Dict[str, str]:
    """Loads environment variables from .env using python-dotenv or fallback parser."""
    env_file = Path(dotenv_path) if dotenv_path else Path(".env")
    if not env_file.is_file():
        return {}

    if HAS_DOTENV:
        _py_load_dotenv(dotenv_path=env_file)
        return dict(os.environ)

    # Standard library fallback parser
    loaded: Dict[str, str] = {}
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    loaded[key] = val
                    if key not in os.environ:
                        os.environ[key] = val
    except Exception as e:
        logger.warning(f"Failed to read .env file at {env_file}: {e}")
    return loaded


@dataclass
class GatewayStats:
    """Tracks token usage and estimated spend against budget constraints."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


class LLMGateway:
    """Unified OpenAI-compatible Gateway supporting BoxAPI and Google AI Studio Gemini."""

    MODEL_PRICING = {
        # Google AI Studio Free Tier ($0.00)
        "gemini-flash-lite-latest": {"prompt": 0.00, "completion": 0.00},
        "gemini-flash-latest": {"prompt": 0.00, "completion": 0.00},
        "gemini-3.8-flash": {"prompt": 0.00, "completion": 0.00},
        "gemini-3.6-flash": {"prompt": 0.00, "completion": 0.00},
        "gemini-3.5-flash-lite": {"prompt": 0.00, "completion": 0.00},
        "gemini-3.1-flash-lite": {"prompt": 0.00, "completion": 0.00},
        "gemini-3.1-pro-preview": {"prompt": 0.00, "completion": 0.00},
        "gemini-3-flash-preview": {"prompt": 0.00, "completion": 0.00},
        "gemini-4-argon": {"prompt": 0.00, "completion": 0.00},
        "gemini-2.5-flash": {"prompt": 0.00, "completion": 0.00},
        "gemini-2.5-flash-lite": {"prompt": 0.00, "completion": 0.00},
        "gemini-2.5-pro": {"prompt": 0.00, "completion": 0.00},
        "gemini-2.0-flash": {"prompt": 0.00, "completion": 0.00},
        "gemini-1.5-flash": {"prompt": 0.00, "completion": 0.00},
        # BoxAPI Hackathon Tier (per 1M tokens)
        "gpt-5.4-nano": {"prompt": 0.02, "completion": 0.08},
        "gpt-5.4-mini": {"prompt": 0.09, "completion": 0.36},
        "gpt-5.4": {"prompt": 1.25, "completion": 5.00},
        "gpt-5-mini": {"prompt": 0.15, "completion": 0.60},
        "gpt-6-astra": {"prompt": 0.60, "completion": 2.40},
        "gpt-6.1-sol": {"prompt": 2.50, "completion": 10.00},
        "gpt-5.6-terra": {"prompt": 1.00, "completion": 4.00},
    }

    def __init__(
        self,
        provider: str = "auto",  # "gemini" | "boxapi" | "offline" | "auto"
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        macro_model: Optional[str] = None,
        debate_model: Optional[str] = None,
        reflection_model: Optional[str] = None,
        budget_limit_usd: float = 2.50,
        timeout_seconds: float = 15.0,
        enable_network: bool = True,
    ) -> None:
        load_dotenv()

        # Resolve provider
        if provider == "auto":
            if os.environ.get("BOXAPI_API_KEY"):
                provider = "boxapi"
            elif os.environ.get("GEMINI_API_KEY"):
                provider = "gemini"
            else:
                provider = "offline"

        self.provider = provider.lower()
        self.enable_network = enable_network and (self.provider != "offline")
        self.timeout_seconds = timeout_seconds
        self.budget_limit_usd = budget_limit_usd

        # Provider-specific configurations
        if self.provider == "gemini":
            self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
            self.base_url = (base_url or os.environ.get("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
            self.macro_model = macro_model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
            self.debate_model = debate_model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
            self.reflection_model = reflection_model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        elif self.provider == "boxapi":
            self.api_key = api_key if api_key is not None else (os.environ.get("BOXAPI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""))
            self.base_url = (base_url or os.environ.get("BOXAPI_BASE_URL") or "https://ai.boxapi.ir/ai/v1").rstrip("/")
            self.macro_model = macro_model or os.environ.get("BOXAPI_MINI_MODEL", "gpt-5.4-mini")
            self.debate_model = debate_model or os.environ.get("BOXAPI_MODEL", "gpt-6-astra")
            self.reflection_model = reflection_model or os.environ.get("BOXAPI_MINI_MODEL", "gpt-5.4-mini")
        else:
            # Offline mode
            self.api_key = api_key or ""
            self.base_url = base_url or ""
            self.macro_model = macro_model or "offline"
            self.debate_model = debate_model or "offline"
            self.reflection_model = reflection_model or "offline"

        self.stats = GatewayStats()

    @property
    def is_configured(self) -> bool:
        """True if network is enabled and an API key is present."""
        return self.enable_network and bool(self.api_key)

    @property
    def budget_exhausted(self) -> bool:
        """True if spend exceeds safety budget (only applies to paid providers like BoxAPI)."""
        if self.provider == "gemini":
            return False  # Google AI Studio free tier
        return self.stats.estimated_cost_usd >= self.budget_limit_usd

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 500,
        response_format_json: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Dispatches an OpenAI-compatible /chat/completions request.
        
        Returns parsed JSON dict, or None on failure/budget exhaustion (triggering fallback).
        """
        if not self.is_configured:
            return None

        if self.budget_exhausted:
            logger.warning(
                f"[{self.provider.upper()} Budget Guard] Spend limit (${self.budget_limit_usd:.2f}) reached. "
                f"Defaulting to quantitative engine."
            )
            return None

        target_model = model or self.macro_model
        endpoint = f"{self.base_url}/chat/completions"

        # Determine candidate models with intelligent fallback for transient provider spikes
        candidate_models = [target_model]
        if self.provider == "gemini":
            for fb in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]:
                if fb not in candidate_models:
                    candidate_models.append(fb)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "StarCoach-TradingEngine/1.0",
        }

        self.stats.total_calls += 1

        for curr_model in candidate_models:
            payload: Dict[str, Any] = {
                "model": curr_model,
                "messages": messages,
            }
            if self.provider == "boxapi" or any(prefix in curr_model for prefix in ["gpt-5", "gpt-6", "o1", "o3", "o4"]):
                payload["max_completion_tokens"] = max_tokens
            else:
                payload["temperature"] = temperature
                payload["max_tokens"] = max_tokens
            if response_format_json:
                payload["response_format"] = {"type": "json_object"}

            try:
                if HAS_REQUESTS:
                    resp = requests.post(endpoint, json=payload, headers=headers, timeout=self.timeout_seconds)
                    status_code = resp.status_code
                    if status_code != 200:
                        if status_code in (503, 429, 500, 502) and len(candidate_models) > 1 and curr_model != candidate_models[-1]:
                            logger.info(f"[{self.provider.upper()}] Model {curr_model} returned {status_code}; trying fallback...")
                            continue
                        self.stats.failed_calls += 1
                        logger.warning(f"[{self.provider.upper()} Error] HTTP {status_code}: {resp.text[:200]}")
                        return None
                    data = resp.json()
                else:
                    req_data = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                        status_code = resp.getcode()
                        raw_body = resp.read().decode("utf-8")
                    if status_code != 200:
                        self.stats.failed_calls += 1
                        return None
                    data = json.loads(raw_body)

                self.stats.successful_calls += 1

                # Token tracking & budget accounting
                usage = data.get("usage", {})
                p_tokens = usage.get("prompt_tokens", 0)
                c_tokens = usage.get("completion_tokens", 0)
                self.stats.total_prompt_tokens += p_tokens
                self.stats.total_completion_tokens += c_tokens

                rates = self.MODEL_PRICING.get(curr_model, {"prompt": 0.50, "completion": 2.00})
                call_cost = (p_tokens / 1_000_000.0 * rates["prompt"]) + (c_tokens / 1_000_000.0 * rates["completion"])
                self.stats.estimated_cost_usd += call_cost

                choices = data.get("choices", [])
                if not choices:
                    return None

                content = choices[0].get("message", {}).get("content", "")
                if response_format_json:
                    clean = content.strip()
                    if clean.startswith("```"):
                        lines = clean.splitlines()
                        clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
                    return json.loads(clean)
                return {"content": content}

            except Exception as e:
                if len(candidate_models) > 1 and curr_model != candidate_models[-1]:
                    logger.info(f"[{self.provider.upper()}] Model {curr_model} failed ({e}); trying fallback...")
                    continue
                self.stats.failed_calls += 1
                logger.warning(f"[{self.provider.upper()} Gateway Fallback] Call failed: {e}. Defaulting to quantitative engine.")
                return None

        self.stats.failed_calls += 1
        return None

    def evaluate_macro_regime(
        self,
        symbol: str,
        indicators: Dict[str, Any],
        recent_lessons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluates macro market regime."""
        system_prompt = (
            "You are the Macro Regime Strategist for an autonomous crypto trading fund operating "
            "on the Wallex exchange with a 5,000,000 TMN credit facility.\n"
            "MANDATE: Capital preservation is paramount. Wallex charges 0.50% round-trip fees.\n"
            "If market conditions are mixed or neutral, default to RANGING_SIDEWAYS or HOLD.\n"
            "Return valid JSON ONLY matching schema:\n"
            "{\n"
            '  "regime": "BULLISH_TREND" | "RANGING_SIDEWAYS" | "HIGH_VOLATILITY_PANIC",\n'
            '  "confidence": float (0.0 to 1.0),\n'
            '  "allowed": boolean,\n'
            '  "rationale": string\n'
            "}"
        )
        user_content = {
            "symbol": symbol,
            "market_indicators": indicators,
            "recent_lessons": recent_lessons or [],
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_content)},
        ]
        return self.chat_completion(messages, model=self.macro_model, max_tokens=250)

    def adjudicate_debate(
        self,
        symbol: str,
        bull_thesis: Dict[str, Any],
        bear_thesis: Dict[str, Any],
        recent_lessons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Adjudicates dialectical Bull vs Bear debate."""
        system_prompt = (
            "You are the Chief Risk Arbiter presiding over an adversarial Bull vs. Bear debate "
            "for an institutional crypto portfolio on Wallex.\n"
            "EVALUATION CRITERIA:\n"
            "1. Assert round-trip fee friction (0.50%). Net edge must exceed +0.35% JEV.\n"
            "2. Stress-test downside hazards. If bear risk is severe, issue DEFENSIVE_HALT.\n"
            "3. If Bull demonstrates superior risk-reward and buyers absorb sells, issue APPROVED_BULLISH_TREND.\n"
            "4. If market is balanced and range-bound, issue APPROVE_RANGE_SCALPING.\n"
            "Return valid JSON ONLY matching schema:\n"
            "{\n"
            '  "verdict": "APPROVED_BULLISH_TREND" | "APPROVE_RANGE_SCALPING" | "DEFENSIVE_HALT",\n'
            '  "recommended_regime": "BULLISH_TREND" | "RANGING_SIDEWAYS" | "HIGH_VOLATILITY_PANIC",\n'
            '  "confidence": float (0.0 to 1.0),\n'
            '  "max_allocation_pct": float (0.0 to 0.20),\n'
            '  "summary_reasoning": string\n'
            "}"
        )
        user_content = {
            "symbol": symbol,
            "bull_thesis": bull_thesis,
            "bear_thesis": bear_thesis,
            "recent_lessons": recent_lessons or [],
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_content)},
        ]
        return self.chat_completion(messages, model=self.debate_model, max_tokens=350)

    def reflect_on_trade(
        self,
        trade_post_mortem: Dict[str, Any],
    ) -> Optional[str]:
        """Distills qualitative behavioral lesson from closed trade."""
        system_prompt = (
            "You are a Post-Trade Reflection Evaluator adhering to the FinMem pattern.\n"
            "Analyze the completed round-trip trade, evaluate whether entry/exit execution followed rules, "
            "and distill a concise 1-sentence behavioral takeaway to condition future trading decisions.\n"
            "Return valid JSON ONLY:\n"
            '{"lesson": "string"}'
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(trade_post_mortem)},
        ]
        res = self.chat_completion(messages, model=self.reflection_model, max_tokens=150)
        if res and isinstance(res, dict) and "lesson" in res:
            return str(res["lesson"])
        return None


# Backward-compatibility alias
BoxAPIGateway = LLMGateway
