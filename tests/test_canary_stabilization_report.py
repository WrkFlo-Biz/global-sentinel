"""Tests for canary stabilization reporting."""
from __future__ import annotations

import json
from pathlib import Path

from src.reports.canary_stabilization_report import CanaryStabilizationReportBuilder


def _write_canary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_builds_stabilization_report_with_failure_trends(tmp_path: Path):
    repo_root = tmp_path
    canary_dir = repo_root / "reports" / "research" / "canary"

    _write_canary(
        canary_dir / "canary_001.json",
        {
            "schema_version": "evidence_only_canary_artifact.v1",
            "generated_at": "2026-03-07T20:00:00+00:00",
            "session_context": {"session": "regular"},
            "rollback_recommended": True,
            "promotion_allowed_if_not_canary": False,
            "reason": "failed: min_trade_count, policy_check",
            "gate_results": [
                {"gate": "min_trade_count", "passed": False},
                {"gate": "policy_check", "passed": False},
            ],
            "eval_metrics": {
                "trade_count": 5,
                "eval_days": 1,
                "blocked_rate": 0.0,
                "degraded_rate": 0.8,
                "runtime_degraded_driver": True,
            },
        },
    )
    _write_canary(
        canary_dir / "canary_002.json",
        {
            "schema_version": "evidence_only_canary_artifact.v1",
            "generated_at": "2026-03-07T21:00:00+00:00",
            "session_context": {"session": "overnight"},
            "rollback_recommended": True,
            "promotion_allowed_if_not_canary": False,
            "reason": "failed: min_eval_days, max_failure_rate",
            "gate_results": [
                {"gate": "min_eval_days", "passed": False},
                {"gate": "max_failure_rate", "passed": False},
            ],
            "canary_vs_baseline_divergence": {
                "failure_rate": {"delta": 0.2},
                "trade_count": {"delta": -2.0},
            },
            "eval_metrics": {
                "trade_count": 8,
                "eval_days": 2,
                "failure_rate": 0.3,
                "drawdown_delta_bps": 70.0,
                "blocked_rate": 0.1,
                "degraded_rate": 0.2,
                "runtime_degraded_driver": False,
            },
            "_lineage": {"config_fingerprints": ["fp-one"]},
        },
    )

    report = CanaryStabilizationReportBuilder(repo_root).build_report(limit=10)

    assert report["schema_version"] == "canary_stabilization_report.v1"
    assert report["gate_failure_trends"]["min_trade_count"] == 1
    assert report["gate_failure_trends"]["min_eval_days"] == 1
    assert report["session_analysis"]["regular"]["artifact_count"] == 1
    assert report["session_analysis"]["overnight"]["artifact_count"] == 1
    assert report["failure_category_breakdown"]["insufficient_evidence_maturity"] == 2
    assert report["failure_category_multi"]["policy_gated_failure"] == 1
    assert report["failure_category_multi"]["degraded_scorecard_runtime_quality_issue"] == 1
    assert report["failure_category_multi"]["market_session_liquidity_issue"] == 1
    assert report["summary"]["dominant_failure_category"] == "insufficient_evidence_maturity"
    assert report["trend_summary"]["trade_count"]["direction"] == "up"
    assert report["trend_summary"]["eval_days"]["direction"] == "up"
    assert report["divergence_summary"]["states"]["regression"] == 1
    assert report["session_analysis"]["overnight"]["liquidity_sensitive_ratio"] == 1.0
    assert report["session_analysis"]["overnight"]["session_constraints"]["limit_only"] is True
    assert report["config_fingerprint_state"]["values"]["fp-one"] == 1
    assert report["persistence_confirmation"]["persistence_mode"] in {"blob_primary", "local_fallback"}
    assert len(report["recommendations"]) >= 1
