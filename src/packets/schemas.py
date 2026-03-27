from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BasePacket:
    packet_type: str
    packet_id: str
    source: str
    source_tier: str
    timestamp_utc: str
    confidence: float
    trust_weight: float
    provenance: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MacroPolicyEvent(BasePacket):
    topic: str = ""
    policy_domain: str = ""
    hawkish_dovish_score: float = 0.0
    growth_inflation_score: float = 0.0
    market_relevance_score: float = 0.0
    related_assets: List[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class GeopoliticalEvent(BasePacket):
    region: str = ""
    severity: float = 0.0
    event_category: str = ""
    energy_relevance: float = 0.0
    supply_chain_relevance: float = 0.0
    asset_channels: List[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class PhysicalFlowEvent(BasePacket):
    region: str = ""
    flow_type: str = ""
    disruption_score: float = 0.0
    measured_value: Optional[float] = None
    unit: str = ""
    related_assets: List[str] = field(default_factory=list)
    summary: str = ""
