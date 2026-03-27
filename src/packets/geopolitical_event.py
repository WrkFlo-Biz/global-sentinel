from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.packets.schemas import GeopoliticalEvent, utc_now_iso


@lru_cache(maxsize=1)
def _schema_versions() -> Dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "config" / "packet_schema_versions.yaml"
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def make_geopolitical_event(
    *,
    source: str,
    source_tier: str,
    trust_weight: float,
    region: str,
    severity: float,
    event_category: str,
    energy_relevance: float,
    supply_chain_relevance: float,
    asset_channels: List[str],
    summary: str,
    confidence: float,
    provenance: Dict[str, Any],
    schema_version: Optional[str] = None,
) -> GeopoliticalEvent:
    raw = f"{source}|{region}|{event_category}|{summary}"
    packet_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    return GeopoliticalEvent(
        packet_type="geopolitical_event",
        packet_id=packet_id,
        source=source,
        source_tier=source_tier,
        timestamp_utc=utc_now_iso(),
        confidence=confidence,
        trust_weight=trust_weight,
        provenance=provenance,
        schema_version=schema_version or _schema_versions().get("geopolitical_event", "1.0.0"),
        region=region,
        severity=severity,
        event_category=event_category,
        energy_relevance=energy_relevance,
        supply_chain_relevance=supply_chain_relevance,
        asset_channels=asset_channels,
        summary=summary,
    )
