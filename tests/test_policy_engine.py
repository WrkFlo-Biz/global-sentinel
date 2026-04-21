"""Tests for centralized policy engine."""
import pytest
from pathlib import Path
from src.core.policy_engine import PolicyEngine, PolicyDecision


@pytest.fixture
def engine(tmp_path):
    # Create minimal config files
    import yaml

    trust = {
        "tiers": {
            "tier_1_official": {"weight": 1.0, "sources": ["fed", "fred"]},
            "tier_2_operational": {"weight": 0.8, "sources": ["gdelt", "sentiment"]},
            "tier_3_research": {"weight": 0.5, "sources": ["congressional_disclosures"]},
            "tier_4_experimental": {"weight": 0.2, "sources": ["rng_anomaly"]},
        },
        "rules": {"execution_block_tier_4": True},
    }
    quantum = {
        "maturity_stages": {
            "stage_1": {"active": True},
            "stage_2": {"active": False},
            "stage_3": {"active": False, "max_influence_weight": 0.15},
        }
    }
    execution = {"current_mode": "NORMAL"}
    sanctions = {"blocked_symbols": ["BLOCKED_CO"]}
    policy_cfg = {
        "max_single_order_notional_pct": 0.12,
        "max_abs_weight_step": 0.05,
        "min_eval_count_for_promotion": 50,
    }

    for name, data in [
        ("data_trust_hierarchy.yaml", trust),
        ("quantum_lane_policy.yaml", quantum),
        ("execution_mode.yaml", execution),
        ("sanctions_policies.yaml", sanctions),
        ("policy_engine_config.yaml", policy_cfg),
    ]:
        (tmp_path / name).write_text(yaml.dump(data), encoding="utf-8")

    # Create empty files for other configs
    for name in [
        "incident_mode_policy.yaml", "intraday_timing_guardrails.yaml",
        "options_rollout.yaml", "order_ttl_policy.yaml",
        "venue_policies.yaml", "paper_trading_graduation.yaml",
    ]:
        (tmp_path / name).write_text("{}", encoding="utf-8")

    return PolicyEngine(config_dir=tmp_path)


def test_trade_idea_tier4_blocked(engine):
    idea = {"source": "rng_anomaly", "symbol": "AAPL"}
    decision = engine.evaluate_trade_idea(idea)
    assert not decision.allowed
    assert "tier_4" in decision.reason


def test_trade_idea_tier1_allowed(engine):
    idea = {"source": "fed", "symbol": "SPY"}
    decision = engine.evaluate_trade_idea(idea)
    assert decision.allowed


def test_trade_idea_sanctions_blocked(engine):
    idea = {"source": "fed", "symbol": "BLOCKED_CO"}
    decision = engine.evaluate_trade_idea(idea)
    assert not decision.allowed
    assert "sanctions" in decision.reason


def test_trade_idea_crisis_mode(engine):
    engine._execution_mode["current_mode"] = "CRISIS"
    idea = {"source": "fed", "symbol": "SPY"}
    decision = engine.evaluate_trade_idea(idea)
    assert not decision.allowed
    assert "CRISIS" in decision.reason


def test_quantum_influence_cap_stage1(engine):
    idea = {"source": "fed", "symbol": "SPY", "quantum_influence_weight": 0.05}
    decision = engine.evaluate_trade_idea(idea)
    assert not decision.allowed
    assert "quantum_influence" in decision.reason


def test_research_score_valid(engine):
    score = {"research_score": 0.72, "not_for_direct_execution": True, "bounded_secondary_signal_only": True}
    decision = engine.evaluate_research_score_attachment(score)
    assert decision.allowed


def test_research_score_out_of_range(engine):
    score = {"research_score": 1.5, "not_for_direct_execution": True}
    decision = engine.evaluate_research_score_attachment(score)
    assert not decision.allowed


def test_weight_promotion_step_too_large(engine):
    current = {"base_score": 0.35, "event_score": 0.20}
    proposed = {"base_score": 0.50, "event_score": 0.20}  # delta 0.15 > 0.05
    decision = engine.evaluate_weight_promotion(current, proposed, {"eval_count": 100})
    assert not decision.allowed
    assert "delta" in decision.reason


def test_weight_promotion_low_eval_count(engine):
    current = {"base_score": 0.35}
    proposed = {"base_score": 0.36}
    decision = engine.evaluate_weight_promotion(current, proposed, {"eval_count": 10})
    assert not decision.allowed
    assert "eval_count" in decision.reason


def test_weight_promotion_allowed(engine):
    current = {"base_score": 0.35}
    proposed = {"base_score": 0.37}
    decision = engine.evaluate_weight_promotion(current, proposed, {"eval_count": 100})
    assert decision.allowed


def test_mode_transition_skip_blocked(engine):
    decision = engine.evaluate_mode_transition("NORMAL", "CRISIS", {})
    assert not decision.allowed
    assert "skip" in decision.reason


def test_mode_transition_valid(engine):
    decision = engine.evaluate_mode_transition("NORMAL", "ELEVATED", {})
    assert decision.allowed


def test_mode_transition_manual_review_always_valid(engine):
    decision = engine.evaluate_mode_transition("NORMAL", "MANUAL_REVIEW", {})
    assert decision.allowed


def test_audit_log_populated(engine):
    engine.evaluate_trade_idea({"source": "fed", "symbol": "SPY"})
    assert len(engine.audit_log) == 1
    assert engine.audit_log[0]["eval_type"] == "trade_idea"


def test_congressional_disclosure_allowed_for_execution(engine):
    """Congressional disclosures are tier_3 - allowed for personal use."""
    idea = {"source": "congressional_disclosures", "symbol": "NVDA"}
    decision = engine.evaluate_trade_idea(idea)
    assert decision.allowed
