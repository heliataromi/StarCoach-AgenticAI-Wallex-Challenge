"""Data contracts and schemas for the Structured Decision Auditor.

Adheres to Ponytail minimalism: immutable frozen dataclasses and cryptographically
tamper-evident SHA-256 digest calculation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import time
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable audit record for 100% of trading decisions and evaluations."""
    record_id: str
    timestamp_iso: str
    timestamp_epoch: float
    symbol: str
    market_regime: str
    action_proposed: str
    execution_outcome: str
    price: float
    quantity: float
    cost_tmn: float
    fee_tmn: float
    latency_ms: float
    jev_metrics: Dict[str, Any]
    risk_metrics: Dict[str, Any]
    market_indicators: Dict[str, Any]
    agentic_reasoning: Dict[str, Any]
    hash_sha256: str = ""

    def calculate_digest(self) -> str:
        """Computes deterministic SHA-256 hash of all record fields excluding hash_sha256."""
        data = asdict(self)
        data.pop("hash_sha256", None)
        canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def with_hash(self) -> DecisionRecord:
        """Returns a copy of this record with its cryptographic SHA-256 digest populated."""
        digest = self.calculate_digest()
        data = asdict(self)
        data["hash_sha256"] = digest
        return DecisionRecord(**data)

    def verify_integrity(self) -> bool:
        """Verifies if the record content matches its SHA-256 digest."""
        if not self.hash_sha256:
            return False
        return self.calculate_digest() == self.hash_sha256
