#!/usr/bin/env python3
"""Classical optimizer baseline for benchmarking against quantum results.

Deterministic greedy selector:
- Ranks candidates by score
- Enforces max_names constraint
- Applies sector concentration cap
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.packets.quantum_optimization_request import QuantumOptimizationRequest
from src.packets.quantum_optimization_result import QuantumOptimizationResult


class ClassicalOptimizerBaseline:
    """Deterministic classical baseline for paired quantum benchmarking."""

    def run(self, req: QuantumOptimizationRequest) -> QuantumOptimizationResult:
        max_names = int(req.constraints.get("max_names", 10))
        max_sector_weight = float(req.constraints.get("max_sector_weight", 1.0))

        ranked = sorted(
            req.candidate_universe,
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )

        selected: List[Dict[str, Any]] = []
        sector_counts: Dict[str, int] = {}
        sector_cap_count = max(1, int(max_names * max_sector_weight))

        for row in ranked:
            sector = row.get("sector", "unknown")
            if sector_counts.get(sector, 0) >= sector_cap_count:
                continue
            selected.append(row)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            if len(selected) >= max_names:
                break

        return QuantumOptimizationResult(
            request_id=req.request_id,
            package_id=req.package_id,
            solver="classical_baseline",
            success=True,
            ranked_solutions=selected,
            objective_value=float(sum(float(x.get("score", 0.0)) for x in selected)),
            feasibility=1.0,
            diagnostics={
                "baseline": True,
                "selection_count": len(selected),
                "sector_cap_count": sector_cap_count,
            },
            provenance=req.provenance,
        )
