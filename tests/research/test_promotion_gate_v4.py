"""Tests for encoder_promotion_gate V4 features — YAML integration, frozen modes, blocked signals."""
import pytest
from pathlib import Path
from src.research.encoder_promotion_gate import EncoderPromotionGate, PromotionDecision


@pytest.fixture
def gate():
    return EncoderPromotionGate(config_path=Path("config/promotion_policy.yaml"))


PASSING_METRICS = {
    "eval_days": 120,
    "trade_count": 300,
    "drawdown_delta_bps": 20,
    "slippage_adjusted_win_delta_bps": 25,
    "failure_rate": 0.01,
    "cumulative_drift_std": 0.5,
}


def test_yaml_loaded(gate):
    assert gate._policy is not None
    assert gate._policy.schema_version == "promotion_policy.v1"


def test_frozen_mode_blocks_promotion(gate):
    result = gate.evaluate(PASSING_METRICS, current_mode="CRISIS")
    assert result.allowed is False
    assert "mode_frozen" in result.reason


def test_manual_review_blocks_promotion(gate):
    result = gate.evaluate(PASSING_METRICS, current_mode="MANUAL_REVIEW")
    assert result.allowed is False


def test_politician_alpha_blocked(gate):
    result = gate.evaluate(PASSING_METRICS, signal_type="politician_alpha")
    assert result.allowed is False
    assert "promotion_blocked" in result.reason
    assert "political_disclosure" in result.reason


def test_normal_mode_allows_passing(gate):
    result = gate.evaluate(PASSING_METRICS, current_mode="NORMAL")
    assert result.allowed is True


def test_quantum_stricter_thresholds(gate):
    """Quantum requires min_eval_days=90, min_trade_count=200."""
    borderline = dict(PASSING_METRICS)
    borderline["eval_days"] = 70  # passes default (60) but fails quantum (90)
    borderline["trade_count"] = 150  # passes default (100) but fails quantum (200)
    
    result_default = gate.evaluate(borderline, signal_type="default")
    assert result_default.allowed is True
    
    result_quantum = gate.evaluate(borderline, signal_type="quantum_portfolio_optimizer")
    assert result_quantum.allowed is False


def test_signal_type_in_decision(gate):
    result = gate.evaluate(PASSING_METRICS, signal_type="classical_baseline")
    assert result.signal_type == "classical_baseline"


def test_unknown_signal_uses_default(gate):
    result = gate.evaluate(PASSING_METRICS, signal_type="some_new_signal")
    assert result.allowed is True  # passes default thresholds


def test_backward_compat_no_config():
    """Gate works without config file using legacy defaults."""
    gate = EncoderPromotionGate(config_path=Path("/tmp/nonexistent.yaml"))
    result = gate.evaluate(PASSING_METRICS)
    assert result.allowed is True


# --- Canary evaluation tests ---

def test_canary_evidence_only_flag(gate):
    result = gate.evaluate_canary(PASSING_METRICS)
    assert result["canary_evidence_only"] is True
    assert result["promotion_allowed_if_not_canary"] is True


def test_canary_never_promotes(gate):
    """Canary result always has canary_evidence_only=True even when gates pass."""
    result = gate.evaluate_canary(PASSING_METRICS)
    assert result["canary_evidence_only"] is True
    assert "gate_results" in result


def test_canary_failing_metrics(gate):
    failing = dict(PASSING_METRICS)
    failing["eval_days"] = 5
    result = gate.evaluate_canary(failing)
    assert result["canary_evidence_only"] is True
    assert result["promotion_allowed_if_not_canary"] is False
    assert result["rollback_recommended"] is True


def test_canary_baseline_divergence(gate):
    baseline = {"eval_days": 100, "trade_count": 250, "failure_rate": 0.02}
    canary = {"eval_days": 120, "trade_count": 300, "failure_rate": 0.01,
              "drawdown_delta_bps": 20, "slippage_adjusted_win_delta_bps": 25,
              "cumulative_drift_std": 0.5}
    result = gate.evaluate_canary(canary, baseline_metrics=baseline)
    div = result["canary_vs_baseline_divergence"]
    assert "eval_days" in div
    assert div["eval_days"]["delta"] == 20
    assert div["trade_count"]["delta"] == 50


def test_canary_frozen_mode(gate):
    result = gate.evaluate_canary(PASSING_METRICS, current_mode="CRISIS")
    assert result["canary_evidence_only"] is True
    assert result["promotion_allowed_if_not_canary"] is False
    assert "mode_frozen" in result["reason"]


def test_canary_politician_alpha_blocked(gate):
    result = gate.evaluate_canary(PASSING_METRICS, signal_type="politician_alpha")
    assert result["canary_evidence_only"] is True
    assert result["promotion_allowed_if_not_canary"] is False
