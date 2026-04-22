#!/usr/bin/env python3
"""NOAA Bridge -- National Weather Service severe weather alerts.

Emits PhysicalFlowEvent packets from the NWS alerts API.  Severe/extreme
weather can disrupt energy production, agriculture, and supply chains.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from src.packets.physical_flow_event import make_physical_flow_event

NWS_ALERTS = (
    "https://api.weather.gov/alerts/active"
    "?status=actual&severity=Extreme,Severe"
)

# Severity to disruption score mapping
SEVERITY_SCORES: Dict[str, float] = {
    "Extreme": 0.95,
    "Severe": 0.75,
    "Moderate": 0.50,
    "Minor": 0.25,
    "Unknown": 0.30,
}

# Keywords in event type that indicate energy/commodity disruption
ENERGY_EVENTS = {"hurricane", "tropical", "winter storm", "ice storm", "blizzard"}
AGRI_EVENTS = {"drought", "flood", "freeze", "frost", "heat", "excessive heat"}


def _classify_disruption(event_type: str, headline: str) -> float:
    """Boost disruption score if the event affects energy or agriculture."""
    lower = f"{event_type} {headline}".lower()
    boost = 0.0
    if any(kw in lower for kw in ENERGY_EVENTS):
        boost += 0.10
    if any(kw in lower for kw in AGRI_EVENTS):
        boost += 0.05
    return min(boost, 0.15)


class NOAABridge:
    source = "noaa"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def __init__(self) -> None:
        self.api_key = os.environ.get("NOAA_API_KEY", "")
        self._cache: Dict[str, Any] = {}

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch active severe/extreme weather alerts, return packet dicts."""
        raw = self._http_get(NWS_ALERTS, "severe_alerts")
        features = []
        if isinstance(raw, dict):
            features = raw.get("features", [])
        elif isinstance(raw, list):
            features = raw

        packets: List[Dict[str, Any]] = []
        for feature in features:
            props = feature.get("properties", {}) if isinstance(feature, dict) else {}
            if not props:
                continue

            severity = props.get("severity", "Unknown")
            event_type = props.get("event", "Weather Alert")
            headline = props.get("headline", "")
            area_desc = props.get("areaDesc", "US")
            alert_id = props.get("id", "")

            base_score = SEVERITY_SCORES.get(severity, 0.30)
            boost = _classify_disruption(event_type, headline)
            disruption = min(base_score + boost, 1.0)

            pkt = make_physical_flow_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                region="US",
                flow_type="weather_disruption",
                disruption_score=round(disruption, 3),
                measured_value=None,
                unit="severity_index",
                related_assets=["CL", "NG", "ZW", "ZC"],
                summary=f"{event_type}: {headline[:250]}",
                confidence=0.88,
                provenance={
                    "alert_id": alert_id,
                    "severity": severity,
                    "event": event_type,
                    "area": area_desc[:200],
                    "api": "nws_alerts",
                },
            )
            packets.append(pkt.to_dict())

        return packets

    # ------------------------------------------------------------------
    def _http_get(self, url: str, cache_key: str) -> Any:
        try:
            headers: Dict[str, str] = {
                "User-Agent": "GlobalSentinel/5.1",
                "Accept": "application/geo+json",
            }
            if self.api_key:
                headers["token"] = self.api_key
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._cache[cache_key] = data
                return data
        except Exception as e:
            logger.warning("NOAA fetch failed, using cache: %s", e)
            return self._cache.get(cache_key, {})
