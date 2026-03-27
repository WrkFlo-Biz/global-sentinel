"""Tests for OperationalAlerts."""
import pytest
from src.monitoring.operational_alerts import OperationalAlerts


def _base_scorecard(**overrides):
    sc = {
        "schema_version": "scorecard.v6",
        "timestamp_utc": "2026-03-07T12:00:00+00:00",
        "cycle": 1,
        "mode": "NORMAL",
        "regime_shift_probability": 0.3,
        "confidence": 0.8,
        "mode_decision_trace": {
            "blocked": False,
            "blocking_reason": None,
            "proposed_mode": "NORMAL",
            "final_mode": "NORMAL",
            "regime_shift_probability": 0.3,
        },
        "feature_freshness": {"total": 5, "fresh": 5},
        "freshness_penalty": 0.0,
        "original_confidence": None,
        "config_fingerprint": "abc123",
        "degraded_mode": False,
    }
    sc.update(overrides)
    return sc


def test_no_alerts_on_clean_scorecard(tmp_path):
    oa = OperationalAlerts(tmp_path)
    alerts = oa.check_and_alert(_base_scorecard())
    assert len(alerts) == 0


def test_blocked_escalation_alert(tmp_path):
    sc = _base_scorecard()
    sc["mode_decision_trace"] = {
        "blocked": True,
        "blocking_reason": "policy_engine_denied",
        "proposed_mode": "CRISIS",
        "final_mode": "ELEVATED",
        "regime_shift_probability": 0.9,
    }
    oa = OperationalAlerts(tmp_path)
    alerts = oa.check_and_alert(sc)
    types = [a["alert_type"] for a in alerts]
    assert "blocked_escalation" in types


def test_freshness_degradation_alert(tmp_path):
    sc = _base_scorecard(freshness_penalty=0.35, original_confidence=0.8, confidence=0.52)
    oa = OperationalAlerts(tmp_path)
    alerts = oa.check_and_alert(sc)
    types = [a["alert_type"] for a in alerts]
    assert "freshness_degradation" in types
    fd = next(a for a in alerts if a["alert_type"] == "freshness_degradation")
    assert fd["severity"] == "warning"  # >= 0.3 threshold


def test_freshness_info_severity(tmp_path):
    sc = _base_scorecard(freshness_penalty=0.25, original_confidence=0.8, confidence=0.6)
    oa = OperationalAlerts(tmp_path)
    alerts = oa.check_and_alert(sc)
    fd = next(a for a in alerts if a["alert_type"] == "freshness_degradation")
    assert fd["severity"] == "info"


def test_quorum_block_alert(tmp_path):
    sc = _base_scorecard()
    sc["mode_decision_trace"] = {
        "blocked": True,
        "blocking_reason": "quorum_not_met",
        "proposed_mode": "ELEVATED",
        "final_mode": "NORMAL",
        "regime_shift_probability": 0.6,
        "quorum_evaluation": {"quorum_met": False},
    }
    oa = OperationalAlerts(tmp_path)
    alerts = oa.check_and_alert(sc)
    types = [a["alert_type"] for a in alerts]
    assert "blocked_escalation" in types
    assert "quorum_blocked_escalation" in types


def test_config_drift_alert(tmp_path):
    oa = OperationalAlerts(tmp_path)
    # First call sets baseline
    alerts1 = oa.check_and_alert(_base_scorecard(config_fingerprint="fp_v1"))
    assert not any(a["alert_type"] == "config_fingerprint_drift" for a in alerts1)
    # Second call same fingerprint — no alert
    alerts2 = oa.check_and_alert(_base_scorecard(config_fingerprint="fp_v1"))
    assert not any(a["alert_type"] == "config_fingerprint_drift" for a in alerts2)
    # Third call different fingerprint — alert
    alerts3 = oa.check_and_alert(_base_scorecard(config_fingerprint="fp_v2"))
    assert any(a["alert_type"] == "config_fingerprint_drift" for a in alerts3)


def test_degraded_mode_alert(tmp_path):
    sc = _base_scorecard(degraded_mode=True)
    sc["feature_freshness"] = {"max_confidence_penalty": 0.5}
    oa = OperationalAlerts(tmp_path)
    alerts = oa.check_and_alert(sc)
    assert any(a["alert_type"] == "degraded_mode" for a in alerts)


def test_blob_fallback_alert(tmp_path):
    oa = OperationalAlerts(tmp_path)
    alert = oa.check_blob_fallback("local_fallback", reason="connection_timeout")
    assert alert is not None
    assert alert["alert_type"] == "blob_fallback"
    assert "connection_timeout" in alert["message"]


def test_blob_fallback_no_alert_on_primary(tmp_path):
    oa = OperationalAlerts(tmp_path)
    alert = oa.check_blob_fallback("blob_primary")
    assert alert is None


def test_blocked_promotion_alert(tmp_path):
    oa = OperationalAlerts(tmp_path)
    alert = oa.check_blocked_promotion("politician_alpha", "tier_3_blocked")
    assert alert["alert_type"] == "blocked_promotion"
    assert "politician_alpha" in alert["message"]


def test_alert_persisted_to_events(tmp_path):
    repo = tmp_path / "repo"
    oa = OperationalAlerts(repo)
    oa.check_blocked_promotion("test_signal", "test_reason")
    events_dir = repo / "logs" / "events"
    assert events_dir.exists()
    files = list(events_dir.glob("alert_blocked_promotion_*.json"))
    assert len(files) == 1
