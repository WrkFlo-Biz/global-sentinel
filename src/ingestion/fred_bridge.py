from __future__ import annotations

import os
import urllib.request
import json
from typing import Any, Dict, List, Optional

from src.packets.macro_policy_event import make_macro_policy_event


FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE = "https://api.stlouisfed.org/fred"
DEFAULT_SERIES = ["DGS10", "CPIAUCSL", "UNRATE", "PAYEMS"]


class FredBridge:
    source = "fred"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def fetch(self) -> List[Dict[str, Any]]:
        return self.fetch_series()

    def fetch_series(self, series_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for sid in (series_ids or DEFAULT_SERIES):
            obs = self._get_latest_observation(sid)
            if not obs:
                continue

            value = obs.get("value")
            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"FRED series update: {sid}",
                policy_domain="macro_data",
                hawkish_dovish_score=0.0,
                growth_inflation_score=0.0,
                market_relevance_score=0.8,
                related_assets=self._map_assets(sid),
                summary=f"Latest {sid} = {value}",
                confidence=0.98,
                provenance={"series_id": sid, "observation_date": obs.get("date")},
            )
            out.append(pkt.to_dict())
        return out

    def _get_latest_observation(self, series_id: str) -> Optional[Dict[str, Any]]:
        if not FRED_API_KEY:
            return None
        url = (
            f"{FRED_BASE}/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}"
            f"&file_type=json&sort_order=desc&limit=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/5.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        observations = data.get("observations", [])
        return observations[0] if observations else None

    def _map_assets(self, sid: str) -> List[str]:
        mapping = {
            "DGS10": ["UST10Y", "TLT", "XLF"],
            "CPIAUCSL": ["UST10Y", "GLD", "XLP"],
            "UNRATE": ["SPX", "IWM", "XLY"],
            "PAYEMS": ["SPX", "IWM", "XLI"],
        }
        return mapping.get(sid, ["SPX"])
