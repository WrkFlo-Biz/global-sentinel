#!/usr/bin/env python3
"""CDS sovereign spread bridge.

Fetches 5-year sovereign CDS or spread proxies and normalizes them into
``MacroPolicyEvent`` packets with lineage metadata.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.structured_logger import get_logger
from src.core.telemetry import record_metric, start_span
from src.packets.macro_policy_event import make_macro_policy_event


logger = get_logger("cds_sovereign_bridge")

# FRED series IDs for available CDS data (or proxy spreads)
SOVEREIGN_SERIES = {
    "US": {"fred_id": "BAMLH0A0HYM2", "name": "US High Yield Spread", "baseline_bps": 350},
    "DE": {"fred_id": None, "name": "Germany", "baseline_bps": 15},
    "JP": {"fred_id": None, "name": "Japan", "baseline_bps": 25},
    "CN": {"fred_id": None, "name": "China", "baseline_bps": 60},
    "TR": {"fred_id": None, "name": "Turkey", "baseline_bps": 350},
    "BR": {"fred_id": None, "name": "Brazil", "baseline_bps": 150},
    "ZA": {"fred_id": None, "name": "South Africa", "baseline_bps": 200},
    "MX": {"fred_id": None, "name": "Mexico", "baseline_bps": 100},
}


class CDSSovereignBridge:
    """Fetches sovereign CDS/credit spread data."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path(".")
        self._fred_key = os.environ.get("FRED_API_KEY", "")

    def poll(self) -> List[Dict[str, Any]]:
        with start_span("bridge.fetch.cds_sovereign", bridge_name="cds_sovereign_bridge"):
            packets: List[Dict[str, Any]] = []
            now = datetime.now(timezone.utc).isoformat()

            for country, info in SOVEREIGN_SERIES.items():
                spread_bps = info["baseline_bps"]

                # Try FRED if series available
                if info["fred_id"] and self._fred_key:
                    try:
                        spread_bps = self._fetch_fred(info["fred_id"]) or spread_bps
                    except Exception as e:
                        logger.warning("fred_fetch_failed", country=country, error=str(e))

                # Compute stress signal
                stress_score = self._compute_stress(spread_bps, info["baseline_bps"])
                confidence = 0.85 if info["fred_id"] and self._fred_key else 0.6
                source_api = "fred" if info["fred_id"] and self._fred_key else "baseline"
                summary = f"{info['name']} sovereign spread at {spread_bps:.1f} bps versus {info['baseline_bps']:.1f} bps baseline"
                packet = make_macro_policy_event(
                    source="cds_sovereign",
                    source_tier="tier_2_operational",
                    trust_weight=0.8,
                    topic=f"{country}_sovereign_cds",
                    policy_domain="sovereign_risk",
                    hawkish_dovish_score=round(stress_score * -1, 4),
                    growth_inflation_score=round(stress_score * -0.5, 4),
                    market_relevance_score=stress_score,
                    related_assets=[country, "sovereign_credit", "rates"],
                    summary=summary,
                    confidence=confidence,
                    provenance={
                        "bridge": "cds_sovereign_bridge",
                        "country": country,
                        "source_api": source_api,
                        "observed_at": now,
                    },
                ).to_dict()
                packet.update(
                    {
                        "country": country,
                        "country_name": info["name"],
                        "spread_bps": spread_bps,
                        "baseline_bps": info["baseline_bps"],
                        "stress_score": stress_score,
                        "_lineage": {
                            "schema_version": "1.0.0",
                            "parent_artifact_ids": [],
                            "source_packet_hashes": [],
                            "feature_group_versions": {},
                            "code_commit_sha": os.environ.get("GIT_COMMIT_SHA", "unknown"),
                            "environment": os.environ.get("GLOBAL_SENTINEL_ENV", "unknown"),
                            "time_window": {"start": now, "end": now},
                            "incident_mode": "normal",
                            "regime_state": "unknown",
                            "manifest_hash": packet["packet_id"],
                            "bridge": "cds_sovereign_bridge",
                            "country": country,
                        },
                    }
                )
                packets.append(packet)

            record_metric("bridge_fetch_success_total", 1, bridge_name="cds_sovereign_bridge")
            record_metric("bridge_packet_throughput_total", len(packets), bridge_name="cds_sovereign_bridge")
            logger.info("bridge_poll_success", bridge_name="cds_sovereign_bridge", packet_count=len(packets))
            return packets

    def _fetch_fred(self, series_id: str) -> Optional[float]:
        import requests
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": self._fred_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            obs = resp.json().get("observations", [])
            if obs and obs[0].get("value", ".") != ".":
                return float(obs[0]["value"]) * 100  # Convert to bps
        return None

    @staticmethod
    def _compute_stress(current_bps: float, baseline_bps: float) -> float:
        if baseline_bps <= 0:
            return 0.0
        ratio = current_bps / baseline_bps
        # > 1.5x baseline = stress, > 2x = high stress
        return round(min(1.0, max(0.0, (ratio - 1.0) / 1.0)), 4)
