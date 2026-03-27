"""Tests for research score writer bounds and guardrails."""

from src.research.research_score_writer import build_research_score


def test_research_score_writer_bounds():
    evaluation = {
        "request_id": "req-1",
        "package_id": "pkg-1",
        "quantum_overlap_score": 0.8,
        "quantum_directional_score": 0.7,
        "classical_overlap_score": 0.4,
        "classical_directional_score": 0.4,
        "quantum_realized_return_bps_sum": 220.0,
        "classical_realized_return_bps_sum": 100.0,
    }

    score = build_research_score(evaluation)

    assert score["request_id"] == "req-1"
    assert score["package_id"] == "pkg-1"
    assert 0.0 <= score["research_score"] <= 1.0
    assert score["guardrails"]["not_for_direct_execution"] is True
    assert score["guardrails"]["bounded_secondary_signal_only"] is True


def test_research_score_positive_influence():
    evaluation = {
        "request_id": "req-2",
        "quantum_overlap_score": 0.9,
        "quantum_directional_score": 0.9,
        "classical_overlap_score": 0.3,
        "classical_directional_score": 0.3,
        "quantum_realized_return_bps_sum": 500.0,
        "classical_realized_return_bps_sum": 50.0,
    }
    score = build_research_score(evaluation)
    assert score["recommended_influence"] == "research_positive"


def test_research_score_negative_influence():
    evaluation = {
        "request_id": "req-3",
        "quantum_overlap_score": 0.1,
        "quantum_directional_score": 0.1,
        "classical_overlap_score": 0.8,
        "classical_directional_score": 0.8,
        "quantum_realized_return_bps_sum": -200.0,
        "classical_realized_return_bps_sum": 300.0,
    }
    score = build_research_score(evaluation)
    assert score["recommended_influence"] == "research_negative"
