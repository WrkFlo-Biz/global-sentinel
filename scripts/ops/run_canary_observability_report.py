#!/usr/bin/env python3
"""Generate canary observability artifacts for the stabilization window."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitoring.operational_alerts import OperationalAlerts
from src.reports.canary_observability_report import CanaryObservabilityReportBuilder


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canary observability artifacts")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-json")
    parser.add_argument("--emit-alert-events", action="store_true")
    return parser.parse_args()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _emit_canary_alerts(repo_root: Path, report: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    alerter = OperationalAlerts(repo_root)
    summary = report.get("summary", {}) or {}
    fingerprint_state = report.get("config_fingerprint_state", {}) or {}
    divergence = report.get("baseline_divergence", {}) or {}

    if int(summary.get("rollback_recommended_count", 0)) > 0:
        alert = {
            "alert_type": "canary_rollback_recommended",
            "severity": "warning",
            "title": "Canary Rollback Recommended",
            "message": (
                f"Canary report has {summary.get('rollback_recommended_count', 0)} "
                "rollback-recommended observations."
            ),
            "timestamp": _utc_now_iso(),
            "details": {
                "rollback_recommended_count": summary.get("rollback_recommended_count", 0),
                "report_schema_version": report.get("schema_version"),
            },
        }
        alerter._emit(alert)
        alerts.append(alert)

    if int(divergence.get("regression_count", 0)) > 0:
        alert = {
            "alert_type": "canary_baseline_regression",
            "severity": "warning",
            "title": "Canary Baseline Regression",
            "message": (
                f"Canary report detected {divergence.get('regression_count', 0)} "
                "baseline regression observations."
            ),
            "timestamp": _utc_now_iso(),
            "details": divergence,
        }
        alerter._emit(alert)
        alerts.append(alert)

    if not bool(fingerprint_state.get("consistent", True)):
        alert = {
            "alert_type": "canary_config_fingerprint_mismatch",
            "severity": "warning",
            "title": "Canary Config Fingerprint Mismatch",
            "message": "Canary observability report detected inconsistent config fingerprints.",
            "timestamp": _utc_now_iso(),
            "details": fingerprint_state,
        }
        alerter._emit(alert)
        alerts.append(alert)

    return alerts


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = (
        Path(args.output_json).resolve()
        if args.output_json
        else repo_root / "reports" / "operational" / "canary_observability_report.json"
    )

    builder = CanaryObservabilityReportBuilder(repo_root)
    report = builder.build_report(
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        limit=args.limit,
    )

    alerts: List[Dict[str, Any]] = []
    if args.emit_alert_events:
        alerts = _emit_canary_alerts(repo_root, report)

    report["emitted_alerts"] = [item.get("alert_type", "") for item in alerts]
    _write_json(output_path, report)
    print(output_path)


if __name__ == "__main__":
    main()
