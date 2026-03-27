#!/usr/bin/env python3
"""Compare current vs previous online-learning state and report drift.

Produces both JSON and markdown drift reports showing weight changes
across learning iterations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class ResearchDriftReport:
    def build(self, previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
        prev_w = previous.get("weights") or {}
        curr_w = current.get("weights") or {}

        keys = sorted(set(prev_w.keys()) | set(curr_w.keys()))
        diffs = {}
        max_abs_drift = 0.0

        for k in keys:
            p = float(prev_w.get(k, 0.0))
            c = float(curr_w.get(k, 0.0))
            d = c - p
            diffs[k] = {
                "previous": p,
                "current": c,
                "delta": d,
            }
            max_abs_drift = max(max_abs_drift, abs(d))

        return {
            "schema_version": "research_drift_report.v1",
            "max_abs_drift": max_abs_drift,
            "weight_diffs": diffs,
            "previous_updates_applied": (previous.get("update_stats") or {}).get("updates_applied"),
            "current_updates_applied": (current.get("update_stats") or {}).get("updates_applied"),
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
    lines.append(f"- Max absolute drift: **{report['max_abs_drift']}**")
    lines.append(f"- Previous updates applied: {report['previous_updates_applied']}")
    lines.append(f"- Current updates applied: {report['current_updates_applied']}")
    lines.append("")
    lines.append("| Weight | Previous | Current | Delta |")
    lines.append("|---|---:|---:|---:|")
    for k, row in report["weight_diffs"].items():
        lines.append(f"| {k} | {row['previous']:.4f} | {row['current']:.4f} | {row['delta']:.4f} |")

    out_md = Path(args.output_md)
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(out_json)
    print(out_md)


if __name__ == "__main__":
    main()
