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
    expected_impact_bps: Optional[float] = None
    realized_slippage_bps: Optional[float] = None
    fill_rate: Optional[float] = None
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
