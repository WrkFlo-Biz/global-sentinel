"""Tests for canary stabilization checkpoint reporting."""
from __future__ import annotations

import json
from pathlib import Path

from src.reports.canary_stabilization_checkpoint import CanaryStabilizationCheckpointBuilder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_builds_checkpoint_with_maturity_and_session_coverage_flags(tmp_path: Path):
    repo_root = tmp_path
    operational = repo_root / "reports" / "operational"

    _write_json(operational / "canary_readiness_report.json", {"readiness_status": "GO"})
    _write_json(
        operational / "canary_stabilization_report.json",
        {
            "summary": {
                "artifact_count": 12,
                "latest_trade_count": 23,
                "latest_eval_days": 1,
                "rollback_recommended_ratio": 1.0,
                "promotion_eligible_ratio": 0.0,
                "dominant_failure_category": "insufficient_evidence_maturity",
                "market_sessions": ["closed"],
            },
            "failure_category_multi": {
                "insufficient_evidence_maturity": 12,
                "degraded_scorecard_runtime_quality_issue": 12,
                "market_session_liquidity_issue": 0,
                "true_canary_weakness": 0,
            },
            "trend_summary": {
                "trade_count": {"direction": "up", "delta": 16.0, "end": 23.0},
                "eval_days": {"direction": "stable", "delta": 0.0, "end": 1.0},
                "rollback_recommended": {"direction": "stable", "end": True},
                "failure_rate": {"direction": "stable", "delta": 0.0},
                "degraded_driver_share": 1.0,
                "session_liquidity_share": 0.0,
                "regression_emerging": True,
            },
        },
    )
    _write_json(
        operational / "canary_observability_report.json",
        {
            "summary": {
                "dominant_failure_category": "policy_gated_failure",
                "config_fingerprint_consistent": True,
                "persistence_modes": {"blob_primary": 12},
            },
            "failure_category_breakdown": {"policy_gated_failure": 12},
            "persistence_confirmation": {"all_blob_primary": True},
            "session_breakdown": {"closed": {"artifact_count": 12}},
        },
    )

    report = CanaryStabilizationCheckpointBuilder(repo_root).build()

    assert report["schema_version"] == "canary_stabilization_checkpoint.v1"
    assert report["checkpoint_status"] == "continue_stabilization_collect_session_coverage"
    assert "evidence_maturity" in report["primary_blockers"]
    assert "policy_gate_still_blocking" in report["primary_blockers"]
    assert report["evidence_quality"]["maturity_level"] == "early"
    assert report["snapshot"]["all_blob_primary"] is True
    assert report["failure_profile"]["dominant_profile"] == "mixed"
    assert report["failure_profile"]["policy_gate_overlay_present"] is True
    assert any("eval_days move above 1" in item for item in report["next_questions"])


def test_checkpoint_detects_not_ready_and_config_drift(tmp_path: Path):
    repo_root = tmp_path
    operational = repo_root / "reports" / "operational"

    _write_json(operational / "canary_readiness_report.json", {"readiness_status": "NO_GO"})
    _write_json(
        operational / "canary_stabilization_report.json",
        {
            "summary": {
                "artifact_count": 3,
                "latest_trade_count": 2,
                "latest_eval_days": 1,
                "rollback_recommended_ratio": 1.0,
                "promotion_eligible_ratio": 0.0,
                "dominant_failure_category": "insufficient_evidence_maturity",
                "market_sessions": ["closed", "overnight"],
            },
            "failure_category_multi": {
                "insufficient_evidence_maturity": 3,
                "degraded_scorecard_runtime_quality_issue": 0,
                "market_session_liquidity_issue": 1,
                "true_canary_weakness": 0,
            },
            "trend_summary": {
                "trade_count": {"direction": "up", "delta": 1.0, "end": 2.0},
                "eval_days": {"direction": "stable", "delta": 0.0, "end": 1.0},
                "rollback_recommended": {"direction": "stable", "end": True},
                "failure_rate": {"direction": "up", "delta": 0.1},
                "degraded_driver_share": 0.2,
                "session_liquidity_share": 0.5,
                "regression_emerging": False,
            },
        },
    )
    _write_json(
        operational / "canary_observability_report.json",
        {
            "summary": {
                "dominant_failure_category": "mixed_or_unclassified",
                "config_fingerprint_consistent": False,
                "persistence_modes": {"blob_primary": 3},
            },
            "persistence_confirmation": {"all_blob_primary": True},
            "session_breakdown": {"closed": {"artifact_count": 2}, "overnight": {"artifact_count": 1}},
        },
    )

    report = CanaryStabilizationCheckpointBuilder(repo_root).build()

    assert report["checkpoint_status"] == "not_ready"
    assert "config_fingerprint_inconsistency" in report["primary_blockers"]
    assert report["snapshot"]["session_coverage"] == ["closed", "overnight"]
    assert report["failure_profile"]["session_breakdown_present"] is True
