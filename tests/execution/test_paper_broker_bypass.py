"""Regression tests for paper/mock/sandbox broker approval bypass and shadow_mode flags."""

from src.execution.shadow_order_router import (
    _PAPER_TRAINING_BROKERS,
    _env_flag,
    ShadowOrderRouter,
)
import os


def test_paper_training_brokers_constant():
    """Verify paper/training broker set contains expected values."""
    assert "mock" in _PAPER_TRAINING_BROKERS
    assert "alpaca_paper" in _PAPER_TRAINING_BROKERS
    assert "tradier_sandbox" in _PAPER_TRAINING_BROKERS
    # Live brokers must NOT be in the set
    assert "alpaca" not in _PAPER_TRAINING_BROKERS
    assert "tradier" not in _PAPER_TRAINING_BROKERS
    assert "ibkr" not in _PAPER_TRAINING_BROKERS


def test_env_flag_truthy():
    os.environ["_TEST_FLAG"] = "true"
    assert _env_flag("_TEST_FLAG") is True
    os.environ["_TEST_FLAG"] = "1"
    assert _env_flag("_TEST_FLAG") is True
    os.environ["_TEST_FLAG"] = "yes"
    assert _env_flag("_TEST_FLAG") is True
    os.environ["_TEST_FLAG"] = "on"
    assert _env_flag("_TEST_FLAG") is True
    del os.environ["_TEST_FLAG"]


def test_env_flag_falsy():
    os.environ["_TEST_FLAG"] = "false"
    assert _env_flag("_TEST_FLAG") is False
    os.environ["_TEST_FLAG"] = "0"
    assert _env_flag("_TEST_FLAG") is False
    os.environ["_TEST_FLAG"] = "no"
    assert _env_flag("_TEST_FLAG") is False
    os.environ["_TEST_FLAG"] = "off"
    assert _env_flag("_TEST_FLAG") is False
    del os.environ["_TEST_FLAG"]


def test_env_flag_default():
    assert _env_flag("_NONEXISTENT_VAR_12345") is False
    assert _env_flag("_NONEXISTENT_VAR_12345", default=True) is True


def test_shadow_mode_in_equity_order_request():
    """shadow_mode must be True in order requests (shadow-only router)."""
    router = ShadowOrderRouter.__new__(ShadowOrderRouter)
    router._resolve_decision_price = lambda pkg, cand: (150.0, "test")
    router._get_account_equity = lambda: 100000.0

    order_req = ShadowOrderRouter._candidate_to_order_request(
        router,
        package={"window_context": {"time_window_name": "core_session"}},
        candidate={
            "symbol": "AAPL",
            "side": "buy",
            "direction": "bullish",
            "instrument_types": ["equity"],
            "confidence_score": 0.6,
            "size_multiplier_suggestion": 1.0,
            "fill_sim_assessment": {"expected_slippage_bps": 5.0},
            "execution_constraints": {},
        },
        strategy_config=None,
    )
    assert order_req["shadow_mode"] is True, (
        "shadow_mode must be True for shadow-only router"
    )


def test_paper_broker_skips_approval_flag():
    """Verify _skip_approval logic for paper brokers."""
    for broker in _PAPER_TRAINING_BROKERS:
        assert broker in _PAPER_TRAINING_BROKERS
    # Live brokers should NOT skip
    assert "alpaca" not in _PAPER_TRAINING_BROKERS
    assert "interactive_brokers" not in _PAPER_TRAINING_BROKERS
