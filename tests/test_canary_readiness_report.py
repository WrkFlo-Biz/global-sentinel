"""Tests for canary readiness review scaffolding."""
from __future__ import annotations

import json
from pathlib import Path

from src.reports.canary_readiness_report import CanaryReadinessReportBuilder


def _write_scorecard(path: Path, *, cycle: int = 1, fingerprint: str = "fp-1") -> None:
    payload = {
        "schema_version": "scorecard.v6",
        "timestamp_utc": f"2026-03-07T19:0{cycle}:00+00:00",
        "cycle": cycle,
        "mode": "NORMAL",
        "regime_shift_probability": 0.41,
        "confidence": 0.72,
        "threshold_values_used": {"version": "test"},
        "mode_decision_trace": {
            "final_mode": "NORMAL",
            "blocked": False,
            "policy_evaluation": {"allowed": True},
            "quorum_evaluation": {"quorum_met": True},
        },
        "feature_freshness": {"degraded_groups": 0},
        "config_fingerprint": fingerprint,
        "freshness_penalty": 0.0,
        "original_confidence": 0.72,
        "degraded_mode": False,
        "quorum_state": {"quorum_met": True},
        "policy_decision_trace": {"allowed": True},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_canary_readiness_report_go_when_all_evidence_passes(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    scorecards_dir = repo_root / "logs" / "scorecards"
    events_dir = repo_root / "logs" / "events"
    scorecards_dir.mkdir(parents=True, exist_ok=True)
    events_dir.mkdir(parents=True, exist_ok=True)
    # Legacy scorecard should not block the current v6 schema epoch.
    (scorecards_dir / "scorecard_000.json").write_text(
        json.dumps(
            {
                "schema_version": "scorecard.v5",
                "timestamp_utc": "2026-03-07T18:59:00+00:00",
                "cycle": 0,
                "mode": "NORMAL",
                "regime_shift_probability": 0.2,
                "confidence": 0.8,
                "threshold_values_used": {"version": "legacy"},
            }
        ),
        encoding="utf-8",
    )
    _write_scorecard(scorecards_dir / "scorecard_001.json", cycle=1, fingerprint="fp-1")
    _write_scorecard(scorecards_dir / "scorecard_002.json", cycle=2, fingerprint="fp-1")

    builder = CanaryReadinessReportBuilder(repo_root)

    monkeypatch.setattr(
        "src.reports.canary_readiness_report.BlobPersistenceHealthChecker.check",
        lambda self: type("BlobHealth", (), {"to_dict": lambda _self: {
            "status": "healthy",
            "persistence_mode": "blob_primary",
            "blob_available": True,
            "fallback_reason": "",
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_readiness_report.RollbackTelemetryCollector.collect",
        lambda self: type("Rollback", (), {"to_dict": lambda _self: {
            "rollback_path_present": True,
            "rollback_path_proven": True,
            "encoder_version_count": 2,
            "learning_state_version_count": 2,
            "recent_encoder_rollbacks": [{"rollback_from": "v1"}],
            "recent_learning_state_rollbacks": [{"metadata": {"rollback_from": "x"}}],
        }})(),
    )

    report = builder.build(limit=10)

    assert report["readiness_status"] == "GO"
    assert report["blockers"] == []
    assert report["summary"]["replay_confidence"] == "high"
    assert report["supporting_evidence"]["current_schema_epoch"]["schema_version"] == "scorecard.v6"


def test_canary_readiness_report_no_go_when_replay_ratio_fails(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    scorecards_dir = repo_root / "logs" / "scorecards"
    scorecards_dir.mkdir(parents=True, exist_ok=True)

    # Missing replay-grade fields on purpose.
    (scorecards_dir / "scorecard_001.json").write_text(
        json.dumps(
            {
                "schema_version": "scorecard.v6",
                "timestamp_utc": "2026-03-07T19:00:00+00:00",
                "cycle": 1,
                "mode": "NORMAL",
                "regime_shift_probability": 0.41,
                "confidence": 0.72,
            }
        ),
        encoding="utf-8",
    )

    builder = CanaryReadinessReportBuilder(repo_root)
    monkeypatch.setattr(
        "src.reports.canary_readiness_report.BlobPersistenceHealthChecker.check",
        lambda self: type("BlobHealth", (), {"to_dict": lambda _self: {
            "status": "healthy",
            "persistence_mode": "blob_primary",
            "blob_available": True,
            "fallback_reason": "",
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_readiness_report.RollbackTelemetryCollector.collect",
        lambda self: type("Rollback", (), {"to_dict": lambda _self: {
            "rollback_path_present": True,
            "rollback_path_proven": True,
            "encoder_version_count": 2,
            "learning_state_version_count": 2,
            "recent_encoder_rollbacks": [{"rollback_from": "v1"}],
            "recent_learning_state_rollbacks": [{"metadata": {"rollback_from": "x"}}],
        }})(),
    )

    report = builder.build(limit=10)

    assert report["readiness_status"] == "NO_GO"
    assert report["top_blocker"]["category"] == "replay"
    assert any(item["criterion"] == "scorecards_replay_grade" and not item["passed"] for item in report["criteria_results"])
