#!/usr/bin/env python3
"""Generate replay, audit, and persistence-health artifacts for operations."""
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

from src.core.blob_persistence_health import BlobPersistenceHealthChecker
from src.monitoring.operational_alerts import OperationalAlerts
from src.replay.decision_replay_runner import DecisionReplayRunner
from src.reports.decision_audit_report import DecisionAuditReportBuilder


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build operational replay/audit artifacts")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output-dir")
    parser.add_argument("--emit-alert-events", action="store_true")
    return parser.parse_args()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else repo_root / "reports" / "operational"
    output_dir.mkdir(parents=True, exist_ok=True)

    replay_runner = DecisionReplayRunner(repo_root)
    replay_report = replay_runner.replay_range(
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        limit=args.limit,
    )
    replay_report["config_consistency"] = replay_runner.verify_config_consistency(limit=args.limit)

    audit_builder = DecisionAuditReportBuilder(repo_root)
    audit_report = audit_builder.build_report(
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        limit=args.limit,
    )

    blob_checker = BlobPersistenceHealthChecker(repo_root)
    blob_report = blob_checker.check().to_dict()

    replay_path = output_dir / "decision_replay_report.json"
    audit_path = output_dir / "decision_audit_report.json"
    blob_path = output_dir / "blob_persistence_health.json"

    _write_json(replay_path, replay_report)
    _write_json(audit_path, audit_report)
    _write_json(blob_path, blob_report)

    alerts: List[Dict[str, Any]] = []
    if args.emit_alert_events:
        alerter = OperationalAlerts(repo_root)
        blob_alert = alerter.check_blob_fallback(
            persistence_mode=str(blob_report.get("persistence_mode", "")),
            reason=str(blob_report.get("fallback_reason", "")),
        )
        if blob_alert:
            alerts.append(blob_alert)

        for event in replay_report.get("config_consistency", {}).get("drift_events", []):
            alert = {
                "alert_type": "config_fingerprint_drift",
                "severity": "warning",
                "title": "Config Fingerprint Drift",
                "message": (
                    f"Config fingerprint drift from cycle {event.get('from_cycle')} "
                    f"to {event.get('to_cycle')}"
                ),
                "timestamp": event.get("timestamp", _utc_now_iso()),
                "details": event,
            }
            alerter._emit(alert)
            alerts.append(alert)

    summary = {
        "schema_version": "operational_replay_bundle.v1",
        "generated_at": _utc_now_iso(),
        "repo_root": str(repo_root),
        "artifacts": {
            "decision_replay_report": str(replay_path),
            "decision_audit_report": str(audit_path),
            "blob_persistence_health": str(blob_path),
        },
        "replay_grade_ratio": replay_report.get("replay_grade_ratio", 0.0),
        "blocked_decision_count": len(replay_report.get("blocked_decisions", [])),
        "degraded_decision_count": len(replay_report.get("degraded_decisions", [])),
        "quorum_block_count": audit_report.get("summary", {}).get("quorum_blocks", 0),
        "blob_status": blob_report.get("status"),
        "persistence_mode": blob_report.get("persistence_mode"),
        "emitted_alerts": [alert.get("alert_type") for alert in alerts],
    }
    summary_path = output_dir / "operational_replay_bundle.json"
    _write_json(summary_path, summary)

    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
