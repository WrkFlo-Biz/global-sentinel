#!/usr/bin/env python3
"""Apply configured stale-packet actions per source."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from src.core.structured_logger import get_logger


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class LatePacketHandler:
    """Handle packets that arrive after their configured freshness TTL."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path("config")
        policy = _load_yaml(self.config_dir / "freshness_policy.yaml")
        self._source_policies = policy.get("sources", {})
        self._logger = get_logger("late_packet_handler")

    def _policy_for(self, source: str) -> Dict[str, Any]:
        return self._source_policies.get(source) or self._source_policies.get(source.removesuffix("_bridge")) or {}

    def handle(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        source = str(packet.get("source", "unknown"))
        policy = self._policy_for(source)
        action = str(policy.get("stale_action", "annotate_stale"))
        override_weight = policy.get("stale_trust_weight_override")
        packet.setdefault("_late_packet", {})
        packet["_late_packet"].update({"source": source, "configured_action": action})

        if action == "discard":
            packet["_late_packet"]["action"] = "discarded"
            self._logger.info("late_packet_discarded", packet_id=packet.get("packet_id"), source=source)
            return packet

        if action == "degrade_trust_weight" and override_weight is not None:
            original = float(packet.get("trust_weight", 1.0))
            packet["trust_weight"] = min(original, float(override_weight))
            packet["_late_packet"].update(
                {
                    "action": "degraded",
                    "original_trust_weight": original,
                    "degraded_trust_weight": packet["trust_weight"],
                }
            )
            self._logger.info(
                "late_packet_degraded",
                packet_id=packet.get("packet_id"),
                source=source,
                original_trust_weight=original,
                degraded_trust_weight=packet["trust_weight"],
            )
            return packet

        packet["_late_packet"]["action"] = "annotated_stale"
        self._logger.info("late_packet_annotated", packet_id=packet.get("packet_id"), source=source)
        return packet
