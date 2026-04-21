"""Tests for ExperimentalQNTKUCBLane."""
from __future__ import annotations

import pytest
from src.research.experimental_qntk_ucb_lane import ExperimentalQNTKUCBLane


def _candidates(n, base_score=0.5):
    return [
        {"symbol": f"SYM{i}", "preopt_feature_score": base_score + i * 0.05,
         "volatility_penalty": 0.2 + i * 0.02, "event_score": 0.1 * i}
        for i in range(n)
    ]


def test_empty_candidates():
    lane = ExperimentalQNTKUCBLane()
    result = lane.run([])
    assert result["candidate_count"] == 0
    assert result["selected_candidates"] == []
    assert result["diversification_score"] == 0.0


def test_kernel_matrix_shape():
    lane = ExperimentalQNTKUCBLane()
    cands = _candidates(5)
    km = lane.compute_kernel_matrix(cands)
    assert len(km) == 5
    assert len(km[0]) == 5


def test_kernel_diagonal_is_one():
    lane = ExperimentalQNTKUCBLane()
    km = lane.compute_kernel_matrix(_candidates(4))
    for i in range(4):
        assert abs(float(km[i][i]) - 1.0) < 1e-6


def test_ucb_selects_diverse():
    lane = ExperimentalQNTKUCBLane(config={"alpha": 2.0})
    cands = _candidates(6)
    km = lane.compute_kernel_matrix(cands)
    visits = {"SYM0": 100, "SYM1": 100, "SYM2": 0, "SYM3": 0, "SYM4": 0, "SYM5": 0}
    selected = lane.ucb_select(cands, km, visits)
    top_symbols = [s["symbol"] for s in selected[:3]]
    assert any(s in top_symbols for s in ["SYM2", "SYM3", "SYM4", "SYM5"])


def test_diversification_score_range():
    lane = ExperimentalQNTKUCBLane()
    cands = _candidates(8)
    km = lane.compute_kernel_matrix(cands)
    selected = lane.ucb_select(cands, km, {})
    div = lane.diversification_score(selected, lane.compute_kernel_matrix(selected))
    assert 0.0 <= div <= 1.0


def test_run_pipeline_produces_result():
    lane = ExperimentalQNTKUCBLane()
    result = lane.run(_candidates(10), visit_counts={"SYM0": 5, "SYM1": 3})
    assert result["schema_version"] == "experimental_qntk_ucb.v1"
    assert result["candidate_count"] == 10
    assert result["selected_count"] <= 20
    assert len(result["selected_candidates"]) > 0
    assert "diversification_score" in result


def test_research_only_flags():
    lane = ExperimentalQNTKUCBLane()
    result = lane.run(_candidates(5))
    assert result["not_for_direct_execution"] is True
    assert result["research_only"] is True


def test_config_overrides():
    lane = ExperimentalQNTKUCBLane(config={"alpha": 0.5, "kernel_bandwidth": 1.0, "max_candidates": 3})
    assert lane.alpha == 0.5
    assert lane.kernel_bandwidth == 1.0
    assert lane.max_candidates == 3
    result = lane.run(_candidates(10))
    assert result["selected_count"] <= 3


def test_ucb_score_present():
    lane = ExperimentalQNTKUCBLane()
    cands = _candidates(5)
    km = lane.compute_kernel_matrix(cands)
    selected = lane.ucb_select(cands, km, {})
    for s in selected:
        assert "ucb_score" in s
        assert "_ucb_components" in s


def test_single_candidate():
    lane = ExperimentalQNTKUCBLane()
    result = lane.run(_candidates(1))
    assert result["selected_count"] == 1
    assert result["diversification_score"] == 0.0
