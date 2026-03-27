#!/usr/bin/env python3
"""Alert formatting helpers for canary readiness and rollback evidence."""
from __future__ import annotations

from typing import Any, Dict, List


def _format_blocker_list(blockers: List[Dict[str, Any]]) -> str:
    if not blockers:
        return "None"
    parts = []
    for item in blockers[:5]:
        category = item.get("category", "unknown")
        reason = item.get("reason", "unspecified")
        parts.append(f"{category}: {reason}")
    return "; ".join(parts)


def format_canary_readiness_alert(report: Dict[str, Any]) -> Dict[str, Any]:
    """Format a GO/NO-GO canary readiness alert payload."""
    status = str(report.get("readiness_status", "NO_GO")).upper()
    blockers = list(report.get("blockers", []))
    summary = report.get("summary", {}) or {}
    title = f"Canary Readiness {status}"
    message = (
        f"Replay confidence={summary.get('replay_confidence', 'unknown')}, "
        f"persistence confidence={summary.get('persistence_confidence', 'unknown')}, "
        f"rollback confidence={summary.get('rollback_confidence', 'unknown')}. "
        f"Blockers: {_format_blocker_list(blockers)}"
    )
    return {
        "alert_type": "canary_readiness_review",
        "severity": "info" if status == "GO" else "warning",
        "title": title,
        "message": message,
        "details": {
            "readiness_status": status,
            "top_blocker": report.get("top_blocker"),
            "blocker_count": len(blockers),
            "evidence_artifacts": report.get("evidence_artifacts", {}),
        },
    }


def format_rollback_evidence_alert(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """Format an alert payload summarizing rollback evidence state."""
    proven = bool(telemetry.get("rollback_path_proven"))
    title = "Rollback Evidence Proven" if proven else "Rollback Evidence Missing"
    message = (
        f"Encoder versions={telemetry.get('encoder_version_count', 0)}, "
        f"learning-state versions={telemetry.get('learning_state_version_count', 0)}, "
        f"rollback path present={telemetry.get('rollback_path_present', False)}, "
        f"rollback proven={proven}."
    )
    return {
        "alert_type": "rollback_evidence",
        "severity": "info" if proven else "warning",
        "title": title,
        "message": message,
        "details": telemetry,
    }
