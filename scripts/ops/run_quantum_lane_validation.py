#!/usr/bin/env python3
"""Run the bounded quantum lane validation report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.reports.quantum_lane_validation_report import QuantumLaneValidationReportBuilder  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Global Sentinel quantum lane validation")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--output-json")
    parser.add_argument("--execute-qcloud", action="store_true")
    parser.add_argument("--execute-pilot", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    builder = QuantumLaneValidationReportBuilder(repo_root)
    report = builder.build(
        execute_qcloud=args.execute_qcloud,
        execute_pilot=args.execute_pilot,
    )

    output_path = Path(args.output_json).resolve() if args.output_json else (
        repo_root / "reports" / "operational" / "quantum_lane_validation_report.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    summary = {
        "schema_version": "quantum_lane_validation_run.v1",
        "report_path": str(output_path),
        "recommendation": report.get("recommendation", {}).get("next_action"),
        "cpuqvm_status": report.get("backend_validation", {}).get("cpuqvm", {}).get("status"),
        "qcloud_status": report.get("backend_validation", {}).get("qcloud", {}).get("status"),
        "pilot_status": report.get("backend_validation", {}).get("pilot", {}).get("status"),
        "blocker_count": len(report.get("blockers", [])),
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
