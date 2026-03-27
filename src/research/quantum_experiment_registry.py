#!/usr/bin/env python3
"""Loader for the quantum finance experiment registry.

The registry is informational and research-only. It standardizes experiment
families, expected classical comparators, and whether local simulation or cloud
hardware should be used for each experiment class.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass(frozen=True)
class QuantumExperimentSpec:
    experiment_id: str
    problem_family: str
    algorithm_family: str
    local_sim_support: bool
    qcloud_support: bool
    classical_comparator: str
    artifact_schema: str
    reproducibility_required: bool
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QuantumExperimentRegistry:
    """Registry of allowed quantum finance experiment families."""

    def __init__(self, experiments: Dict[str, QuantumExperimentSpec]):
        self._experiments = experiments

    @classmethod
    def load(cls, path: Path) -> "QuantumExperimentRegistry":
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        experiments: Dict[str, QuantumExperimentSpec] = {}
        for experiment_id, entry in (payload.get("experiments") or {}).items():
            experiments[str(experiment_id)] = QuantumExperimentSpec(
                experiment_id=str(experiment_id),
                problem_family=str(entry.get("problem_family", "")),
                algorithm_family=str(entry.get("algorithm_family", "")),
                local_sim_support=bool(entry.get("local_sim_support", True)),
                qcloud_support=bool(entry.get("qcloud_support", False)),
                classical_comparator=str(entry.get("classical_comparator", "classical_baseline")),
                artifact_schema=str(entry.get("artifact_schema", "quantum_experiment_artifact.v1")),
                reproducibility_required=bool(entry.get("reproducibility_required", True)),
                notes=str(entry.get("notes", "")),
            )
        return cls(experiments)

    def get(self, experiment_id: str) -> Optional[QuantumExperimentSpec]:
        return self._experiments.get(str(experiment_id))

    def resolve_for_objective(self, objective_type: str) -> Optional[QuantumExperimentSpec]:
        lowered = str(objective_type or "").strip().lower()
        for spec in self._experiments.values():
            if spec.problem_family == lowered or spec.experiment_id == lowered:
                return spec
        return None

    def list_experiments(self) -> List[QuantumExperimentSpec]:
        return list(self._experiments.values())
