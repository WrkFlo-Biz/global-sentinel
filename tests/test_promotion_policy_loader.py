"""Tests for src/core/promotion_policy_loader.py"""
import pytest
from pathlib import Path
from src.core.promotion_policy_loader import (
    load_promotion_policy, PromotionPolicy, SignalThresholds,
    CanaryPolicy, RollbackPolicy,
)


def test_load_from_real_config():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    assert policy.schema_version == "promotion_policy.v1"
    assert "CRISIS" in policy.frozen_modes
    assert "MANUAL_REVIEW" in policy.frozen_modes
    assert policy.human_approval_required is True


def test_default_thresholds():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    default = policy.get_thresholds("default")
    assert default.min_eval_days == 60
    assert default.min_trade_count == 100
    assert default.max_failure_rate == 0.05


def test_politician_alpha_blocked():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    assert policy.is_promotion_blocked("politician_alpha") is True
    t = policy.get_thresholds("politician_alpha")
    assert t.promotion_blocked is True
    assert t.blocked_reason == "political_disclosure_research_only"


def test_quantum_thresholds_stricter():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    default = policy.get_thresholds("default")
    quantum = policy.get_thresholds("quantum_portfolio_optimizer")
    assert quantum.min_eval_days > default.min_eval_days
    assert quantum.min_trade_count > default.min_trade_count
    assert quantum.max_failure_rate < default.max_failure_rate


def test_frozen_mode_check():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    assert policy.is_mode_frozen("CRISIS") is True
    assert policy.is_mode_frozen("NORMAL") is False
    assert policy.is_mode_frozen("ELEVATED") is False


def test_canary_policy():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    assert policy.canary_policy.confidence_level == 0.95
    assert policy.canary_policy.min_sample_size == 50


def test_rollback_policy():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    assert policy.rollback_policy.max_versions_retained == 10
    assert policy.rollback_policy.auto_rollback_on_drift is True


def test_unknown_signal_falls_back_to_default():
    policy = load_promotion_policy(Path("config/promotion_policy.yaml"))
    t = policy.get_thresholds("nonexistent_signal")
    default = policy.get_thresholds("default")
    assert t.min_eval_days == default.min_eval_days


def test_missing_file_returns_defaults():
    policy = load_promotion_policy(Path("/tmp/nonexistent_policy.yaml"))
    assert isinstance(policy, PromotionPolicy)
    assert "default" in policy.signal_thresholds
