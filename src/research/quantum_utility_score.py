#!/usr/bin/env python3
"""Multi-dimensional evaluation of quantum lane value.

NOT just P&L delta. Evaluates objective improvement, runtime, feasibility,
stability, and net operational usefulness.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class QuantumUtilityScorer:
    """Multi-dimensional quantum utility scoring."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.weights = weights or {
            "objective_improvement": 0.25,
            "runtime_efficiency": 0.10,
            "feasibility_rate": 0.15,
            "rerun_stability": 0.15,
            "recommendation_overlap": 0.10,
            "slippage_adjusted_delta": 0.25,
        }

    def score(
        self,
        quantum_result: Dict[str, Any],
        classical_result: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata = metadata or {}

        q_obj = quantum_result.get("objective_value", quantum_result.get("sharpe_ratio", 0))
        c_obj = classical_result.get("objective_value", classical_result.get("sharpe_ratio", 0))

        # Objective improvement vs classical
        if abs(c_obj) > 1e-8:
            obj_improvement = (q_obj - c_obj) / abs(c_obj)
        else:
            obj_improvement = 0.0 if abs(q_obj) < 1e-8 else 1.0

        # Runtime ratio
        q_time = quantum_result.get("elapsed_seconds", 1.0)
        c_time = classical_result.get("elapsed_seconds", 1.0)
        runtime_ratio = q_time / max(c_time, 0.001)
        runtime_efficiency = max(0, 1.0 - (runtime_ratio - 1.0) / 10.0)

        # Feasibility rate
        feasibility = float(metadata.get("feasibility_rate", 1.0))

        # Rerun stability
        stability = float(metadata.get("rerun_stability", 1.0))

        # Recommendation overlap stability
        overlap = float(metadata.get("recommendation_overlap_stability", 1.0))

        # Slippage-adjusted delta
        slippage_delta = float(metadata.get("slippage_adjusted_delta_bps", 0)) / 100.0
        slippage_score = min(1.0, max(0.0, slippage_delta + 0.5))

        # Component scores normalized to [0, 1]
        components = {
            "objective_improvement": min(1.0, max(0.0, obj_improvement + 0.5)),
            "runtime_efficiency": min(1.0, max(0.0, runtime_efficiency)),
            "feasibility_rate": feasibility,
            "rerun_stability": stability,
            "recommendation_overlap": overlap,
            "slippage_adjusted_delta": slippage_score,
        }

        # Weighted composite
        overall = sum(
            components.get(k, 0) * w
            for k, w in self.weights.items()
        )

        return {
            "schema_version": "quantum_utility_score.v1",
            "overall_utility": round(overall, 4),
            "components": {k: round(v, 4) for k, v in components.items()},
            "quantum_objective": round(q_obj, 6),
            "classical_objective": round(c_obj, 6),
            "objective_delta": round(q_obj - c_obj, 6),
            "runtime_ratio": round(runtime_ratio, 4),
            "weights_used": self.weights,
            "not_for_direct_execution": True,
        }

    def statistical_significance(
        self,
        quantum_scores: List[float],
        classical_scores: List[float],
    ) -> Dict[str, Any]:
        """Paired t-test and Wilcoxon signed-rank test."""
        n = min(len(quantum_scores), len(classical_scores))
        if n < 3:
            return {"significant": False, "reason": f"insufficient_samples_{n}", "sample_size": n}

        try:
            from scipy import stats
            q = quantum_scores[:n]
            c = classical_scores[:n]

            t_stat, t_p = stats.ttest_rel(q, c)
            try:
                w_stat, w_p = stats.wilcoxon([q[i] - c[i] for i in range(n)])
            except ValueError:
                w_stat, w_p = 0.0, 1.0

            return {
                "sample_size": n,
                "t_statistic": round(float(t_stat), 4),
                "t_p_value": round(float(t_p), 6),
                "wilcoxon_statistic": round(float(w_stat), 4),
                "wilcoxon_p_value": round(float(w_p), 6),
                "significant": float(t_p) < 0.05,
                "quantum_mean": round(sum(q) / n, 6),
                "classical_mean": round(sum(c) / n, 6),
            }
        except ImportError:
            # Manual paired t-test
            diffs = [quantum_scores[i] - classical_scores[i] for i in range(n)]
            mean_d = sum(diffs) / n
            var_d = sum((d - mean_d) ** 2 for d in diffs) / max(n - 1, 1)
            se = (var_d / n) ** 0.5 if var_d > 0 else 1e-8
            t_stat = mean_d / se
            return {
                "sample_size": n,
                "t_statistic": round(t_stat, 4),
                "significant": abs(t_stat) > 2.0,
                "quantum_mean": round(sum(quantum_scores[:n]) / n, 6),
                "classical_mean": round(sum(classical_scores[:n]) / n, 6),
                "note": "scipy unavailable; manual t-test",
            }
