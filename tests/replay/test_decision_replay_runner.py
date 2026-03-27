"""Tests for DecisionReplayRunner."""
import json
import pytest
from pathlib import Path
from src.replay.decision_replay_runner import DecisionReplayRunner, REPLAY_REQUIRED_FIELDS


def _make_v6_scorecard(cycle=1, blocked=False, penalty=0.0, fingerprint="abc123"):
    """Build a replay-grade v6 scorecard."""
    mdt = {
        "pre_transition_mode": "NORMAL",
        "proposed_mode": "ELEVATED" if blocked else "NORMAL",
        "final_mode": "NORMAL",
        "reason": "threshold_based",
        "regime_shift_probability": 0.6,
        "thresholds_used": {"crisis": 0.85, "elevated": 0.55, "hysteresis": 0.05},
        "policy_evaluation": {"allowed": not blocked} if blocked else None,
        "quorum_evaluation": None,
        "blocked": blocked,
        "blocking_reason": "policy_engine_denied" if blocked else None,
        "cycle": cycle,
    }
    return {
        "schema_version": "scorecard.v6",
        "timestamp_utc": f"2026-03-07T{10+cycle:02d}:00:00+00:00",
        "cycle": cycle,
        "mode": "NORMAL",
        "regime_shift_probability": 0.3,
        "confidence": 0.8 * (1 - penalty),
        "evidence": [],
        "data_freshness_status": {},
        "threshold_values_used": {"crisis": 0.85, "elevated": 0.55},
        "mode_decision_trace": mdt,
        "quorum_state": mdt.get("quorum_evaluation"),
        "policy_decision_trace": mdt.get("policy_evaluation"),
        "feature_freshness": {"total_features": 5, "fresh_count": 5, "max_confidence_penalty": penalty},
        "freshness_penalty": penalty,
        "original_confidence": 0.8 if penalty else None,
        "config_fingerprint": fingerprint,
        "config_versions": {},
        "degraded_mode": penalty > 0,
        "risk_gate_status": "active",
        "manual_veto_status": False,
        "kill_switch_status": False,
        "fallback_mode_status": False,
        "shadow_execution_eligible": True,
    }


@pytest.fixture
def replay_dir(tmp_path):
    repo = tmp_path / "repo"
    sc_dir = repo / "logs" / "scorecards"
    sc_dir.mkdir(parents=True)
    ev_dir = repo / "logs" / "events"
    ev_dir.mkdir(parents=True)
    return repo


def _write_scorecard(repo, sc, idx=0):
    sc_dir = repo / "logs" / "scorecards"
    fname = f"scorecard_{idx:04d}.json"
    (sc_dir / fname).write_text(json.dumps(sc))


def test_replay_grade_scorecard(replay_dir):
    sc = _make_v6_scorecard(cycle=1)
    _write_scorecard(replay_dir, sc, 1)
    runner = DecisionReplayRunner(replay_dir)
    result = runner.replay_scorecard(replay_dir / "logs" / "scorecards" / "scorecard_0001.json")
    assert result.replay_grade is True
    assert result.has_mode_decision_trace is True
    assert result.has_freshness_state is True
    assert result.has_config_fingerprint is True
    assert result.missing_fields == []


def test_old_schema_not_replay_grade(replay_dir):
    sc = {"schema_version": "scorecard.v3", "cycle": 1, "mode": "NORMAL"}
    _write_scorecard(replay_dir, sc, 1)
    runner = DecisionReplayRunner(replay_dir)
    result = runner.replay_scorecard(replay_dir / "logs" / "scorecards" / "scorecard_0001.json")
    assert result.replay_grade is False
    assert len(result.missing_fields) > 0


def test_blocked_escalation_detected(replay_dir):
    sc = _make_v6_scorecard(cycle=1, blocked=True)
    _write_scorecard(replay_dir, sc, 1)
    runner = DecisionReplayRunner(replay_dir)
    result = runner.replay_scorecard(replay_dir / "logs" / "scorecards" / "scorecard_0001.json")
    assert result.mode_blocked is True
    assert result.blocking_reason == "policy_engine_denied"


def test_freshness_penalty_recorded(replay_dir):
    sc = _make_v6_scorecard(cycle=1, penalty=0.3)
    _write_scorecard(replay_dir, sc, 1)
    runner = DecisionReplayRunner(replay_dir)
    result = runner.replay_scorecard(replay_dir / "logs" / "scorecards" / "scorecard_0001.json")
    assert result.freshness_penalty == pytest.approx(0.3)
    assert result.degraded_mode is True
    assert result.original_confidence == pytest.approx(0.8)


def test_replay_range(replay_dir):
    for i in range(5):
        sc = _make_v6_scorecard(cycle=i + 1, penalty=0.1 if i == 2 else 0.0)
        _write_scorecard(replay_dir, sc, i)
    runner = DecisionReplayRunner(replay_dir)
    report = runner.replay_range()
    assert report["total_scorecards"] == 5
    assert report["replay_grade_count"] == 5
    assert len(report["degraded_decisions"]) == 1


def test_config_consistency_stable(replay_dir):
    for i in range(3):
        sc = _make_v6_scorecard(cycle=i + 1, fingerprint="same_fp")
        _write_scorecard(replay_dir, sc, i)
    runner = DecisionReplayRunner(replay_dir)
    result = runner.verify_config_consistency()
    assert result["config_stable"] is True
    assert result["unique_fingerprints"] == 1
    assert len(result["drift_events"]) == 0


def test_config_drift_detected(replay_dir):
    for i in range(3):
        fp = "fp_v1" if i < 2 else "fp_v2"
        sc = _make_v6_scorecard(cycle=i + 1, fingerprint=fp)
        _write_scorecard(replay_dir, sc, i)
    runner = DecisionReplayRunner(replay_dir)
    result = runner.verify_config_consistency()
    assert result["config_stable"] is False
    assert len(result["drift_events"]) == 1


def test_empty_scorecards_dir(tmp_path):
    repo = tmp_path / "repo"
    runner = DecisionReplayRunner(repo)
    result = runner.replay_range()
    assert result["error"] == "scorecards_dir not found"


def test_replay_blocked_decisions_in_range(replay_dir):
    for i in range(4):
        sc = _make_v6_scorecard(cycle=i + 1, blocked=(i == 1 or i == 3))
        _write_scorecard(replay_dir, sc, i)
    runner = DecisionReplayRunner(replay_dir)
    report = runner.replay_range()
    assert len(report["blocked_decisions"]) == 2
