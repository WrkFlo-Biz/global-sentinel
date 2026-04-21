#!/usr/bin/env python3
"""Run a formal GO/NO-GO canary-readiness review."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitoring.alert_formatters import (  # noqa: E402
    format_canary_readiness_alert,
    format_rollback_evidence_alert,
)
from src.reports.canary_readiness_report import CanaryReadinessReportBuilder  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Global Sentinel canary-readiness review")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output-json")
    parser.add_argument("--emit-alert-events", action="store_true")
    return parser.parse_args()


def _persist_alert(repo_root: Path, payload: dict) -> Path:
    events_dir = repo_root / "logs" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_iso().replace(":", "-").replace("+", "p")
    out = events_dir / f"alert_{payload.get('alert_type', 'unknown')}_{ts}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    builder = CanaryReadinessReportBuilder(repo_root)
    report = builder.build(limit=args.limit)

    output_path = Path(args.output_json).resolve() if args.output_json else (
        repo_root / "reports" / "operational" / "canary_readiness_report.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    emitted = []
    if args.emit_alert_events:
        readiness_alert = format_canary_readiness_alert(report)
        rollback_alert = format_rollback_evidence_alert(
            report.get("supporting_evidence", {}).get("rollback_telemetry", {})
        )
        emitted.append(str(_persist_alert(repo_root, readiness_alert)))
        emitted.append(str(_persist_alert(repo_root, rollback_alert)))

    summary = {
        "schema_version": "canary_readiness_review_run.v1",
        "generated_at": _utc_now_iso(),
        "readiness_status": report.get("readiness_status"),
        "top_blocker": report.get("top_blocker"),
        "report_path": str(output_path),
        "emitted_alerts": emitted,
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
