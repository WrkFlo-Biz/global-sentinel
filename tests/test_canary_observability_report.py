"""Tests for canary observability report scaffolding."""
from __future__ import annotations

import json
from pathlib import Path

from src.reports.canary_observability_report import CanaryObservabilityReportBuilder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_builds_canary_observability_summary(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    research_dir = repo_root / "reports" / "research"

    _write_json(
        research_dir / "candidate_canary_gate.json",
        {
            "timestamp": "2026-03-07T20:00:00+00:00",
            "signal_type": "online_weighted_encoder",
            "current_mode": "NORMAL",
            "canary_evidence_only": True,
            "promotion_allowed_if_not_canary": False,
            "rollback_recommended": True,
            "reason": "failure_rate exceeded",
            "canary_vs_baseline_divergence": {
                "failure_rate": {
                    "canary": 0.08,
                    "baseline": 0.02,
                    "delta": 0.06,
                    "delta_pct": 300.0,
                },
                "trade_count": {
                    "canary": 140.0,
                    "baseline": 150.0,
                    "delta": -10.0,
                    "delta_pct": -6.666667,
                },
            },
            "gate_results": [
                {"gate": "max_failure_rate", "passed": False},
                {"gate": "policy_check", "passed": False},
            ],
            "eval_metrics": {
                "trade_count": 140,
                "eval_days": 3,
                "failure_rate": 0.08,
                "degraded_rate": 0.3,
                "degraded_count": 2,
                "blocked_count": 1,
            },
            "session_context": {
                "session": "overnight",
                "constraints": {"limit_only": True, "allowed_time_in_force": ["day"]},
            },
            "config_fingerprint": "fp-1",
            "metadata": {"persistence_mode": "blob_primary"},
        },
    )
    _write_json(
        research_dir / "canary_comparison.json",
        {
            "schema_version": "canary_comparison.v1",
            "timestamp_utc": "2026-03-07T20:05:00+00:00",
            "metrics": {
                "mean_score_delta": 0.012,
                "correlation": 0.94,
                "win_rate_delta": 0.02,
                "sharpe_delta": 0.03,
            },
            "promotion_recommendation": "promote",
            "reason": "candidate improves on all key metrics",
            "eval_metrics": {"trade_count": 150, "eval_days": 4, "failure_rate": 0.02},
            "_lineage": {"config_fingerprint": "fp-1"},
        },
    )

    monkeypatch.setattr(
        "src.reports.canary_observability_report.BlobPersistenceHealthChecker.check",
        lambda self: type("BlobHealth", (), {"to_dict": lambda _self: {
            "status": "healthy",
            "persistence_mode": "blob_primary",
            "blob_available": True,
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.RollbackTelemetryCollector.collect",
        lambda self: type("Rollback", (), {"to_dict": lambda _self: {
            "rollback_path_present": True,
            "rollback_path_proven": True,
            "encoder_version_count": 2,
            "learning_state_version_count": 2,
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.DecisionReplayRunner.verify_config_consistency",
        lambda self, limit=100: {"consistent": True, "fingerprints": ["fp-1"]},
    )

    report = CanaryObservabilityReportBuilder(repo_root).build_report(limit=10)

    assert report["schema_version"] == "canary_observability_report.v1"
    assert report["summary"]["total_canary_artifacts"] == 2
    assert report["summary"]["rollback_recommended_count"] == 1
    assert report["summary"]["baseline_regression_count"] == 1
    assert report["summary"]["config_fingerprint_consistent"] is True
    assert report["summary"]["dominant_failure_category"] in {"no_material_failure", "policy_gated_failure"}
    assert report["rollback_trigger_reasons"]["failure_rate exceeded"] == 1
    assert report["baseline_divergence"]["states"]["regression"] == 1
    assert report["baseline_divergence"]["states"]["improvement"] == 1
    assert report["failure_category_breakdown"]["policy_gated_failure"] == 1
    assert report["session_breakdown"]["overnight"]["liquidity_contribution_ratio"] == 1.0
    assert report["persistence_confirmation"]["all_blob_primary"] is True
    assert report["summary"]["overnight_condition_count"] == 1
    assert report["trend_summary"]["trade_count"]["direction"] == "up"
    assert report["trend_window_summary"]["trade_count"]["direction"] == "up"
    overnight_observation = next(
        observation for observation in report["observations"] if observation["market_session"] == "overnight"
    )
    assert overnight_observation["dominant_failure_category"] == "policy_gated_failure"
    assert overnight_observation["degraded_scorecard_contribution"] is True
    assert overnight_observation["degraded_scorecard_flag"] is True
    assert overnight_observation["overnight_condition_flag"] is True
    assert overnight_observation["session_constraints"]["limit_only"] is True


def test_excludes_readiness_report_and_non_canary_files(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    operational_dir = repo_root / "reports" / "operational"

    _write_json(
        operational_dir / "canary_readiness_report.json",
        {
            "schema_version": "canary_readiness_report.v1",
            "readiness_status": "GO",
        },
    )
    _write_json(
        operational_dir / "boring_report.json",
        {
            "schema_version": "decision_audit_report.v1",
            "summary": {},
        },
    )
    _write_json(
        operational_dir / "real_canary.json",
        {
            "timestamp": "2026-03-07T20:15:00+00:00",
            "canary_evidence_only": True,
            "promotion_allowed_if_not_canary": True,
            "rollback_recommended": False,
            "reason": "all gates passed",
            "eval_metrics": {"trade_count": 12, "eval_days": 2, "failure_rate": 0.0},
            "config_fingerprint": "fp-2",
        },
    )

    monkeypatch.setattr(
        "src.reports.canary_observability_report.BlobPersistenceHealthChecker.check",
        lambda self: type("BlobHealth", (), {"to_dict": lambda _self: {
            "status": "healthy",
            "persistence_mode": "blob_primary",
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.RollbackTelemetryCollector.collect",
        lambda self: type("Rollback", (), {"to_dict": lambda _self: {
            "rollback_path_present": True,
            "rollback_path_proven": False,
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.DecisionReplayRunner.verify_config_consistency",
        lambda self, limit=100: {"consistent": True, "fingerprints": ["fp-2"]},
    )

    report = CanaryObservabilityReportBuilder(repo_root).build_report(limit=10)

    assert report["summary"]["total_canary_artifacts"] == 1
    assert report["observations"][0]["artifact_path"].endswith("real_canary.json")
    assert report["observations"][0]["dominant_failure_category"] == "no_material_failure"


def test_flags_config_fingerprint_mismatch(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    research_dir = repo_root / "reports" / "research"

    _write_json(
        research_dir / "canary_a.json",
        {
            "timestamp": "2026-03-07T20:20:00+00:00",
            "canary_evidence_only": True,
            "promotion_allowed_if_not_canary": True,
            "rollback_recommended": False,
            "reason": "stable",
            "eval_metrics": {"trade_count": 5, "eval_days": 1, "failure_rate": 0.0},
            "config_fingerprint": "fp-a",
        },
    )
    _write_json(
        research_dir / "canary_b.json",
        {
            "timestamp": "2026-03-07T20:21:00+00:00",
            "canary_evidence_only": True,
            "promotion_allowed_if_not_canary": True,
            "rollback_recommended": False,
            "reason": "stable",
            "eval_metrics": {"trade_count": 7, "eval_days": 2, "failure_rate": 0.0},
            "_lineage": {"config_fingerprint": "fp-b"},
        },
    )

    monkeypatch.setattr(
        "src.reports.canary_observability_report.BlobPersistenceHealthChecker.check",
        lambda self: type("BlobHealth", (), {"to_dict": lambda _self: {
            "status": "healthy",
            "persistence_mode": "blob_primary",
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.RollbackTelemetryCollector.collect",
        lambda self: type("Rollback", (), {"to_dict": lambda _self: {
            "rollback_path_present": True,
            "rollback_path_proven": True,
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.DecisionReplayRunner.verify_config_consistency",
        lambda self, limit=100: {"consistent": False, "fingerprints": ["fp-a", "fp-b"]},
    )

    report = CanaryObservabilityReportBuilder(repo_root).build_report(limit=10)

    assert report["summary"]["config_fingerprint_consistent"] is False
    assert report["config_fingerprint_state"]["values"]["fp-a"] == 1
    assert report["config_fingerprint_state"]["values"]["fp-b"] == 1
    assert report["trend_summary"]["trade_count"]["direction"] == "up"


def test_backfills_market_session_for_older_canary_artifacts(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    research_dir = repo_root / "reports" / "research"

    _write_json(
        research_dir / "legacy_canary.json",
        {
            "timestamp": "2026-03-09T01:30:00+00:00",
            "canary_evidence_only": True,
            "promotion_allowed_if_not_canary": False,
            "rollback_recommended": True,
            "reason": "legacy artifact without session context",
            "eval_metrics": {"trade_count": 1, "eval_days": 1, "failure_rate": 0.9},
            "config_fingerprint": "fp-session",
        },
    )

    monkeypatch.setattr(
        "src.reports.canary_observability_report.BlobPersistenceHealthChecker.check",
        lambda self: type("BlobHealth", (), {"to_dict": lambda _self: {
            "status": "healthy",
            "persistence_mode": "blob_primary",
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.RollbackTelemetryCollector.collect",
        lambda self: type("Rollback", (), {"to_dict": lambda _self: {
            "rollback_path_present": True,
            "rollback_path_proven": True,
        }})(),
    )
    monkeypatch.setattr(
        "src.reports.canary_observability_report.DecisionReplayRunner.verify_config_consistency",
        lambda self, limit=100: {"consistent": True, "fingerprints": ["fp-session"]},
    )

    report = CanaryObservabilityReportBuilder(repo_root).build_report(limit=10)

    assert report["summary"]["market_sessions"]["overnight"] == 1
    assert report["observations"][0]["market_session"] == "overnight"
    assert report["observations"][0]["overnight_condition_flag"] is True
    assert report["observations"][0]["dominant_failure_category"] == "mixed_or_unclassified"
