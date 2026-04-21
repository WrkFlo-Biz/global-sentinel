"""Canonical schema for real trade or shadow-trade outcome telemetry."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional


@dataclass
class TradeOutcomeRecord:
    symbol: str
    trade_executed: bool
    direction: str  # long | short
    realized_return_bps: float
    timestamp_utc: Optional[str] = None
    event_novelty_score: Optional[float] = None
    expected_edge_bps: Optional[float] = None
    expected_cost_bps: Optional[float] = None
    net_expected_value_bps: Optional[float] = None
    expected_impact_bps: Optional[float] = None
    realized_slippage_bps: Optional[float] = None
    fill_rate: Optional[float] = None
    max_favorable_excursion_bps: Optional[float] = None
    max_adverse_excursion_bps: Optional[float] = None
    time_to_edge_minutes: Optional[float] = None
    time_to_edge_score: Optional[float] = None
    time_to_edge_bucket: Optional[str] = None
    time_to_edge_label: Optional[str] = None
    fill_quality_score: Optional[float] = None
    fill_quality_label: Optional[str] = None
    execution_quality_label: Optional[str] = None
    alpha_label: Optional[str] = None
    realized_edge_capture_ratio: Optional[float] = None
    adverse_excursion_ratio: Optional[float] = None
    post_event_drift_bps: Optional[float] = None
    post_event_drift_score: Optional[float] = None
    post_event_drift_label: Optional[str] = None
    edge_decay_score: Optional[float] = None
    edge_decay_weight: Optional[float] = None
    edge_decay_label: Optional[str] = None
    sample_weight: Optional[float] = None
    time_window: Optional[str] = None
    incident_mode: Optional[bool] = None
    research_score_used: Optional[float] = None
    quantum_influenced: Optional[bool] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeOutcomeTelemetry:
    schema_version: str
    request_id: str
    package_id: str
    trades: List[TradeOutcomeRecord]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "package_id": self.package_id,
            "trades": [t.to_dict() for t in self.trades],
        }
