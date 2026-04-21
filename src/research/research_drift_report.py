#!/usr/bin/env python3
"""Compare online-learning states and summarize drift plus edge decay pressure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class ResearchDriftReport:
    def build(self, previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
        prev_w = previous.get("weights") or {}
        curr_w = current.get("weights") or {}
        prev_stats = previous.get("update_stats") or {}
        curr_stats = current.get("update_stats") or {}

        keys = sorted(set(prev_w.keys()) | set(curr_w.keys()))
        diffs = {}
        max_abs_drift = 0.0

        for k in keys:
            p = _safe_float(prev_w.get(k), 0.0)
            c = _safe_float(curr_w.get(k), 0.0)
            d = c - p
            diffs[k] = {
                "previous": p,
                "current": c,
                "delta": d,
            }
            max_abs_drift = max(max_abs_drift, abs(d))

        prev_decay = _safe_float(prev_stats.get("last_average_edge_decay_score"), 0.0)
        curr_decay = _safe_float(curr_stats.get("last_average_edge_decay_score"), 0.0)
        prev_decaying_ratio = _safe_float(prev_stats.get("last_decaying_edge_ratio"), 0.0)
        curr_decaying_ratio = _safe_float(curr_stats.get("last_decaying_edge_ratio"), 0.0)
        decay_pressure = max(0.0, curr_decay - prev_decay)
        decaying_edge_pressure = max(0.0, curr_decaying_ratio - prev_decaying_ratio)
        decay_adjusted_drift = max_abs_drift * (1.0 + decay_pressure + (decaying_edge_pressure * 0.5))

        return {
            "schema_version": "research_drift_report.v2",
            "max_abs_drift": round(max_abs_drift, 6),
            "decay_adjusted_drift": round(decay_adjusted_drift, 6),
            "weight_diffs": diffs,
            "previous_updates_applied": prev_stats.get("updates_applied"),
            "current_updates_applied": curr_stats.get("updates_applied"),
            "edge_decay_summary": {
                "previous_average_edge_decay_score": round(prev_decay, 4),
                "current_average_edge_decay_score": round(curr_decay, 4),
                "edge_decay_pressure": round(decay_pressure, 4),
                "previous_decaying_edge_ratio": round(prev_decaying_ratio, 4),
                "current_decaying_edge_ratio": round(curr_decaying_ratio, 4),
                "decaying_edge_pressure": round(decaying_edge_pressure, 4),
                "current_average_fill_quality_score": curr_stats.get("last_average_fill_quality_score"),
                "current_average_time_to_edge_score": curr_stats.get("last_average_time_to_edge_score"),
            },
        }


def parse_args():
    p = argparse.ArgumentParser(description="Build research drift report")
    p.add_argument("--previous-state-json", required=True)
    p.add_argument("--current-state-json", required=True)
    p.add_argument("--output-json", default="reports/research/research_drift_report.json")
    return p.parse_args()


def main():
    args = parse_args()
    previous = load_json(Path(args.previous_state_json))
    current = load_json(Path(args.current_state_json))

    report = ResearchDriftReport().build(previous, current)
    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(out_json)


if __name__ == "__main__":
    main()
