#!/usr/bin/env python3
"""Maritime AIS bridge for chokepoint disruption scoring.

This is the canonical module name used by the main repo. It wraps the v2
implementation and normalizes the emitted packets so downstream packet handling
sees consistent packet shapes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.bridges.maritime_bridge_v2 import MaritimeBridgeV2
from src.core.structured_logger import get_logger
from src.core.telemetry import record_metric, start_span
from src.packets.physical_flow_event import make_physical_flow_event


logger = get_logger("maritime_bridge")


class MaritimeBridge(MaritimeBridgeV2):
    """Compatibility wrapper around :class:`MaritimeBridgeV2`."""

    def __init__(self, repo_root: Optional[Path] = None):
        super().__init__(repo_root=repo_root)

    def poll(self) -> List[Dict[str, Any]]:
        with start_span("bridge.fetch.maritime", bridge_name="maritime_bridge"):
            try:
                packets: List[Dict[str, Any]] = []
                for raw in super().poll():
                    packet = make_physical_flow_event(
                        source=str(raw.get("source", "maritime")),
                        source_tier=str(raw.get("source_tier", "tier_2_operational")),
                        trust_weight=float(raw.get("trust_weight", 0.8)),
                        region=str(raw.get("region", "unknown")),
                        flow_type=str(raw.get("flow_type", "maritime")),
                        disruption_score=float(raw.get("disruption_score", 0.0)),
                        measured_value=raw.get("measured_value"),
                        unit=str(raw.get("unit", "score")),
                        related_assets=list(raw.get("related_assets", [])),
                        summary=str(raw.get("summary", raw.get("region_name", "maritime chokepoint activity"))),
                        confidence=float(raw.get("confidence", 0.75)),
                        provenance=dict(raw.get("provenance", {})),
                    ).to_dict()
                    packet.update(
                        {
                            "vessel_count": raw.get("vessel_count"),
                            "avg_speed_knots": raw.get("avg_speed_knots"),
                            "_lineage": raw.get("_lineage", {}),
                        }
                    )
                    packets.append(packet)
                record_metric("bridge_fetch_success_total", 1, bridge_name="maritime_bridge")
                record_metric("bridge_packet_throughput_total", len(packets), bridge_name="maritime_bridge")
                logger.info("bridge_poll_success", bridge_name="maritime_bridge", packet_count=len(packets))
                return packets
            except Exception as exc:
                record_metric("bridge_fetch_failure_total", 1, bridge_name="maritime_bridge")
                logger.error("bridge_poll_failed", bridge_name="maritime_bridge", error=str(exc))
                raise
