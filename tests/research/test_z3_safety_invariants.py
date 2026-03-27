"""Tests for Z3SafetyInvariantChecker."""
from __future__ import annotations

import pytest
from src.research.z3_safety_invariant_checks import Z3SafetyInvariantChecker


@pytest.fixture
def checker():
    return Z3SafetyInvariantChecker()


def _safe_state(**overrides):
    """Return a system state that passes all invariants."""
    base = {
        "mode": "NORMAL",
        "quantum_influence_weight": 0.0,
        "quantum_influence_cap": 0.0,
        "pending_promotions": [],
        "pending_config_changes": [],
        "active_execution_sources": ["fed", "ecb", "maritime"],
        "position_notional": 5000.0,
        "max_notional_per_trade": 10000.0,
        "kill_switch_checked": True,
        "manual_veto_checked": True,
        "human_approval_gate": True,
        "research_artifact_in_execution": False,
    }
    base.update(overrides)
    return base


def test_all_invariants_hold_safe_state(checker):
    result = checker.verify_all(_safe_state())
    assert result["all_hold"] is True
    assert result["invariant_count"] == 7
    assert result["not_for_direct_execution"] is True
    assert result["research_only"] is True


def test_research_to_execution_without_approval(checker):
    state = _safe_state(research_artifact_in_execution=True, human_approval_gate=False)
    result = checker.verify_all(state)
    assert result["all_hold"] is False
    failed = [r for r in result["results"] if not r["holds"]]
    names = [r["invariant"] for r in failed]
    assert "no_research_to_execution_without_approval" in names


def test_research_to_execution_with_approval(checker):
    state = _safe_state(research_artifact_in_execution=True, human_approval_gate=True)
    result = checker.verify_all(state)
    assert result["all_hold"] is True


def test_quantum_influence_exceeds_cap(checker):
    state = _safe_state(quantum_influence_weight=0.15, quantum_influence_cap=0.10)
    result = checker.verify_all(state)
    assert result["all_hold"] is False
    failed = [r for r in result["results"] if not r["holds"]]
    assert any("quantum_influence" in r["invariant"] for r in failed)


def test_quantum_influence_within_cap(checker):
    state = _safe_state(quantum_influence_weight=0.05, quantum_influence_cap=0.10)
    result = checker.verify_all(state)
    assert result["all_hold"] is True


def test_promotion_insufficient_eval(checker):
    state = _safe_state(pending_promotions=[
        {"name": "encoder_v3", "eval_count": 10, "drift_score": 0.05},
    ])
    result = checker.verify_all(state)
    assert result["all_hold"] is False


def test_promotion_excessive_drift(checker):
    state = _safe_state(pending_promotions=[
        {"name": "encoder_v3", "eval_count": 100, "drift_score": 0.50},
    ])
    result = checker.verify_all(state)
    assert result["all_hold"] is False


def test_promotion_meets_requirements(checker):
    state = _safe_state(pending_promotions=[
        {"name": "encoder_v3", "eval_count": 100, "drift_score": 0.05},
    ])
    result = checker.verify_all(state)
    assert result["all_hold"] is True


def test_crisis_freezes_promotions(checker):
    state = _safe_state(mode="CRISIS", pending_promotions=[
        {"name": "encoder_v3", "eval_count": 100, "drift_score": 0.05},
    ])
    result = checker.verify_all(state)
    assert result["all_hold"] is False
    failed = [r for r in result["results"] if not r["holds"]]
    assert any("crisis_freezes" in r["invariant"] for r in failed)


def test_manual_review_freezes_config_changes(checker):
    state = _safe_state(mode="MANUAL_REVIEW", pending_config_changes=["threshold_update"])
    result = checker.verify_all(state)
    assert result["all_hold"] is False


def test_normal_mode_allows_promotions(checker):
    state = _safe_state(mode="NORMAL", pending_promotions=[
        {"name": "encoder_v3", "eval_count": 100, "drift_score": 0.05},
    ])
    result = checker.verify_all(state)
    assert result["all_hold"] is True


def test_political_disclosure_in_execution(checker):
    state = _safe_state(active_execution_sources=["fed", "congressional_disclosures"])
    result = checker.verify_all(state)
    assert result["all_hold"] is False
    failed = [r for r in result["results"] if not r["holds"]]
    assert any("political" in r["invariant"] for r in failed)


def test_political_disclosure_isolated(checker):
    state = _safe_state(active_execution_sources=["fed", "ecb", "maritime"])
    result = checker.verify_all(state)
    assert result["all_hold"] is True


def test_position_sizing_exceeded(checker):
    state = _safe_state(position_notional=15000.0, max_notional_per_trade=10000.0)
    result = checker.verify_all(state)
    assert result["all_hold"] is False


def test_kill_switch_not_checked(checker):
    state = _safe_state(kill_switch_checked=False)
    result = checker.verify_all(state)
    assert result["all_hold"] is False


def test_manual_veto_not_checked(checker):
    state = _safe_state(manual_veto_checked=False)
    result = checker.verify_all(state)
    assert result["all_hold"] is False


def test_verify_single(checker):
    state = _safe_state()
    result = checker.verify_single("kill_switch_always_checked", state)
    assert result["holds"] is True


def test_verify_single_unknown(checker):
    result = checker.verify_single("nonexistent_invariant", _safe_state())
    assert result["holds"] is False


def test_invariant_names(checker):
    names = checker.invariant_names
    assert len(names) == 7
    assert "kill_switch_always_checked" in names


def test_state_hash_deterministic(checker):
    state = _safe_state()
    r1 = checker.verify_all(state)
    r2 = checker.verify_all(state)
    assert r1["state_hash"] == r2["state_hash"]
