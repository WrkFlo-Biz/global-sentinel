"""Tests for QuantumDecompositionPolicy."""
from __future__ import annotations

import pytest
from src.research.quantum_decomposition_policy import QuantumDecompositionPolicy


def _candidates(n, base_score=0.5):
    return [{"symbol": f"SYM{i}", "preopt_feature_score": base_score + i * 0.01} for i in range(n)]


def test_prune_keeps_top_candidates():
    policy = QuantumDecompositionPolicy(config={"classical_prune_keep_ratio": 0.5})
    result = policy.preprocess(_candidates(10), {}, {})
    assert result["pruned_candidate_count"] == 5


def test_size_reduction_enforced():
    policy = QuantumDecompositionPolicy(config={"max_candidates": 8, "classical_prune_keep_ratio": 1.0})
    result = policy.preprocess(_candidates(20), {}, {})
    assert result["pruned_candidate_count"] == 8
    steps = {s["step"]: s for s in result["preprocessing_steps"]}
    assert steps["size_reduction"]["reduction_applied"] is True


def test_quantum_recommended_for_valid_problem():
    policy = QuantumDecompositionPolicy(config={"max_qubits": 20, "min_candidates_for_quantum": 4})
    result = policy.preprocess(_candidates(8), {}, {})
    assert result["recommendation"] == "quantum"
    assert result["formulation_valid"] is True


def test_classical_fallback_too_few_candidates():
    policy = QuantumDecompositionPolicy(config={"min_candidates_for_quantum": 5})
    result = policy.preprocess(_candidates(3), {}, {})
    assert result["recommendation"] == "classical_fallback"
    assert result["formulation_valid"] is False


def test_classical_fallback_exceeds_qubits():
    policy = QuantumDecompositionPolicy(config={"max_qubits": 5, "classical_prune_keep_ratio": 1.0})
    result = policy.preprocess(_candidates(10), {}, {})
    assert result["recommendation"] == "classical_fallback"
    assert result["formulation_valid"] is False


def test_problem_sizing_correct():
    policy = QuantumDecompositionPolicy()
    result = policy.preprocess(_candidates(10), {}, {})
    sizing = result["problem_sizing"]
    assert sizing["candidate_count"] <= 10
    assert sizing["qubits_needed"] == sizing["candidate_count"]
    assert sizing["qaoa_layers"] >= 1


def test_empty_candidates():
    policy = QuantumDecompositionPolicy()
    result = policy.preprocess([], {}, {})
    assert result["pruned_candidate_count"] == 0
    assert result["recommendation"] == "classical_fallback"


def test_schema_version_present():
    policy = QuantumDecompositionPolicy()
    result = policy.preprocess(_candidates(6), {}, {})
    assert result["schema_version"] == "decomposition_policy.v1"
    assert result["not_for_direct_execution"] is True


def test_shot_budget_from_config():
    policy = QuantumDecompositionPolicy(config={"shot_budget": 2048})
    result = policy.preprocess(_candidates(6), {}, {})
    assert result["shot_budget"] == 2048


def test_preprocessing_steps_logged():
    policy = QuantumDecompositionPolicy()
    result = policy.preprocess(_candidates(10), {}, {})
    step_names = [s["step"] for s in result["preprocessing_steps"]]
    assert "classical_prune" in step_names
    assert "size_reduction" in step_names
    assert "problem_sizing" in step_names
    assert "formulation_validation" in step_names
