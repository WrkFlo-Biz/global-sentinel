"""Tests for canary-readiness alert formatting helpers."""
from __future__ import annotations

from src.monitoring.alert_formatters import (
    format_canary_readiness_alert,
    format_rollback_evidence_alert,
)


def test_formats_no_go_canary_readiness_alert():
    payload = format_canary_readiness_alert(
        {
            "readiness_status": "NO_GO",
            "top_blocker": {"category": "replay", "reason": "replay-grade ratio below threshold"},
            "blockers": [
                {"category": "replay", "reason": "replay-grade ratio below threshold"},
                {"category": "rollback", "reason": "rollback path not proven"},
            ],
            "summary": {
                "replay_confidence": "low",
                "persistence_confidence": "high",
                "rollback_confidence": "low",
            },
            "evidence_artifacts": {"report": "/tmp/report.json"},
        }
    )

    assert payload["alert_type"] == "canary_readiness_review"
    assert payload["severity"] == "warning"
    assert "Canary Readiness NO_GO" == payload["title"]
    assert "replay" in payload["message"]


def test_formats_go_rollback_evidence_alert():
    payload = format_rollback_evidence_alert(
        {
            "encoder_version_count": 3,
            "learning_state_version_count": 2,
            "rollback_path_present": True,
            "rollback_path_proven": True,
        }
    )

    assert payload["alert_type"] == "rollback_evidence"
    assert payload["severity"] == "info"
    assert "Proven" in payload["title"]
    assert "encoder versions=3" in payload["message"].lower()
