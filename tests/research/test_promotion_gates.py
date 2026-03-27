"""Tests for promotion governance modules."""
from src.research.encoder_promotion_gate import EncoderPromotionGate
from src.research.signal_graduation_report import SignalGraduationReport
from src.research.research_promotion_readiness_report import ResearchPromotionReadinessReport


def test_promotion_gate_all_pass():
    gate = EncoderPromotionGate(min_eval_days=30, min_trade_count=50)
    decision = gate.evaluate({
        "eval_days": 60,
        "trade_count": 100,
        "drawdown_delta_bps": 20,
        "slippage_adjusted_win_delta_bps": 15,
        "failure_rate": 0.02,
    })
    assert decision.allowed


def test_promotion_gate_insufficient_eval():
    gate = EncoderPromotionGate(min_eval_days=60)
    decision = gate.evaluate({"eval_days": 30, "trade_count": 200, "drawdown_delta_bps": 10, "slippage_adjusted_win_delta_bps": 20, "failure_rate": 0.01})
    assert not decision.allowed
    assert "min_eval_days" in decision.reason


def test_promotion_gate_with_guardrail_failure():
    gate = EncoderPromotionGate(min_eval_days=1, min_trade_count=1)
    decision = gate.evaluate(
        {"eval_days": 60, "trade_count": 100, "drawdown_delta_bps": 10, "slippage_adjusted_win_delta_bps": 20, "failure_rate": 0.01},
        guardrail_result={"passed": False},
    )
    assert not decision.allowed
    assert "guardrail" in decision.reason


def test_signal_graduation_promote():
    report = SignalGraduationReport()
    result = report.evaluate_signal(
        "test_signal",
        {"eval_days": 90, "trade_count": 200, "drawdown_delta_bps": 20, "slippage_adjusted_win_delta_bps": 25, "failure_rate": 0.02},
        criteria={"min_eval_days": 60, "min_trade_count": 100, "max_drawdown_delta_bps": 50, "min_slippage_adjusted_win_delta_bps": 10, "max_failure_rate": 0.05},
    )
    assert result["recommendation"] == "promote"


def test_signal_graduation_hold():
    report = SignalGraduationReport()
    result = report.evaluate_signal(
        "test_signal",
        {"eval_days": 20, "trade_count": 200},
        criteria={"min_eval_days": 60},
    )
    assert result["recommendation"] == "hold"


def test_readiness_report():
    report_builder = ResearchPromotionReadinessReport()
    reports = [
        {"signal_name": "sig_a", "recommendation": "promote", "criteria_results": []},
        {"signal_name": "sig_b", "recommendation": "hold", "criteria_results": [{"criterion": "min_eval_days", "passed": False}]},
    ]
    result = report_builder.build(reports)
    assert result["signals_ready_count"] == 1
    assert result["signals_blocked_count"] == 1
    assert "sig_a" in result["signals_ready"]
    assert "sig_b" in result["signals_blocked"]


def test_promotion_decision_to_dict():
    gate = EncoderPromotionGate(min_eval_days=1, min_trade_count=1)
    decision = gate.evaluate({"eval_days": 10, "trade_count": 10, "drawdown_delta_bps": 0, "slippage_adjusted_win_delta_bps": 20, "failure_rate": 0})
    d = decision.to_dict()
    assert "allowed" in d
    assert "gate_results" in d
