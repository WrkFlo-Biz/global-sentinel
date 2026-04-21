#!/usr/bin/env python3
"""Semiconductor supply chain bridge.

Tracks global chip manufacturing indicators and emits normalized
``GeopoliticalEvent`` packets with research lineage metadata.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.structured_logger import get_logger
from src.core.telemetry import record_metric, start_span
from src.packets.geopolitical_event import make_geopolitical_event


logger = get_logger("semiconductor_supply_bridge")

# FRED series for semiconductor indicators
SEMI_SERIES = {
    "NAICS3344": {"name": "Semiconductor Manufacturing Index", "baseline": 100.0},
    "AWHNONAG": {"name": "Avg Weekly Hours Manufacturing", "baseline": 40.5},
}

# Critical materials tracked
CRITICAL_MATERIALS = ["gallium", "germanium", "neon", "palladium", "cobalt"]


class SemiconductorSupplyBridge:
    """Tracks semiconductor supply chain indicators."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path(".")
        self._fred_key = os.environ.get("FRED_API_KEY", "")

    def poll(self) -> List[Dict[str, Any]]:
        with start_span("bridge.fetch.semiconductor_supply", bridge_name="semiconductor_supply_bridge"):
            packets = []
            now = datetime.now(timezone.utc).isoformat()

            # FRED data
            for series_id, info in SEMI_SERIES.items():
                value = info["baseline"]
                source_api = "baseline"
                confidence = 0.5

                if self._fred_key:
                    try:
                        fetched = self._fetch_fred(series_id)
                        if fetched is not None:
                            value = fetched
                            source_api = "fred"
                            confidence = 0.80
                    except Exception as e:
                        logger.warning("fred_semiconductor_fetch_failed", series_id=series_id, error=str(e))

                supply_stress = self._compute_supply_stress(value, info["baseline"])
                packet = make_geopolitical_event(
                    source="semiconductor_supply",
                    source_tier="tier_3_research",
                    trust_weight=0.5,
                    region="global",
                    severity=round(min(1.0, supply_stress * 0.8), 4),
                    event_category="supply_chain_indicator",
                    energy_relevance=round(min(1.0, supply_stress * 0.2), 4),
                    supply_chain_relevance=supply_stress,
                    asset_channels=["semiconductors", "equities", "fx"],
                    summary=f"{info['name']} at {value:.2f} versus {info['baseline']:.2f} baseline",
                    confidence=confidence,
                    provenance={
                        "bridge": "semiconductor_supply_bridge",
                        "series_id": series_id,
                        "source_api": source_api,
                        "observed_at": now,
                    },
                ).to_dict()
                packet.update(
                    {
                        "indicator_name": info["name"],
                        "value": value,
                        "baseline": info["baseline"],
                        "supply_chain_stress": supply_stress,
                        "event_type": "supply_chain_indicator",
                        "sector": "semiconductors",
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
                            "bridge": "semiconductor_supply_bridge",
                            "series_id": series_id,
                        },
                    }
                )
                packets.append(packet)

            # Aggregate semiconductor packet
            if packets:
                avg_stress = sum(p["supply_chain_stress"] for p in packets) / len(packets)
                aggregate = make_geopolitical_event(
                    source="semiconductor_supply",
                    source_tier="tier_3_research",
                    trust_weight=0.5,
                    region="global",
                    severity=round(min(1.0, avg_stress * 0.8), 4),
                    event_category="supply_chain_aggregate",
                    energy_relevance=round(min(1.0, avg_stress * 0.2), 4),
                    supply_chain_relevance=round(avg_stress, 4),
                    asset_channels=["semiconductors", "equities", "fx"],
                    summary="Aggregate semiconductor supply chain stress packet",
                    confidence=max(p["confidence"] for p in packets),
                    provenance={"bridge": "semiconductor_supply_bridge", "type": "aggregate", "observed_at": now},
                ).to_dict()
                aggregate.update(
                    {
                        "event_type": "supply_chain_aggregate",
                        "sector": "semiconductors",
                        "aggregate_stress": round(avg_stress, 4),
                        "critical_materials_tracked": CRITICAL_MATERIALS,
                        "_lineage": {
                            "schema_version": "1.0.0",
                            "parent_artifact_ids": [],
                            "source_packet_hashes": [p["packet_id"] for p in packets],
                            "feature_group_versions": {},
                            "code_commit_sha": os.environ.get("GIT_COMMIT_SHA", "unknown"),
                            "environment": os.environ.get("GLOBAL_SENTINEL_ENV", "unknown"),
                            "time_window": {"start": now, "end": now},
                            "incident_mode": "normal",
                            "regime_state": "unknown",
                            "manifest_hash": aggregate["packet_id"],
                            "bridge": "semiconductor_supply_bridge",
                        },
                    }
                )
                packets.append(aggregate)

            record_metric("bridge_fetch_success_total", 1, bridge_name="semiconductor_supply_bridge")
            record_metric("bridge_packet_throughput_total", len(packets), bridge_name="semiconductor_supply_bridge")
            logger.info("bridge_poll_success", bridge_name="semiconductor_supply_bridge", packet_count=len(packets))
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
                return float(obs[0]["value"])
        return None

    @staticmethod
    def _compute_supply_stress(current: float, baseline: float) -> float:
        if baseline <= 0:
            return 0.0
        deviation = abs(current - baseline) / baseline
        return round(min(1.0, deviation), 4)
