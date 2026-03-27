#!/usr/bin/env python3
"""Research evaluation harness for Global Sentinel.

Evaluates every research change before it can be promoted to production.
Scores across multiple dimensions and enforces minimum thresholds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# Default minimum threshold per dimension (each score is 0.0-1.0)
DEFAULT_MIN_THRESHOLDS: Dict[str, float] = {
    "predictive_quality": 0.5,
    "execution_realism": 0.5,
    "safety_compliance": 0.5,
    "reproducibility": 0.5,
    "drift_sensitivity": 0.5,
    "compute_cost": 0.5,
    "lineage_completeness": 0.5,
}

# Default weights for overall score (equal by default)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "predictive_quality": 1.0,
    "execution_realism": 1.0,
    "safety_compliance": 1.0,
    "reproducibility": 1.0,
    "drift_sensitivity": 1.0,
    "compute_cost": 1.0,
    "lineage_completeness": 1.0,
}


class ResearchEvalHarness:
    """Evaluates research changes across multiple quality dimensions."""

    def __init__(
        self,
        min_thresholds: Optional[Dict[str, float]] = None,
        weights: Optional[Dict[str, float]] = None,
        max_drift: float = 0.15,
        max_compute_seconds: float = 300.0,
    ):
        self.min_thresholds = {**DEFAULT_MIN_THRESHOLDS, **(min_thresholds or {})}
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.max_drift = max_drift
        self.max_compute_seconds = max_compute_seconds

    def evaluate(self, research_change: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a research change across all dimensions.

        Args:
            research_change: Dict containing research change metadata and metrics.

        Returns:
            Evaluation result dict with schema_version, scores, pass/fail, and diagnostics.
        """
        dimension_scores: Dict[str, float] = {}
        blocking_failures: List[str] = []
        warnings: List[str] = []

        # --- Score each dimension ---
        dimension_scores["predictive_quality"] = self._score_predictive_quality(
            research_change, warnings
        )
        dimension_scores["execution_realism"] = self._score_execution_realism(
            research_change, warnings
        )
        dimension_scores["safety_compliance"] = self._score_safety_compliance(
            research_change, warnings
        )
        dimension_scores["reproducibility"] = self._score_reproducibility(
            research_change, warnings
        )
        dimension_scores["drift_sensitivity"] = self._score_drift_sensitivity(
            research_change, warnings
        )
        dimension_scores["compute_cost"] = self._score_compute_cost(
            research_change, warnings
        )
        dimension_scores["lineage_completeness"] = self._score_lineage_completeness(
            research_change, warnings
        )

        # --- Check each dimension against its minimum threshold ---
        for dim, score in dimension_scores.items():
            threshold = self.min_thresholds.get(dim, 0.5)
            if score < threshold:
                blocking_failures.append(
                    f"{dim}: {score:.3f} < {threshold:.3f}"
                )

        # --- Compute weighted average ---
        total_weight = sum(self.weights.get(d, 1.0) for d in dimension_scores)
        if total_weight > 0:
            overall_score = sum(
                dimension_scores[d] * self.weights.get(d, 1.0)
                for d in dimension_scores
            ) / total_weight
        else:
            overall_score = 0.0

        overall_pass = len(blocking_failures) == 0

        return {
            "schema_version": "research_eval_harness.v1",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "overall_pass": overall_pass,
            "overall_score": round(overall_score, 4),
            "dimension_scores": {k: round(v, 4) for k, v in dimension_scores.items()},
            "blocking_failures": blocking_failures,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Dimension scorers
    # ------------------------------------------------------------------

    def _score_predictive_quality(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check if research_score, sharpe_ratio, or win_rate meet thresholds."""
        scores: List[float] = []

        research_score = rc.get("research_score")
        if research_score is not None and isinstance(research_score, (int, float)):
            # Clamp to [0, 1]; scores already expected in that range
            scores.append(max(0.0, min(1.0, float(research_score))))
        else:
            warnings.append("predictive_quality: research_score missing or non-numeric")

        sharpe = rc.get("sharpe_ratio")
        if sharpe is not None and isinstance(sharpe, (int, float)):
            # Map sharpe: 0 -> 0.0, 1.0 -> 0.5, 2.0 -> 1.0 (clamped)
            scores.append(max(0.0, min(1.0, float(sharpe) / 2.0)))
        else:
            warnings.append("predictive_quality: sharpe_ratio missing or non-numeric")

        win_rate = rc.get("win_rate")
        if win_rate is not None and isinstance(win_rate, (int, float)):
            scores.append(max(0.0, min(1.0, float(win_rate))))
        else:
            warnings.append("predictive_quality: win_rate missing or non-numeric")

        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def _score_execution_realism(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check if slippage_adjusted_delta exists and is positive."""
        sad = rc.get("slippage_adjusted_delta")
        if sad is None:
            warnings.append("execution_realism: slippage_adjusted_delta missing")
            return 0.0
        if not isinstance(sad, (int, float)):
            warnings.append("execution_realism: slippage_adjusted_delta non-numeric")
            return 0.0
        if sad <= 0:
            return 0.0
        # Positive delta: score scales linearly, 1.0 at delta >= 0.01
        return min(1.0, float(sad) / 0.01)

    def _score_safety_compliance(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check not_for_direct_execution and bounded_secondary_signal_only flags."""
        checks_passed = 0
        total_checks = 2

        nfde = rc.get("not_for_direct_execution")
        if nfde is True:
            checks_passed += 1
        else:
            warnings.append(
                "safety_compliance: not_for_direct_execution is not True"
            )

        bsso = rc.get("bounded_secondary_signal_only")
        if bsso is True:
            checks_passed += 1
        else:
            warnings.append(
                "safety_compliance: bounded_secondary_signal_only is not True"
            )

        return checks_passed / total_checks

    def _score_reproducibility(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check if training_dataset_hash and code_version are present."""
        checks_passed = 0
        total_checks = 2

        tdh = rc.get("training_dataset_hash")
        if tdh is not None and isinstance(tdh, str) and len(tdh) > 0:
            checks_passed += 1
        else:
            warnings.append("reproducibility: training_dataset_hash missing or empty")

        cv = rc.get("code_version")
        if cv is not None and isinstance(cv, str) and len(cv) > 0:
            checks_passed += 1
        else:
            warnings.append("reproducibility: code_version missing or empty")

        return checks_passed / total_checks

    def _score_drift_sensitivity(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check if drift_score < max_drift."""
        drift = rc.get("drift_score")
        if drift is None:
            warnings.append("drift_sensitivity: drift_score missing")
            return 0.0
        if not isinstance(drift, (int, float)):
            warnings.append("drift_sensitivity: drift_score non-numeric")
            return 0.0
        if drift < 0:
            warnings.append("drift_sensitivity: negative drift_score")
            return 0.0
        if drift >= self.max_drift:
            return 0.0
        # Linear scale: 0 drift -> 1.0, max_drift -> 0.0
        return 1.0 - (float(drift) / self.max_drift)

    def _score_compute_cost(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check if elapsed_seconds < max_compute_seconds."""
        elapsed = rc.get("elapsed_seconds")
        if elapsed is None:
            warnings.append("compute_cost: elapsed_seconds missing")
            return 0.0
        if not isinstance(elapsed, (int, float)):
            warnings.append("compute_cost: elapsed_seconds non-numeric")
            return 0.0
        if elapsed < 0:
            warnings.append("compute_cost: negative elapsed_seconds")
            return 0.0
        if elapsed >= self.max_compute_seconds:
            return 0.0
        # Linear scale: 0 seconds -> 1.0, max -> 0.0
        return 1.0 - (float(elapsed) / self.max_compute_seconds)

    def _score_lineage_completeness(
        self, rc: Dict[str, Any], warnings: List[str]
    ) -> float:
        """Check if parent_artifact_ids and source_packet_hashes are present."""
        checks_passed = 0
        total_checks = 2

        pai = rc.get("parent_artifact_ids")
        if pai is not None and isinstance(pai, list) and len(pai) > 0:
            checks_passed += 1
        else:
            warnings.append("lineage_completeness: parent_artifact_ids missing or empty")

        sph = rc.get("source_packet_hashes")
        if sph is not None and isinstance(sph, list) and len(sph) > 0:
            checks_passed += 1
        else:
            warnings.append("lineage_completeness: source_packet_hashes missing or empty")

        return checks_passed / total_checks
