#!/usr/bin/env python3
"""Maritime Bridge -- Vessel tracking and port congestion (stub).

Requires a commercial AIS API subscription (e.g. MarineTraffic, VesselFinder).
When data is available, emits PhysicalFlowEvent packets with
flow_type="vessel_tracking".
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.packets.physical_flow_event import make_physical_flow_event


class MaritimeBridge:
    """Stub bridge for maritime/vessel tracking data.

    Returns an empty list until a commercial API is configured.
    When implemented, each packet would represent a vessel-tracking
    or port-congestion observation as a PhysicalFlowEvent.
    """

    source = "maritime"
    source_tier = "tier_2_operational"
    trust_weight = 0.8

    def fetch(self) -> List[Dict[str, Any]]:
        """Return empty list -- no commercial API configured yet."""
        return []

    # ------------------------------------------------------------------
    # Example of what a real implementation would emit:
    #
    # pkt = make_physical_flow_event(
    #     source=self.source,
    #     source_tier=self.source_tier,
    #     trust_weight=self.trust_weight,
    #     region="Strait of Hormuz",
    #     flow_type="vessel_tracking",
    #     disruption_score=0.6,
    #     measured_value=42.0,
    #     unit="vessels_in_transit",
    #     related_assets=["CL", "NG", "BDRY"],
    #     summary="Vessel congestion elevated in Strait of Hormuz.",
    #     confidence=0.80,
    #     provenance={"api": "commercial_ais"},
    # )
    # return [pkt.to_dict()]
