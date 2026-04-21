"""Persisted online-learning state for QFinance research weights.

Stores feature weights, update statistics, and guardrails.
Initializes with sensible defaults if no prior state exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_STATE = {
    "schema_version": "qfinance_online_learning_state.v1",
    "version": 1,
    "weights": {
        "base_score": 0.35,
        "event_score": 0.20,
        "quality_score": 0.15,
        "anomaly_score": 0.10,
        "liquidity_score": 0.15,
        "regime_alignment": 0.15,
        "volatility_penalty": -0.10,
    },
    "update_stats": {
        "updates_applied": 0,
        "last_update_ts": None,
        "last_training_rows": 0,
    },
    "guardrails": {
        "max_abs_weight_step": 0.05,
        "min_weight": -1.0,
        "max_weight": 1.0,
        "sum_normalize_positive_block": False,
        "research_only": True,
        "drift_guardrails": {
            "concept_drift_trigger_score": 0.58,
            "concept_drift_critical_score": 0.75,
            "max_decaying_edge_ratio": 0.45,
            "max_average_edge_decay_score": 0.55,
            "min_average_fill_quality_score": 0.50,
            "min_average_time_to_edge_score": 0.45,
            "max_step_scale_under_drift": 0.50,
            "max_drift_down_weight_step": 0.03,
        },
    },
}


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return dict(DEFAULT_STATE)
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def parse_args():
    p = argparse.ArgumentParser(description="Manage online learning state")
    p.add_argument("--state-json", required=True)
    p.add_argument("--init", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    path = Path(args.state_json)

    if args.init and not path.exists():
        save_state(path, dict(DEFAULT_STATE))
    else:
        state = load_state(path)
        save_state(path, state)

    print(path)


if __name__ == "__main__":
    main()
