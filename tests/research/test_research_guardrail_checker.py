"""Tests for research guardrail checker."""
from src.research.research_guardrail_checker import ResearchGuardrailChecker


def test_valid_research_score():
    checker = ResearchGuardrailChecker()
    result = checker.check_research_score({
        "research_score": 0.72,
        "not_for_direct_execution": True,
        "schema_version": "research_score.v1",
        "confidence": 0.85,
    })
    assert result.passed


def test_score_out_of_range():
    checker = ResearchGuardrailChecker()
    result = checker.check_research_score({
        "research_score": 1.5,
        "not_for_direct_execution": True,
        "schema_version": "v1",
        "confidence": 0.5,
    })
    assert not result.passed
    assert any(c["name"] == "score_in_range" and not c["passed"] for c in result.checks)


def test_missing_execution_flag():
    checker = ResearchGuardrailChecker()
    result = checker.check_research_score({
        "research_score": 0.5,
        "schema_version": "v1",
        "confidence": 0.5,
    })
    assert not result.passed


def test_quantum_flag_required():
    checker = ResearchGuardrailChecker()
    result = checker.check_research_score({
        "research_score": 0.5,
        "not_for_direct_execution": True,
        "quantum_sourced": True,
        "schema_version": "v1",
        "confidence": 0.5,
    })
    assert not result.passed
    assert any("quantum" in c["name"] for c in result.checks if not c["passed"])


def test_weight_step_too_large():
    checker = ResearchGuardrailChecker()
    result = checker.check_weight_update(
        current_weights={"base_score": 0.35},
        proposed_weights={"base_score": 0.50},
        learning_state={"update_stats": {"updates_applied": 100}},
    )
    assert not result.passed
    assert any(c["name"] == "max_weight_step" and not c["passed"] for c in result.checks)


def test_weight_update_valid():
    checker = ResearchGuardrailChecker()
    result = checker.check_weight_update(
        current_weights={"base_score": 0.35},
        proposed_weights={"base_score": 0.37},
        learning_state={"update_stats": {"updates_applied": 100}},
    )
    assert result.passed


def test_nan_weight_rejected():
    checker = ResearchGuardrailChecker()
    result = checker.check_weight_update(
        current_weights={"x": 0.5},
        proposed_weights={"x": float("nan")},
        learning_state={"update_stats": {"updates_applied": 100}},
    )
    assert not result.passed


def test_training_dataset_valid():
    checker = ResearchGuardrailChecker()
    result = checker.check_training_dataset({
        "rows": [
            {"symbol": "XOM", "realized_return_bps": 50},
            {"symbol": "AAPL", "realized_return_bps": -10},
        ]
    })
    assert result.passed


def test_training_dataset_empty():
    checker = ResearchGuardrailChecker()
    result = checker.check_training_dataset({"rows": []})
    assert not result.passed


def test_guardrail_result_to_dict():
    checker = ResearchGuardrailChecker()
    result = checker.check_research_score({
        "research_score": 0.5,
        "not_for_direct_execution": True,
        "schema_version": "v1",
        "confidence": 0.5,
    })
    d = result.to_dict()
    assert "passed" in d
    assert "checks" in d
    assert "checker_version" in d
