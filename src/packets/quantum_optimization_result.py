from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass
class QuantumOptimizationResult:
    request_id: str
    package_id: str
    solver: str
    success: bool
    ranked_solutions: List[Dict[str, Any]]
    objective_value: float
    feasibility: float
    diagnostics: Dict[str, Any]
    provenance: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
