"""Policy Uncertainty Bridge -- Tier 3 research-only policy uncertainty index.

Loads policy uncertainty observations from a staged JSON file and emits
MacroPolicyEvent packets. Never drives execution directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.packets.macro_policy_event import make_macro_policy_event


class PolicyUncertaintyBridge:
    source = "policy_uncertainty"
    source_tier = "tier_3_research"
    trust_weight = 0.5

    def fetch(self) -> List[Dict[str, Any]]:
        """Return empty list — requires staged JSON data file."""
        return []

    def fetch_from_json(self, path: str) -> List[Dict[str, Any]]:
        """Load policy uncertainty observations from a local/staged JSON file.

        Expected format:
        {"series": [{"region": "China", "value": 145.2, "date": "2026-03-01"}, ...]}
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        out: List[Dict[str, Any]] = []

        for row in raw.get("series", []):
            region = row.get("region", "Global")
            value = float(row.get("value", 0.0))
            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"Policy uncertainty update: {region}",
                policy_domain="policy_uncertainty",
                hawkish_dovish_score=0.0,
                growth_inflation_score=0.0,
                market_relevance_score=min(value / 300.0, 1.0),
                related_assets=self._related_assets(region),
                summary=f"Policy uncertainty for {region} = {value}",
                confidence=0.70,
                provenance={"region": region, "date": row.get("date"), "value": value},
            )
            out.append(pkt.to_dict())
        return out

    def _related_assets(self, region: str) -> List[str]:
        r = region.lower()
        if "china" in r:
            return ["EEM", "FXI", "BABA", "PDD", "SOXX"]
        if "hong kong" in r:
            return ["EWH", "FXI"]
        return ["SPY", "EEM", "DXY"]
