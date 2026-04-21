#!/usr/bin/env python3
"""Convert evaluation output into a bounded research score for Sentinel.

The research score is a [0, 1] value that indicates whether quantum
optimization is outperforming classical baseline on real trade outcomes.
It is NOT a direct execution signal — it's a secondary research metric.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from src.research.research_guardrail_checker import ResearchGuardrailChecker


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def validate_research_score_inputs(
    evaluation: Dict[str, Any],
    *,
    min_executed_trade_count: int = 5,
    min_walk_forward_folds: int = 2,
) -> Dict[str, Any]:
    executed_trade_count = _safe_int(
        evaluation.get("executed_trade_count", evaluation.get("trade_count", 0)),
        0,
    )
    walk_forward = evaluation.get("walk_forward_validation") or {}
    if not isinstance(walk_forward, dict):
        walk_forward = {}

    folds_run = _safe_int(
        walk_forward.get("folds_run", walk_forward.get("fold_count", 0)),
        0,
    )
    walk_forward_passed = bool(walk_forward.get("passed", False))

    checks = [
        {
            "name": "min_executed_trade_count",
            "passed": executed_trade_count >= min_executed_trade_count,
            "value": executed_trade_count,
            "threshold": min_executed_trade_count,
            "reason": (
                "ok"
                if executed_trade_count >= min_executed_trade_count
                else f"executed_trade_count {executed_trade_count} < {min_executed_trade_count}"
            ),
        },
        {
            "name": "walk_forward_validation_present",
            "passed": bool(walk_forward),
            "value": bool(walk_forward),
            "threshold": True,
            "reason": "ok" if walk_forward else "missing walk_forward_validation payload",
        },
        {
            "name": "walk_forward_min_folds",
            "passed": folds_run >= min_walk_forward_folds,
            "value": folds_run,
            "threshold": min_walk_forward_folds,
            "reason": (
                "ok"
                if folds_run >= min_walk_forward_folds
                else f"walk-forward folds {folds_run} < {min_walk_forward_folds}"
            ),
        },
        {
            "name": "walk_forward_passed",
            "passed": walk_forward_passed,
            "value": walk_forward_passed,
            "threshold": True,
            "reason": "ok" if walk_forward_passed else "walk-forward validation failed",
        },
    ]

    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "executed_trade_count": executed_trade_count,
        "walk_forward_validation": {
            **walk_forward,
            "folds_run": folds_run,
            "passed": walk_forward_passed,
        },
    }


def build_research_score(
    evaluation: Dict[str, Any],
    *,
    require_validation: bool = False,
    min_executed_trade_count: int = 5,
    min_walk_forward_folds: int = 2,
) -> Dict[str, Any]:
    q_overlap = safe_float(evaluation.get("quantum_overlap_score"))
    q_dir = safe_float(evaluation.get("quantum_directional_score"))
    c_overlap = safe_float(evaluation.get("classical_overlap_score"))
    c_dir = safe_float(evaluation.get("classical_directional_score"))

    q_real = safe_float(evaluation.get("quantum_realized_return_bps_sum"))
    c_real = safe_float(evaluation.get("classical_realized_return_bps_sum"))

    delta_real = q_real - c_real

    # Bounded contribution: 35% overlap + 35% directional + 30% realized delta
    raw = 0.35 * q_overlap + 0.35 * q_dir + 0.30 * (0.5 + (delta_real / 1000.0))
    raw_bounded = clamp(raw, 0.0, 1.0)

    validation = validate_research_score_inputs(
        evaluation,
        min_executed_trade_count=min_executed_trade_count,
        min_walk_forward_folds=min_walk_forward_folds,
    )
    validation_failed = require_validation and not validation["passed"]
    bounded = 0.0 if validation_failed else raw_bounded

    influence = "none"
    if validation_failed:
        influence = "none"
    elif bounded >= 0.70:
        influence = "research_positive"
    elif bounded >= 0.55:
        influence = "research_neutral_positive"
    elif bounded <= 0.35:
        influence = "research_negative"

    score = {
        "schema_version": "research_score.v1",
        "request_id": evaluation.get("request_id"),
        "package_id": evaluation.get("package_id"),
        "research_score": bounded,
        "raw_research_score": raw_bounded,
        "recommended_influence": influence,
        "confidence": 1.0 if validation["passed"] else 0.0,
        "not_for_direct_execution": True,
        "bounded_secondary_signal_only": True,
        "components": {
            "quantum_overlap_score": q_overlap,
            "quantum_directional_score": q_dir,
            "classical_overlap_score": c_overlap,
            "classical_directional_score": c_dir,
            "realized_return_bps_delta_quantum_minus_classical": delta_real,
        },
        "validation": validation,
        "guardrails": {
            "not_for_direct_execution": True,
            "bounded_secondary_signal_only": True,
            "require_validation": require_validation,
            "promotion_blocked": validation_failed,
        },
    }
    score["guardrail_result"] = ResearchGuardrailChecker().check_research_score(score).to_dict()
    return score


def parse_args():
    p = argparse.ArgumentParser(description="Write bounded research score from evaluation")
    p.add_argument("--evaluation-json", required=True)
    p.add_argument("--output-json", required=True)
    p.add_argument("--require-validation", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    evaluation = json.loads(Path(args.evaluation_json).read_text(encoding="utf-8"))
    score = build_research_score(evaluation, require_validation=args.require_validation)
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(score, indent=2), encoding="utf-8")
    print(json.dumps(score, indent=2))


if __name__ == "__main__":
    main()
