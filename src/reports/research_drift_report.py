#!/usr/bin/env python3
"""Compare current vs previous online-learning state and report actionable drift."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class ResearchDriftReport:
    def _thresholds(self, current: Dict[str, Any]) -> Dict[str, float]:
        guardrails = (current.get("guardrails") or {}).get("drift_guardrails") or {}
        return {
            "concept_drift_trigger_score": _safe_float(
                guardrails.get("concept_drift_trigger_score"), 0.58
            ),
            "concept_drift_critical_score": _safe_float(
                guardrails.get("concept_drift_critical_score"), 0.75
            ),
            "max_decaying_edge_ratio": _safe_float(
                guardrails.get("max_decaying_edge_ratio"), 0.45
            ),
            "max_average_edge_decay_score": _safe_float(
                guardrails.get("max_average_edge_decay_score"), 0.55
            ),
            "min_average_fill_quality_score": _safe_float(
                guardrails.get("min_average_fill_quality_score"), 0.50
            ),
            "min_average_time_to_edge_score": _safe_float(
                guardrails.get("min_average_time_to_edge_score"), 0.45
            ),
        }

    def _build_drift_signals(
        self,
        *,
        current_stats: Dict[str, Any],
        drift_monitor: Dict[str, Any],
        thresholds: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        monitor_signal_map: Dict[str, Dict[str, Any]] = {}
        for row in drift_monitor.get("signals") or []:
            if isinstance(row, dict) and row.get("name"):
                monitor_signal_map[str(row["name"])] = row

        def _monitor_signal_value(name: str, default: float) -> float:
            row = monitor_signal_map.get(name) or {}
            return _safe_float(row.get("value"), default)

        avg_decay = _safe_float(
            current_stats.get("last_average_edge_decay_score"),
            _monitor_signal_value("avg_edge_decay_score", 0.0),
        )
        decaying_ratio = _safe_float(
            current_stats.get("last_decaying_edge_ratio"),
            _monitor_signal_value("decaying_edge_ratio", 0.0),
        )
        avg_fill = _safe_float(
            current_stats.get("last_average_fill_quality_score"),
            _monitor_signal_value("avg_fill_quality_score", 0.7),
        )
        avg_time = _safe_float(
            current_stats.get("last_average_time_to_edge_score"),
            _monitor_signal_value("avg_time_to_edge_score", 0.5),
        )
        concept_score = _safe_float(
            drift_monitor.get("concept_drift_score"),
            current_stats.get("last_concept_drift_score"),
        )

        return [
            {
                "name": "avg_edge_decay_score",
                "value": round(avg_decay, 4),
                "threshold": thresholds["max_average_edge_decay_score"],
                "comparison": "<=",
                "breached": avg_decay >= thresholds["max_average_edge_decay_score"],
            },
            {
                "name": "decaying_edge_ratio",
                "value": round(decaying_ratio, 4),
                "threshold": thresholds["max_decaying_edge_ratio"],
                "comparison": "<=",
                "breached": decaying_ratio >= thresholds["max_decaying_edge_ratio"],
            },
            {
                "name": "avg_fill_quality_score",
                "value": round(avg_fill, 4),
                "threshold": thresholds["min_average_fill_quality_score"],
                "comparison": ">=",
                "breached": avg_fill <= thresholds["min_average_fill_quality_score"],
            },
            {
                "name": "avg_time_to_edge_score",
                "value": round(avg_time, 4),
                "threshold": thresholds["min_average_time_to_edge_score"],
                "comparison": ">=",
                "breached": avg_time <= thresholds["min_average_time_to_edge_score"],
            },
            {
                "name": "concept_drift_score",
                "value": round(concept_score, 4),
                "threshold": thresholds["concept_drift_trigger_score"],
                "comparison": "<=",
                "breached": concept_score >= thresholds["concept_drift_trigger_score"],
            },
        ]

    def build(self, previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
        prev_w = previous.get("weights") or {}
        curr_w = current.get("weights") or {}
        prev_stats = previous.get("update_stats") or {}
        curr_stats = current.get("update_stats") or {}
        drift_monitor = current.get("drift_monitor") or {}
        thresholds = self._thresholds(current)

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
                "abs_delta": abs(d),
            }
            max_abs_drift = max(max_abs_drift, abs(d))

        top_weight_drifts = sorted(
            (
                {
                    "weight": key,
                    "previous": row["previous"],
                    "current": row["current"],
                    "delta": row["delta"],
                    "abs_delta": row["abs_delta"],
                }
                for key, row in diffs.items()
            ),
            key=lambda item: item["abs_delta"],
            reverse=True,
        )[:5]

        drift_signals = self._build_drift_signals(
            current_stats=curr_stats,
            drift_monitor=drift_monitor,
            thresholds=thresholds,
        )
        breached = [row["name"] for row in drift_signals if row.get("breached")]

        concept_drift_score = _safe_float(
            drift_monitor.get("concept_drift_score"),
            curr_stats.get("last_concept_drift_score"),
        )
        monitor_triggered = bool(
            drift_monitor.get("triggered", curr_stats.get("last_drift_triggered", False))
        )
        monitor_critical = bool(drift_monitor.get("critical", False))
        if monitor_critical or concept_drift_score >= thresholds["concept_drift_critical_score"]:
            action_state = "critical"
        elif monitor_triggered or concept_drift_score >= thresholds["concept_drift_trigger_score"] or len(breached) >= 2:
            action_state = "degrade"
        elif breached:
            action_state = "watch"
        else:
            action_state = "normal"

        recommended_actions: List[str] = []
        if action_state in {"degrade", "critical"}:
            recommended_actions.append("Apply automatic down-weighting and reduce learning-rate for next cycle.")
            recommended_actions.append("Require >=2 additional walk-forward folds before promotion decisions.")
        if "avg_fill_quality_score" in breached:
            recommended_actions.append("Increase execution-quality filters and tighten fill/slippage constraints.")
        if "avg_time_to_edge_score" in breached:
            recommended_actions.append("Reduce horizon-sensitive features and prioritize faster edge-capture regimes.")
        if "decaying_edge_ratio" in breached:
            recommended_actions.append("Down-rank regime-fragile signals and require stronger event confirmation.")
        if not recommended_actions:
            recommended_actions.append("No action required; monitor drift metrics.")

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
            "top_weight_drifts": top_weight_drifts,
            "previous_updates_applied": prev_stats.get("updates_applied"),
            "current_updates_applied": curr_stats.get("updates_applied"),
            "drift_thresholds": thresholds,
            "drift_signals": drift_signals,
            "breached_signals": breached,
            "concept_drift": {
                "score": round(concept_drift_score, 4),
                "trigger_score": thresholds["concept_drift_trigger_score"],
                "critical_score": thresholds["concept_drift_critical_score"],
                "severity": action_state,
            },
            "actionability": {
                "state": action_state,
                "auto_down_weighting_applied": monitor_triggered,
                "down_weighting_multiplier": _safe_float(
                    drift_monitor.get(
                        "down_weighting_multiplier",
                        curr_stats.get("last_down_weighting_multiplier"),
                    ),
                    1.0,
                ),
                "pipeline_flags": {
                    "concept_drift_triggered": monitor_triggered,
                    "concept_drift_critical": monitor_critical,
                    "breach_count": len(breached),
                },
                "recommended_actions": recommended_actions,
            },
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
    p.add_argument("--output-md", default="reports/research/research_drift_report.md")
    return p.parse_args()


def main():
    args = parse_args()

    previous = load_json(Path(args.previous_state_json))
    current = load_json(Path(args.current_state_json))
    report = ResearchDriftReport().build(previous, current)

    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = ["# Research Drift Report", ""]
    lines.append(f"- Max absolute drift: **{report['max_abs_drift']:.6f}**")
    lines.append(f"- Decay-adjusted drift: **{report['decay_adjusted_drift']:.6f}**")
    lines.append(f"- Drift state: **{report['actionability']['state']}**")
    lines.append(
        f"- Auto down-weighting applied: `{report['actionability']['auto_down_weighting_applied']}`"
    )
    lines.append(
        f"- Down-weighting multiplier: `{report['actionability']['down_weighting_multiplier']}`"
    )
    lines.append(f"- Previous updates applied: {report['previous_updates_applied']}")
    lines.append(f"- Current updates applied: {report['current_updates_applied']}")
    lines.append("")
    lines.append("## Recommended Actions")
    for action in report["actionability"]["recommended_actions"]:
        lines.append(f"- {action}")
    lines.append("")
    lines.append("## Breached Signals")
    if report["breached_signals"]:
        for signal in report["breached_signals"]:
            lines.append(f"- {signal}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("| Weight | Previous | Current | Delta |")
    lines.append("|---|---:|---:|---:|")
    for k, row in report["weight_diffs"].items():
        lines.append(
            f"| {k} | {row['previous']:.4f} | {row['current']:.4f} | {row['delta']:.4f} |"
        )

    out_md = Path(args.output_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(out_json)
    print(out_md)


if __name__ == "__main__":
    main()
