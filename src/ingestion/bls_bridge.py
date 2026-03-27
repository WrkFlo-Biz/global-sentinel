from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional

from src.packets.macro_policy_event import make_macro_policy_event

BLS_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

LABOR_SERIES = {
    "unemployment_rate": "LNS14000000",
    "nonfarm_payrolls": "CES0000000001",
    "avg_hourly_earnings": "CES0500000003",
    "cpi_all_urban": "CUUR0000SA0",
    "ppi_finished_goods": "WPSFD4",
}


class BLSBridge:
    source = "bls"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("BLS_API_KEY", "")

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch key labor indicators and return as MacroPolicyEvent packets."""
        raw = self._get_series(list(LABOR_SERIES.values()))
        out: List[Dict[str, Any]] = []
        for label, sid in LABOR_SERIES.items():
            series_data = raw.get(sid, [])
            if not series_data:
                continue
            latest = series_data[0]
            value = latest.get("value", "")
            period = latest.get("period", "")
            year = latest.get("year", "")

            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"BLS {label}: {value}",
                policy_domain="labor_data",
                hawkish_dovish_score=0.0,
                growth_inflation_score=self._growth_inflation_score(label),
                market_relevance_score=0.85,
                related_assets=self._map_assets(label),
                summary=f"{label} = {value} ({year} {period})",
                confidence=0.95,
                provenance={"series_id": sid, "year": year, "period": period},
            )
            out.append(pkt.to_dict())
        return out

    def _get_series(self, series_ids: List[str], start_year: int = 2025, end_year: int = 2026) -> Dict[str, List[Dict]]:
        payload = json.dumps({
            "seriesid": series_ids,
            "startyear": str(start_year),
            "endyear": str(end_year),
            "registrationkey": self.api_key,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(BLS_BASE, data=payload, headers={
                "Content-Type": "application/json",
                "User-Agent": "GlobalSentinel/5.1",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = {}
                for series in data.get("Results", {}).get("series", []):
                    sid = series.get("seriesID", "")
                    results[sid] = series.get("data", [])
                return results
        except Exception:
            return {}

    def _growth_inflation_score(self, label: str) -> float:
        if label in ("cpi_all_urban", "ppi_finished_goods", "avg_hourly_earnings"):
            return 1.0  # inflation indicator
        if label in ("unemployment_rate", "nonfarm_payrolls"):
            return -1.0  # growth/labor indicator
        return 0.0

    def _map_assets(self, label: str) -> List[str]:
        mapping = {
            "unemployment_rate": ["SPX", "IWM", "XLY"],
            "nonfarm_payrolls": ["SPX", "IWM", "XLI"],
            "avg_hourly_earnings": ["UST10Y", "GLD", "XLF"],
            "cpi_all_urban": ["UST10Y", "GLD", "TIP"],
            "ppi_finished_goods": ["UST10Y", "XLI", "XLB"],
        }
        return mapping.get(label, ["SPX"])
