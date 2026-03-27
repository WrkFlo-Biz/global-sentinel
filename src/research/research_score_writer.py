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


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def build_research_score(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    q_overlap = safe_float(evaluation.get("quantum_overlap_score"))
    q_dir = safe_float(evaluation.get("quantum_directional_score"))
    c_overlap = safe_float(evaluation.get("classical_overlap_score"))
    c_dir = safe_float(evaluation.get("classical_directional_score"))

    q_real = safe_float(evaluation.get("quantum_realized_return_bps_sum"))
    c_real = safe_float(evaluation.get("classical_realized_return_bps_sum"))

    delta_real = q_real - c_real

    # Bounded contribution: 35% overlap + 35% directional + 30% realized delta
    raw = 0.35 * q_overlap + 0.35 * q_dir + 0.30 * (0.5 + (delta_real / 1000.0))
    bounded = clamp(raw, 0.0, 1.0)

    influence = "none"
    if bounded >= 0.70:
        influence = "research_positive"
    elif bounded >= 0.55:
        influence = "research_neutral_positive"
    elif bounded <= 0.35:
        influence = "research_negative"

    return {
        "schema_version": "research_score.v1",
        "request_id": evaluation.get("request_id"),
        "package_id": evaluation.get("package_id"),
        "research_score": bounded,
        "recommended_influence": influence,
        "components": {
            "quantum_overlap_score": q_overlap,
            "quantum_directional_score": q_dir,
            "classical_overlap_score": c_overlap,
            "classical_directional_score": c_dir,
            "realized_return_bps_delta_quantum_minus_classical": delta_real,
        },
        "guardrails": {
            "not_for_direct_execution": True,
            "bounded_secondary_signal_only": True,
        },
    }


def parse_args():
    p = argparse.ArgumentParser(description="Write bounded research score from evaluation")
    p.add_argument("--evaluation-json", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    evaluation = json.loads(Path(args.evaluation_json).read_text(encoding="utf-8"))
    score = build_research_score(evaluation)
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(score, indent=2), encoding="utf-8")
    print(json.dumps(score, indent=2))


if __name__ == "__main__":
    main()
