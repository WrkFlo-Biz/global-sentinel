#!/usr/bin/env python3
"""Geopolitical Risk (GPR) Index Bridge.

Fetches the Matteo Iacoviello GPR data feed and emits normalized
``GeopoliticalEvent`` packets with lineage metadata.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.structured_logger import get_logger
from src.core.telemetry import record_metric, start_span
from src.packets.geopolitical_event import make_geopolitical_event


logger = get_logger("gpr_index_bridge")

GPR_DATA_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.csv"


class GPRIndexBridge:
    """Fetches Geopolitical Risk Index data."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path(".")
        self._cache_path = Path(self.repo_root) / "artifacts" / "cache" / "gpr_latest.csv"

    def poll(self) -> List[Dict[str, Any]]:
        """Fetch latest GPR data. Falls back to cached or baseline."""
        with start_span("bridge.fetch.gpr_index", bridge_name="gpr_index_bridge"):
            try:
                packets = self._poll_live()
                record_metric("bridge_fetch_success_total", 1, bridge_name="gpr_index_bridge", mode="live")
            except Exception as e:
                record_metric("bridge_fetch_failure_total", 1, bridge_name="gpr_index_bridge", mode="live")
                logger.warning("gpr_live_poll_failed", error=str(e))
                packets = self._poll_cached()
                record_metric("bridge_fetch_success_total", 1, bridge_name="gpr_index_bridge", mode="fallback")
            record_metric("bridge_packet_throughput_total", len(packets), bridge_name="gpr_index_bridge")
            logger.info("bridge_poll_success", bridge_name="gpr_index_bridge", packet_count=len(packets))
            return packets

    def _poll_live(self) -> List[Dict[str, Any]]:
        import requests
        resp = requests.get(GPR_DATA_URL, timeout=15)
        resp.raise_for_status()
        text = resp.text

        # Cache for offline use
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(text, encoding="utf-8")

        return self._parse_csv(text)

    def _poll_cached(self) -> List[Dict[str, Any]]:
        if self._cache_path.exists():
            text = self._cache_path.read_text(encoding="utf-8")
            return self._parse_csv(text)
        return self._baseline_packet()

    def _parse_csv(self, text: str) -> List[Dict[str, Any]]:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return self._baseline_packet()

        # Get latest row
        latest = rows[-1]
        now = datetime.now(timezone.utc).isoformat()

        gpr_value = 0.0
        gpr_threats = 0.0
        gpr_acts = 0.0

        for key, val in latest.items():
            key_lower = key.strip().lower()
            if key_lower in ("gpr", "gprd", "gpr_daily"):
                try:
                    gpr_value = float(val)
                except (ValueError, TypeError):
                    pass
            elif "threat" in key_lower:
                try:
                    gpr_threats = float(val)
                except (ValueError, TypeError):
                    pass
            elif "act" in key_lower:
                try:
                    gpr_acts = float(val)
                except (ValueError, TypeError):
                    pass

        # GPR severity mapping: historical mean ~100, spikes to 300+ in crises
        severity = self._map_severity(gpr_value)

        row_date = latest.get("date", latest.get("Date", "unknown"))
        packet = make_geopolitical_event(
            source="gpr_index",
            source_tier="tier_3_research",
            trust_weight=0.5,
            region="global",
            severity=severity,
            event_category="geopolitical_risk_index",
            energy_relevance=round(min(1.0, severity * 0.5), 4),
            supply_chain_relevance=round(min(1.0, severity * 0.35), 4),
            asset_channels=["equities", "rates", "energy", "fx"],
            summary=f"GPR daily index at {gpr_value:.2f} for {row_date}",
            confidence=0.80,
            provenance={
                "bridge": "gpr_index_bridge",
                "data_url": GPR_DATA_URL,
                "row_date": row_date,
            },
        ).to_dict()
        packet.update(
            {
                "gpr_value": gpr_value,
                "gpr_threats": gpr_threats,
                "gpr_acts": gpr_acts,
                "event_type": "geopolitical_risk_index",
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
                    "bridge": "gpr_index_bridge",
                },
            }
        )
        return [packet]

    def _baseline_packet(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        packet = make_geopolitical_event(
            source="gpr_index",
            source_tier="tier_3_research",
            trust_weight=0.5,
            region="global",
            severity=0.3,
            event_category="geopolitical_risk_index",
            energy_relevance=0.15,
            supply_chain_relevance=0.1,
            asset_channels=["equities", "rates", "energy", "fx"],
            summary="Baseline GPR fallback packet",
            confidence=0.4,
            provenance={"bridge": "gpr_index_bridge", "data_url": "baseline"},
        ).to_dict()
        packet.update(
            {
                "gpr_value": 100.0,
                "event_type": "geopolitical_risk_index",
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
                    "bridge": "gpr_index_bridge",
                },
            }
        )
        return [packet]

    @staticmethod
    def _map_severity(gpr_value: float) -> float:
        # Historical: mean ~100, std ~50. 200+ = elevated, 300+ = crisis
        if gpr_value <= 0:
            return 0.0
        normalized = (gpr_value - 100) / 100.0
        return round(min(1.0, max(0.0, (normalized + 0.5) / 1.5)), 4)
