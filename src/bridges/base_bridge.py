#!/usr/bin/env python3
"""Canonical bridge contract for Global Sentinel."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseBridge(ABC):
    """Standard bridge contract shared across the normalized registry."""

    source: str = ""
    source_tier: str = ""
    trust_weight: float = 0.0
    freshness_ttl_minutes: int = 60

    def __init__(self, repo_root: Optional[Path] = None, config: Optional[dict] = None):
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.config = config or {}
        self._last_fetch_time: Optional[datetime] = None
        self._last_fetch_result: Optional[Dict[str, Any]] = None
        self._consecutive_failures = 0

    @abstractmethod
    def fetch(self) -> Dict[str, Any]:
        """Fetch normalized bridge output."""

    def is_fresh(self) -> bool:
        if not self._last_fetch_time:
            return False
        elapsed_min = (datetime.now(timezone.utc) - self._last_fetch_time).total_seconds() / 60.0
        return elapsed_min < float(self.freshness_ttl_minutes)

    def health(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "fresh": self.is_fresh(),
            "last_fetch": self._last_fetch_time.isoformat() if self._last_fetch_time else None,
            "consecutive_failures": self._consecutive_failures,
        }

    def _mark_success(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._last_fetch_time = datetime.now(timezone.utc)
        self._last_fetch_result = payload
        self._consecutive_failures = 0
        return payload

    def _mark_failure(self, error: Any) -> Dict[str, Any]:
        self._consecutive_failures += 1
        payload = {
            "source": self.source,
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "timestamp_utc": utc_now_iso(),
            "fresh": False,
            "error": str(error),
            "data": None,
        }
        self._last_fetch_result = payload
        return payload
