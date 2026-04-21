#!/usr/bin/env python3
"""Hybrid Decomposition-First Policy for Global Sentinel V4.

Enforces problem reduction before quantum optimization:
1. Classically prune the candidate universe first
2. Reduce problem size to tractable limits
3. Validate formulation before quantum execution
4. Prefer structured/recursive QAOA only after validation
5. Fall back to simulator/classical if unstable

Integrates with QuantumOptimizerBridge as a pre-processing step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default limits
DEFAULT_MAX_QUBITS = 20
DEFAULT_MAX_CANDIDATES = 15
DEFAULT_MIN_CANDIDATES_FOR_QUANTUM = 4
DEFAULT_MAX_CIRCUIT_DEPTH = 100
DEFAULT_CLASSICAL_PRUNE_KEEP_RATIO = 0.6
DEFAULT_SHOT_BUDGET = 1024


class QuantumDecompositionPolicy:
    """Enforce decomposition-first policy before quantum optimization."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, config_dir: Optional[Path] = None):
        self._config = config or {}
        if not self._config and config_dir:
            self._config = self._load_config(config_dir)

        self.max_qubits = int(self._config.get("max_qubits", DEFAULT_MAX_QUBITS))
        self.max_candidates = int(self._config.get("max_candidates", DEFAULT_MAX_CANDIDATES))
        self.min_candidates = int(self._config.get("min_candidates_for_quantum", DEFAULT_MIN_CANDIDATES_FOR_QUANTUM))
        self.max_circuit_depth = int(self._config.get("max_circuit_depth", DEFAULT_MAX_CIRCUIT_DEPTH))
        self.prune_keep_ratio = float(self._config.get("classical_prune_keep_ratio", DEFAULT_CLASSICAL_PRUNE_KEEP_RATIO))
        self.shot_budget = int(self._config.get("shot_budget", DEFAULT_SHOT_BUDGET))

    @staticmethod
    def _load_config(config_dir: Path) -> Dict[str, Any]:
        try:
            import yaml
            path = config_dir / "quantum_lane_policy.yaml"
            if path.exists():
                cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                return cfg.get("decomposition_policy", cfg)
        except Exception:
            pass
        return {}

    def preprocess(
        self,
        candidates: List[Dict[str, Any]],
        objective: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run decomposition-first pipeline on candidates.

        Returns a preprocessed result with pruned candidates, problem sizing,
        and a recommendation on whether to proceed with quantum.
        """
        original_count = len(candidates)
        steps: List[Dict[str, Any]] = []

        # Step 1: Classical pruning — keep top candidates by score
        pruned = self._classical_prune(candidates)
        steps.append({
            "step": "classical_prune",
            "input_count": original_count,
            "output_count": len(pruned),
            "keep_ratio": self.prune_keep_ratio,
        })

        # Step 2: Size reduction — enforce qubit/candidate limits
        reduced, reduction_applied = self._enforce_size_limits(pruned)
        steps.append({
            "step": "size_reduction",
            "input_count": len(pruned),
            "output_count": len(reduced),
            "reduction_applied": reduction_applied,
            "max_candidates": self.max_candidates,
        })

        # Step 3: Problem sizing — estimate circuit requirements
        sizing = self._estimate_problem_size(reduced, objective)
        steps.append({
            "step": "problem_sizing",
            **sizing,
        })

        # Step 4: Formulation validation
        formulation_ok, formulation_reason = self._validate_formulation(sizing, objective)
        steps.append({
            "step": "formulation_validation",
            "pass": formulation_ok,
            "reason": formulation_reason,
        })

        # Step 5: Stability check — decide quantum vs classical
        use_quantum, quantum_reason = self._should_use_quantum(
            len(reduced), sizing, formulation_ok
        )

        recommendation = "quantum" if use_quantum else "classical_fallback"
        fallback_reason = None if use_quantum else quantum_reason

        return {
            "schema_version": "decomposition_policy.v1",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "original_candidate_count": original_count,
            "pruned_candidate_count": len(reduced),
            "candidates": reduced,
            "recommendation": recommendation,
            "fallback_reason": fallback_reason,
            "problem_sizing": sizing,
            "formulation_valid": formulation_ok,
            "preprocessing_steps": steps,
            "shot_budget": self.shot_budget,
            "not_for_direct_execution": True,
        }

    def _classical_prune(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep top candidates by preopt_feature_score."""
        if not candidates:
            return []

        sorted_cands = sorted(
            candidates,
            key=lambda c: float(c.get("preopt_feature_score", c.get("score", 0.0))),
            reverse=True,
        )

        keep_count = max(1, int(len(sorted_cands) * self.prune_keep_ratio))
        return sorted_cands[:keep_count]

    def _enforce_size_limits(
        self, candidates: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Truncate to max_candidates if needed."""
        if len(candidates) <= self.max_candidates:
            return candidates, False
        return candidates[: self.max_candidates], True

    def _estimate_problem_size(
        self, candidates: List[Dict[str, Any]], objective: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Estimate qubit count, circuit depth, and parameter count."""
        n = len(candidates)
        # Binary encoding: 1 qubit per candidate
        qubits_needed = n
        # QAOA depth scales with problem size
        estimated_depth = min(n * 3, self.max_circuit_depth)
        # Parameters: 2 per QAOA layer (gamma, beta)
        p_layers = min(max(1, n // 3), 5)
        param_count = 2 * p_layers

        return {
            "candidate_count": n,
            "qubits_needed": qubits_needed,
            "estimated_circuit_depth": estimated_depth,
            "qaoa_layers": p_layers,
            "parameter_count": param_count,
            "within_qubit_limit": qubits_needed <= self.max_qubits,
            "within_depth_limit": estimated_depth <= self.max_circuit_depth,
        }

    def _validate_formulation(
        self, sizing: Dict[str, Any], objective: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Validate that the problem formulation is tractable."""
        if not sizing.get("within_qubit_limit", False):
            return False, f"exceeds_qubit_limit:{sizing['qubits_needed']}>{self.max_qubits}"
        if not sizing.get("within_depth_limit", False):
            return False, f"exceeds_depth_limit:{sizing['estimated_circuit_depth']}>{self.max_circuit_depth}"
        if sizing.get("candidate_count", 0) < self.min_candidates:
            return False, f"too_few_candidates:{sizing['candidate_count']}<{self.min_candidates}"
        return True, "formulation_valid"

    def _should_use_quantum(
        self,
        candidate_count: int,
        sizing: Dict[str, Any],
        formulation_ok: bool,
    ) -> Tuple[bool, str]:
        """Decide whether to proceed with quantum or fall back to classical."""
        if not formulation_ok:
            return False, "formulation_invalid"
        if candidate_count < self.min_candidates:
            return False, f"too_few_candidates_for_quantum_advantage:{candidate_count}"
        if sizing.get("qubits_needed", 0) > self.max_qubits:
            return False, "exceeds_qubit_limit"
        return True, "quantum_recommended"
