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
        normalized = self._attach_canonical_ledger(payload)
        self._last_fetch_result = normalized
        self._consecutive_failures = 0
        return normalized

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

    def _attach_canonical_ledger(self, payload: Any) -> Any:
        """Attach canonical event metadata to any event-like payloads.

        The bridge fleet emits a mix of direct packets, lists of packets,
        and wrapper dicts that carry packet lists under keys like ``data``
        or ``packets``. This helper walks those common shapes and uses the
        shared research ledger normalizer when a value looks event-like.
        """
        try:
            from src.research.event_ledger import attach_event_ledger
        except Exception:
            return payload

        def looks_like_event_packet(value: Any) -> bool:
            if not isinstance(value, dict):
                return False
            event_keys = (
                "event_type",
                "event_id",
                "packet_id",
                "headline",
                "title",
                "summary",
                "published_time_utc",
                "published_date",
                "source_url",
                "source_domain",
                "category",
                "tags",
            )
            return any(value.get(key) not in (None, "", [], {}) for key in event_keys)

        def recurse(value: Any) -> Any:
            if isinstance(value, list):
                return [recurse(item) for item in value]
            if not isinstance(value, dict):
                return value

            updated = dict(value)
            for key in ("data", "packets", "events", "items", "results"):
                nested = updated.get(key)
                if isinstance(nested, list):
                    updated[key] = [recurse(item) for item in nested]
                elif isinstance(nested, dict):
                    updated[key] = recurse(nested)

            if looks_like_event_packet(updated):
                return attach_event_ledger(updated, bridge_name=self.source)
            return updated

        return recurse(payload)
