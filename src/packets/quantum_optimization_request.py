from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass
class QuantumOptimizationRequest:
    request_id: str
    package_id: str
    timestamp_utc: str
    runtime_flags: Dict[str, Any]
    time_window_state: Dict[str, Any]
    regime_state: Dict[str, Any]
    objective: Dict[str, Any]
    constraints: Dict[str, Any]
    candidate_universe: List[Dict[str, Any]]
    market_microstructure: Dict[str, Any]
    provenance: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
