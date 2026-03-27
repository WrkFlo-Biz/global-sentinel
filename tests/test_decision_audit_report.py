"""Tests for DecisionAuditReportBuilder."""
import json
import pytest
from pathlib import Path
from src.reports.decision_audit_report import DecisionAuditReportBuilder


def _make_scorecard(cycle, blocked=False, penalty=0.0, fingerprint="fp1"):
    mdt = {
        "pre_transition_mode": "NORMAL",
        "proposed_mode": "ELEVATED" if blocked else "NORMAL",
        "final_mode": "NORMAL",
        "blocked": blocked,
        "blocking_reason": "policy_engine_denied" if blocked else None,
        "regime_shift_probability": 0.6,
        "policy_evaluation": {"allowed": False} if blocked else None,
        "quorum_evaluation": None,
    }
    return {
        "schema_version": "scorecard.v6",
        "timestamp_utc": f"2026-03-07T{10+cycle:02d}:00:00+00:00",
        "cycle": cycle,
        "mode": "NORMAL",
        "regime_shift_probability": 0.3,
        "confidence": 0.8 * (1 - penalty),
        "mode_decision_trace": mdt,
        "feature_freshness": {"total": 5},
        "freshness_penalty": penalty,
        "original_confidence": 0.8 if penalty else None,
        "config_fingerprint": fingerprint,
        "degraded_mode": penalty > 0,
    }


@pytest.fixture
def audit_dir(tmp_path):
    repo = tmp_path / "repo"
    sc_dir = repo / "logs" / "scorecards"
    sc_dir.mkdir(parents=True)
    ev_dir = repo / "logs" / "events"
    ev_dir.mkdir(parents=True)
    return repo


def _write(repo, sc, idx):
    d = repo / "logs" / "scorecards"
    (d / f"scorecard_{idx:04d}.json").write_text(json.dumps(sc))


def test_empty_report(tmp_path):
    builder = DecisionAuditReportBuilder(tmp_path / "nope")
    report = builder.build_report()
    assert report["summary"]["total_cycles"] == 0


def test_blocked_escalations(audit_dir):
    _write(audit_dir, _make_scorecard(1, blocked=True), 0)
    _write(audit_dir, _make_scorecard(2, blocked=False), 1)
    _write(audit_dir, _make_scorecard(3, blocked=True), 2)
    builder = DecisionAuditReportBuilder(audit_dir)
    report = builder.build_report()
    assert report["summary"]["blocked_escalations"] == 2
    assert report["blocking_reasons"]["policy_engine_denied"] == 2


def test_freshness_degradations(audit_dir):
    _write(audit_dir, _make_scorecard(1, penalty=0.2), 0)
    _write(audit_dir, _make_scorecard(2, penalty=0.0), 1)
    builder = DecisionAuditReportBuilder(audit_dir)
    report = builder.build_report()
    assert report["summary"]["freshness_degradations"] == 1
    assert report["freshness_degradations"][0]["freshness_penalty"] == pytest.approx(0.2)


def test_config_drift(audit_dir):
    _write(audit_dir, _make_scorecard(1, fingerprint="fp_a"), 0)
    _write(audit_dir, _make_scorecard(2, fingerprint="fp_b"), 1)
    _write(audit_dir, _make_scorecard(3, fingerprint="fp_b"), 2)
    builder = DecisionAuditReportBuilder(audit_dir)
    report = builder.build_report()
    assert report["summary"]["config_drift_events"] == 1


def test_quorum_blocks(audit_dir):
    sc = _make_scorecard(1, blocked=True)
    sc["mode_decision_trace"]["blocking_reason"] = "quorum_not_met"
    sc["mode_decision_trace"]["quorum_evaluation"] = {"quorum_met": False}
    _write(audit_dir, sc, 0)
    builder = DecisionAuditReportBuilder(audit_dir)
    report = builder.build_report()
    assert report["summary"]["quorum_blocks"] == 1


def test_report_schema(audit_dir):
    _write(audit_dir, _make_scorecard(1), 0)
    builder = DecisionAuditReportBuilder(audit_dir)
    report = builder.build_report()
    assert report["schema_version"] == "decision_audit_report.v1"
    assert "generated_at" in report
    assert "period" in report
