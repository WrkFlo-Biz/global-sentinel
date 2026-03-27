#!/usr/bin/env python3
"""Adapter that turns SEC filing packets into scored research output."""
from __future__ import annotations

from typing import Any, Dict, List

from src.bridges.base_bridge import BaseBridge, utc_now_iso
from src.ingestion.sec_edgar_bridge import SECEdgarBridge
from src.ingestion.sec_filing_event_scorer import SECFilingEventScorer


class SECFilingAdapter(BaseBridge):
    source = "sec_filing_event_scorer"
    source_tier = "tier_3_research"
    trust_weight = 0.5
    freshness_ttl_minutes = 1440

    def __init__(self, repo_root=None, config=None):
        super().__init__(repo_root=repo_root, config=config)
        self._edgar = SECEdgarBridge()
        self._scorer = SECFilingEventScorer()

    def fetch(self) -> Dict[str, Any]:
        try:
            filings = self._edgar.fetch()
            if not isinstance(filings, list):
                filings = []

            scored: List[Dict[str, Any]] = []
            for filing in filings:
                if isinstance(filing, dict):
                    scored.append(self._scorer.score(filing))

            payload = {
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": utc_now_iso(),
                "fresh": len(scored) > 0,
                "data": {
                    "filing_count": len(filings),
                    "scored_count": len(scored),
                    "high_significance_count": sum(
                        1 for row in scored if float(row.get("filing_significance_score", 0.0)) >= 0.75
                    ),
                    "scores": scored,
                },
                "record_count": len(scored),
            }
            return self._mark_success(payload)
        except Exception as exc:  # pragma: no cover - integration path
            return self._mark_failure(exc)
