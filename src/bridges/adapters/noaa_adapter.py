#!/usr/bin/env python3
"""Adapter for NOAA weather/climate disruption bridge."""
from __future__ import annotations

from typing import Any, Dict

import logging

from src.bridges.base_bridge import BaseBridge, utc_now_iso

logger = logging.getLogger(__name__)


class NOAAAdapter(BaseBridge):
    source = "noaa"
    source_tier = "tier_2"
    trust_weight = 0.6
    freshness_ttl_minutes = 60

    def __init__(self, repo_root=None, config=None):
        super().__init__(repo_root=repo_root, config=config)
        self._inner = None

    def _get_inner(self):
        if self._inner is None:
            from src.ingestion.noaa_bridge import NOAABridge
            self._inner = NOAABridge()
        return self._inner

    def fetch(self) -> Dict[str, Any]:
        try:
            inner = self._get_inner()
            result = inner.fetch() if hasattr(inner, "fetch") else inner.poll()
            payload = result if isinstance(result, dict) else {"data": result}
            if isinstance(payload.get("data"), list):
                payload.setdefault("record_count", len(payload["data"]))
            payload.setdefault("source", self.source)
            payload.setdefault("source_tier", self.source_tier)
            payload.setdefault("trust_weight", self.trust_weight)
            payload.setdefault("timestamp_utc", utc_now_iso())
            payload.setdefault("fresh", True)
            return self._mark_success(payload)
        except Exception as e:
            logger.warning("NOAAAdapter fetch failed: %s", e)
            return self._mark_failure(str(e))
