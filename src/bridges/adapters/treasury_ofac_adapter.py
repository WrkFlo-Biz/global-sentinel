#!/usr/bin/env python3
"""Adapter for Treasury OFAC sanctions bridge."""
from __future__ import annotations

from typing import Any, Dict

import logging

from src.bridges.base_bridge import BaseBridge, utc_now_iso

logger = logging.getLogger(__name__)


class TreasuryOfacAdapter(BaseBridge):
    source = "treasury_ofac"
    source_tier = "tier_1_official"
    trust_weight = 0.9
    freshness_ttl_minutes = 120

    def __init__(self, repo_root=None, config=None):
        super().__init__(repo_root=repo_root, config=config)
        self._inner = None

    def _get_inner(self):
        if self._inner is None:
            from src.bridges.treasury_ofac_bridge import TreasuryOFACBridge
            self._inner = TreasuryOFACBridge(repo_root=self.repo_root)
        return self._inner

    def fetch(self) -> Dict[str, Any]:
        try:
            inner = self._get_inner()
            result = inner.fetch() if hasattr(inner, "fetch") else inner.poll()
            payload = result if isinstance(result, dict) else {"data": result}
            payload.setdefault("source", self.source)
            payload.setdefault("source_tier", self.source_tier)
            payload.setdefault("trust_weight", self.trust_weight)
            payload.setdefault("timestamp_utc", utc_now_iso())
            payload.setdefault("fresh", True)
            return self._mark_success(payload)
        except Exception as e:
            logger.warning("TreasuryOfacAdapter fetch failed: %s", e)
            return self._mark_failure(str(e))
