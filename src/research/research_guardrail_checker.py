#!/usr/bin/env python3
"""Research guardrail checker for Global Sentinel.

Validates ALL research outputs before they can influence any downstream system.
Must pass before: score attachment, weight promotion, training dataset building.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class GuardrailResult:
    passed: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""
    checker_version: str = "1.0.0"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ResearchGuardrailChecker:
    """Validates all research outputs before downstream influence."""

    def __init__(self, max_weight_step: float = 0.05, min_eval_count: int = 50):
        self.max_weight_step = max_weight_step
        self.min_eval_count = min_eval_count

    def check_research_score(self, score: Dict[str, Any]) -> GuardrailResult:
        checks = []

        # Check: not_for_direct_execution flag
        nfde = score.get("not_for_direct_execution", False)
        checks.append({
            "name": "not_for_direct_execution",
            "passed": bool(nfde),
            "value": nfde,
            "threshold": True,
            "reason": "flag must be True" if not nfde else "ok",
        })

        # Check: bounded_secondary_signal_only flag
        bsso = score.get("bounded_secondary_signal_only", score.get("not_for_direct_execution", False))
        checks.append({
            "name": "bounded_secondary_signal_only",
            "passed": bool(bsso),
            "value": bsso,
            "threshold": True,
            "reason": "flag must be True" if not bsso else "ok",
        })

        # Check: score in [0, 1]
        rs = score.get("research_score")
        in_range = rs is not None and 0.0 <= rs <= 1.0
        checks.append({
            "name": "score_in_range",
            "passed": in_range,
            "value": rs,
            "threshold": "[0.0, 1.0]",
            "reason": f"score {rs} out of [0,1]" if not in_range else "ok",
        })

        # Check: quantum_direct_execution_forbidden if quantum-sourced
        if score.get("quantum_sourced"):
            qdef = score.get("quantum_direct_execution_forbidden", False)
            checks.append({
                "name": "quantum_direct_execution_forbidden",
                "passed": bool(qdef),
                "value": qdef,
                "threshold": True,
                "reason": "quantum-sourced score must have this flag" if not qdef else "ok",
            })

        # Check: schema_version present
        sv = score.get("schema_version")
        checks.append({
            "name": "schema_version_present",
            "passed": sv is not None,
            "value": sv,
            "threshold": "not None",
            "reason": "missing schema_version" if sv is None else "ok",
        })

        # Check: confidence present and reasonable
        conf = score.get("confidence")
        conf_ok = conf is not None and isinstance(conf, (int, float)) and 0 <= conf <= 1
        checks.append({
            "name": "confidence_valid",
            "passed": conf_ok,
            "value": conf,
            "threshold": "[0.0, 1.0]",
            "reason": "missing or invalid confidence" if not conf_ok else "ok",
        })

        passed = all(c["passed"] for c in checks)
        return GuardrailResult(passed=passed, checks=checks)

    def check_weight_update(
        self,
        current_weights: Dict[str, float],
        proposed_weights: Dict[str, float],
        learning_state: Dict[str, Any],
    ) -> GuardrailResult:
        checks = []

        # Check: max absolute step per weight
        all_keys = set(list(current_weights.keys()) + list(proposed_weights.keys()))
        max_delta_found = 0.0
        step_violations = []
        for k in all_keys:
            delta = abs(proposed_weights.get(k, 0.0) - current_weights.get(k, 0.0))
            max_delta_found = max(max_delta_found, delta)
            if delta > self.max_weight_step:
                step_violations.append(f"{k}={delta:.4f}")

        checks.append({
            "name": "max_weight_step",
            "passed": len(step_violations) == 0,
            "value": max_delta_found,
            "threshold": self.max_weight_step,
            "reason": f"violations: {', '.join(step_violations)}" if step_violations else "ok",
        })

        # Check: no NaN or Inf
        nan_inf_keys = []
        for k, v in proposed_weights.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                nan_inf_keys.append(k)

        checks.append({
            "name": "no_nan_or_inf",
            "passed": len(nan_inf_keys) == 0,
            "value": nan_inf_keys,
            "threshold": "none",
            "reason": f"NaN/Inf in: {nan_inf_keys}" if nan_inf_keys else "ok",
        })

        # Check: minimum eval count
        update_stats = learning_state.get("update_stats", {})
        eval_count = update_stats.get("updates_applied", 0)
        checks.append({
            "name": "min_eval_count",
            "passed": eval_count >= self.min_eval_count,
            "value": eval_count,
            "threshold": self.min_eval_count,
            "reason": f"eval_count {eval_count} < {self.min_eval_count}" if eval_count < self.min_eval_count else "ok",
        })

        passed = all(c["passed"] for c in checks)
        return GuardrailResult(passed=passed, checks=checks)

    def check_training_dataset(self, dataset: Dict[str, Any]) -> GuardrailResult:
        checks = []
        rows = dataset.get("rows", [])

        # Check: non-empty
        checks.append({
            "name": "non_empty_dataset",
            "passed": len(rows) > 0,
            "value": len(rows),
            "threshold": "> 0",
            "reason": "empty dataset" if len(rows) == 0 else "ok",
        })

        # Check: required fields present
        required = ["symbol"]
        missing_fields = []
        for i, row in enumerate(rows):
            for f in required:
                if f not in row:
                    missing_fields.append(f"row[{i}].{f}")

        checks.append({
            "name": "required_fields",
            "passed": len(missing_fields) == 0,
            "value": len(missing_fields),
            "threshold": 0,
            "reason": f"missing: {missing_fields[:5]}" if missing_fields else "ok",
        })

        # Check: no duplicate rows by symbol + timestamp
        seen = set()
        duplicates = 0
        for row in rows:
            key = (row.get("symbol", ""), row.get("timestamp_utc", ""))
            if key in seen:
                duplicates += 1
            seen.add(key)

        checks.append({
            "name": "no_duplicate_rows",
            "passed": duplicates == 0,
            "value": duplicates,
            "threshold": 0,
            "reason": f"{duplicates} duplicates found" if duplicates > 0 else "ok",
        })

        # Check: label distribution not degenerate (if labels present)
        labels = [row.get("alpha_label") for row in rows if row.get("alpha_label")]
        if labels:
            unique_labels = set(labels)
            degenerate = len(unique_labels) <= 1 and len(labels) > 5
            checks.append({
                "name": "label_distribution",
                "passed": not degenerate,
                "value": len(unique_labels),
                "threshold": "> 1 for datasets with > 5 labeled rows",
                "reason": "degenerate label distribution" if degenerate else "ok",
            })

        passed = all(c["passed"] for c in checks)
        return GuardrailResult(passed=passed, checks=checks)
