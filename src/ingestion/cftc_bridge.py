#!/usr/bin/env python3
"""CFTC Bridge -- Commitments of Traders reports.

Emits PhysicalFlowEvent packets from the CFTC Socrata API for
Commitments of Traders (COT) data.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional

from src.packets.physical_flow_event import make_physical_flow_event

# CFTC Socrata API for COT Futures-Only reports
CFTC_COT_API = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"

# Map CFTC market names to tradable asset symbols
MARKET_ASSET_MAP: Dict[str, str] = {
    "CRUDE OIL, LIGHT SWEET": "CL",
    "GOLD": "GC",
    "SILVER": "SI",
    "COPPER": "HG",
    "WHEAT-SRW": "ZW",
    "CORN": "ZC",
    "NATURAL GAS": "NG",
    "E-MINI S&P 500": "ES",
    "U.S. TREASURY BONDS": "ZB",
    "EURO FX": "6E",
}

# Markets we actively track for physical-flow relevance
DEFAULT_MARKETS = [
    "CRUDE OIL, LIGHT SWEET",
    "GOLD",
    "SILVER",
    "COPPER",
    "WHEAT-SRW",
]


def _compute_net_positioning(record: Dict[str, Any]) -> Optional[float]:
    """Return commercial net = long - short (in contracts)."""
    try:
        comm_long = float(record.get("comm_positions_long_all", 0))
        comm_short = float(record.get("comm_positions_short_all", 0))
        return comm_long - comm_short
    except (TypeError, ValueError):
        return None


def _disruption_score_from_net(net: Optional[float]) -> float:
    """Heuristic: large absolute net positions signal crowding risk.

    Score 0.0-1.0.  We use a simple sigmoid-like mapping.
    """
    if net is None:
        return 0.3
    abs_net = abs(net)
    if abs_net > 200_000:
        return 0.9
    if abs_net > 100_000:
        return 0.7
    if abs_net > 50_000:
        return 0.5
    return 0.3


class CFTCBridge:
    source = "cftc"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch latest COT data for key commodities, return packet dicts."""
        packets: List[Dict[str, Any]] = []

        for market in DEFAULT_MARKETS:
            record = self._get_latest_cot(market)
            if not record:
                continue

            net = _compute_net_positioning(record)
            disruption = _disruption_score_from_net(net)
            asset = MARKET_ASSET_MAP.get(market, market[:2].upper())
            report_date = record.get("report_date_as_yyyy_mm_dd", "")

            pkt = make_physical_flow_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                region="US",
                flow_type="futures_positioning",
                disruption_score=disruption,
                measured_value=net,
                unit="contracts_net",
                related_assets=["CL", "GC", "SI", "HG", "ZW"],
                summary=(
                    f"COT {market}: commercial net = {net:+,.0f} contracts"
                    if net is not None
                    else f"COT {market}: data unavailable"
                ),
                confidence=0.92,
                provenance={
                    "market": market,
                    "report_date": report_date,
                    "asset_symbol": asset,
                    "api": "cftc_socrata",
                },
            )
            packets.append(pkt.to_dict())

        return packets

    # ------------------------------------------------------------------
    def _get_latest_cot(self, market: str) -> Optional[Dict[str, Any]]:
        encoded = urllib.request.quote(market)
        url = (
            f"{CFTC_COT_API}"
            f"?$limit=1"
            f"&$order=report_date_as_yyyy_mm_dd DESC"
            f"&market_and_exchange_names={encoded}"
        )
        data = self._http_get(url, f"cot:{market}")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def _http_get(self, url: str, cache_key: str) -> Any:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "GlobalSentinel/5.1"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._cache[cache_key] = data
                return data
        except Exception:
            return self._cache.get(cache_key, [])
