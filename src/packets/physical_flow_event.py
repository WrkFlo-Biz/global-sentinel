from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.packets.schemas import PhysicalFlowEvent, utc_now_iso


@lru_cache(maxsize=1)
def _schema_versions() -> Dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "config" / "packet_schema_versions.yaml"
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def make_physical_flow_event(
    *,
    source: str,
    source_tier: str,
    trust_weight: float,
    region: str,
    flow_type: str,
    disruption_score: float,
    measured_value: Optional[float],
    unit: str,
    related_assets: List[str],
    summary: str,
    confidence: float,
    provenance: Dict[str, Any],
    schema_version: Optional[str] = None,
) -> PhysicalFlowEvent:
    raw = f"{source}|{region}|{flow_type}|{summary}"
    packet_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    return PhysicalFlowEvent(
        packet_type="physical_flow_event",
        packet_id=packet_id,
        source=source,
        source_tier=source_tier,
        timestamp_utc=utc_now_iso(),
        confidence=confidence,
        trust_weight=trust_weight,
        provenance=provenance,
        schema_version=schema_version or _schema_versions().get("physical_flow_event", "1.0.0"),
        region=region,
        flow_type=flow_type,
        disruption_score=disruption_score,
        measured_value=measured_value,
        unit=unit,
        related_assets=related_assets,
        summary=summary,
    )
