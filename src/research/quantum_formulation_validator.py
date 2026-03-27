#!/usr/bin/env python3
"""Validate quantum optimization requests against the formulation registry."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class QuantumFormulationValidator:
    """Validates quantum requests against registered formulations."""

    def __init__(self, registry_path: Optional[Path] = None):
        path = registry_path or Path("config/qfinance_formulation_registry.yaml")
        self._registry = _load_yaml(path)

    def validate(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a quantum optimization request.

        Returns validation result with pass/fail and details.
        """
        checks = []
        formulation_id = request.get("formulation_id", "")
        formulations = self._registry.get("formulations", {})
        spec = formulations.get(formulation_id)

        if not spec:
            # Try to match by problem family
            family = request.get("problem_family", request.get("objective_type", ""))
            for fid, fspec in formulations.items():
                if fspec.get("problem_family") == family:
                    spec = fspec
                    formulation_id = fid
                    break

        if not spec:
            return {
                "valid": False,
                "formulation_id": formulation_id,
                "reason": "no matching formulation in registry",
                "checks": [],
            }

        # Check decision variable count
        n_vars = request.get("decision_variable_count", request.get("candidate_count", 0))
        max_vars = spec.get("max_decision_variables", 100)
        var_ok = n_vars <= max_vars
        checks.append({"check": "max_decision_variables", "passed": var_ok, "value": n_vars, "limit": max_vars})

        # Check circuit depth
        depth = request.get("circuit_depth", 0)
        max_depth = spec.get("max_circuit_depth", 100)
        depth_ok = depth <= max_depth or depth == 0
        checks.append({"check": "max_circuit_depth", "passed": depth_ok, "value": depth, "limit": max_depth})

        # Check shots
        shots = request.get("shots", spec.get("default_shots", 1000))
        checks.append({"check": "shots", "passed": True, "value": shots, "limit": "n/a"})

        # Check encoding
        encoding = request.get("encoding", spec.get("encoding", ""))
        encoding_match = encoding == spec.get("encoding", "") or encoding == ""
        checks.append({"check": "encoding_match", "passed": encoding_match, "value": encoding, "expected": spec.get("encoding")})

        all_passed = all(c["passed"] for c in checks)

        return {
            "valid": all_passed,
            "formulation_id": formulation_id,
            "formulation_spec": spec,
            "checks": checks,
            "reason": "all checks passed" if all_passed else "validation failed",
        }
