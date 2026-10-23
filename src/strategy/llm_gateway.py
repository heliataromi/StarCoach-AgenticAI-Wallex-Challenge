"""BoxAPI LLM Gateway for StarCoach Wallex Algorithmic Trading Challenge.

Provides high-speed, cost-managed LLM reasoning using BoxAPI (https://ai.boxapi.ir/ai/v1):
- OpenAI-compatible REST endpoint (/chat/completions) via Python standard library (urllib.request).
- Multi-tier model routing:
    * Routine Macro Regime Evaluation: gpt-5.4-mini (ultra-low cost, fast)
    * High-Conviction Dialectical Debate Arbitration: gpt-6-astra (flagship model)
    * Post-Trade Episodic Memory Reflection: gpt-5.4-mini (distills trade lessons)
- Budget Guardian: Strictly enforces spend limits to preserve the $3.00 hackathon credit.
- Graceful Fallback: Seamlessly falls back to deterministic quantitative evaluation
  on network timeouts, API errors, or exhausted budget so live trading never halts.
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
import urllib.error
import urllib.request

logger = logging.getLogger("boxapi_gateway")


def load_dotenv(dotenv_path: Optional[Union[str, Path]] = None) -> Dict[str, str]:
    """Lightweight pure-Python .env file parser conforming to Ponytail minimalism.
    
    Reads key=value pairs, ignores comments and whitespace, and sets values into
    os.environ if they are not already defined.
    """
    env_file = Path(dotenv_path) if dotenv_path else Path(".env")
    loaded: Dict[str, str] = {}
    if not env_file.is_file():
        return loaded

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
                    # Strip wrapping single or double quotes
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
    """Tracks token usage and estimated spend against the hackathon credit budget."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


class BoxAPIGateway:
    """Unified OpenAI-compatible Gateway for BoxAPI LLM endpoints."""

    # Approximate pricing per 1M tokens (USD) for budget tracking
    MODEL_PRICING = {
        "gpt-5.4-mini": {"prompt": 0.15, "completion": 0.60},
        "gpt-5-mini": {"prompt": 0.15, "completion": 0.60},
        "gpt-6-astra": {"prompt": 2.50, "completion": 10.00},
        "gpt-6.1-sol": {"prompt": 2.50, "completion": 10.00},
        "gpt-5.6-terra": {"prompt": 1.00, "completion": 4.00},
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        macro_model: str = "gpt-5.4-mini",
        debate_model: str = "gpt-6-astra",
        reflection_model: str = "gpt-5.4-mini",
        budget_limit_usd: float = 2.50,  # Safety cap below $3.00
        timeout_seconds: float = 6.0,
        enable_network: bool = True,
    ) -> None:
        # Load .env if not already loaded
        load_dotenv()

        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = os.environ.get("BOXAPI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("BOXAPI_BASE_URL") or "https://ai.boxapi.ir/ai/v1").rstrip("/")
        self.macro_model = macro_model
        self.debate_model = debate_model
        self.reflection_model = reflection_model
        self.budget_limit_usd = budget_limit_usd
        self.timeout_seconds = timeout_seconds
        self.enable_network = enable_network

        self.stats = GatewayStats()

    @property
    def is_configured(self) -> bool:
        """True if an API key is present and network calls are permitted."""
        return bool(self.api_key) and self.enable_network

    @property
    def budget_exhausted(self) -> bool:
        """True if estimated cost has exceeded the safety budget cap."""
        return self.stats.estimated_cost_usd >= self.budget_limit_usd

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 500,
        response_format_json: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Dispatches an OpenAI-compatible /chat/completions request via urllib.
        
        Returns parsed JSON dict, or None on failure/budget exhaustion (triggering fallback).
        """
        if not self.is_configured:
            return None

        if self.budget_exhausted:
            logger.warning(
                f"[BoxAPI Budget Guard] Spend limit (${self.budget_limit_usd:.2f}) reached. "
                f"Falling back to deterministic quantitative engine."
            )
            return None

        target_model = model or self.macro_model
        endpoint = f"{self.base_url}/chat/completions"

        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "StarCoach-TradingEngine/1.0",
        }

        req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

        self.stats.total_calls += 1
        t0 = time.perf_counter()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status_code = resp.getcode()
                raw_body = resp.read().decode("utf-8")

            if status_code != 200:
                self.stats.failed_calls += 1
                logger.warning(f"[BoxAPI Error] HTTP {status_code}: {raw_body[:200]}")
                return None

            data = json.loads(raw_body)
            self.stats.successful_calls += 1

            # Track tokens & budget
            usage = data.get("usage", {})
            p_tokens = usage.get("prompt_tokens", 0)
            c_tokens = usage.get("completion_tokens", 0)
            self.stats.total_prompt_tokens += p_tokens
            self.stats.total_completion_tokens += c_tokens

            # Compute estimated cost
            rates = self.MODEL_PRICING.get(target_model, {"prompt": 0.50, "completion": 2.00})
            call_cost = (p_tokens / 1_000_000.0 * rates["prompt"]) + (c_tokens / 1_000_000.0 * rates["completion"])
            self.stats.estimated_cost_usd += call_cost

            choices = data.get("choices", [])
            if not choices:
                return None

            content = choices[0].get("message", {}).get("content", "")
            if response_format_json:
                try:
                    return json.loads(content)
                except Exception:
                    # Strip any markdown fences if present
                    clean = content.strip()
                    if clean.startswith("```"):
                        lines = clean.splitlines()
                        clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
                    return json.loads(clean)
            return {"content": content}

        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            self.stats.failed_calls += 1
            logger.warning(f"[BoxAPI Gateway Fallback] Call failed: {e}. Defaulting to quantitative engine.")
            return None
        except Exception as e:
            self.stats.failed_calls += 1
            logger.warning(f"[BoxAPI Gateway Unexpected Error] {e}. Defaulting to quantitative engine.")
            return None

    def evaluate_macro_regime(
        self,
        symbol: str,
        indicators: Dict[str, Any],
        recent_lessons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluates macro market regime using cost-efficient gpt-5.4-mini."""
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
        """Adjudicates dialectical Bull vs Bear debate using flagship gpt-6-astra."""
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
        """Distills qualitative behavioral lesson from closed trade using gpt-5.4-mini."""
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
