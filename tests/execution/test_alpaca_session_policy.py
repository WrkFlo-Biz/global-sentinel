"""Tests for Alpaca session policy helpers."""
from __future__ import annotations

from src.execution.alpaca_session_policy import AlpacaSessionPolicy


def test_blocks_overnight_order_when_not_tradable():
    policy = AlpacaSessionPolicy()
    decision = policy.evaluate_equity_order(
        symbol="AAPL",
        order={"type": "limit", "time_in_force": "day"},
        asset_metadata={"overnight_tradable": False, "overnight_halted": False},
        timestamp_utc="2026-03-09T01:30:00+00:00",
    ).to_dict()
    assert decision["allowed"] is False
    assert "overnight_tradable_required" in decision["checks_failed"]


def test_blocks_overnight_market_order():
    policy = AlpacaSessionPolicy()
    decision = policy.evaluate_equity_order(
        symbol="SPY",
        order={"type": "market", "time_in_force": "day"},
        asset_metadata={"overnight_tradable": True, "overnight_halted": False},
        timestamp_utc="2026-03-09T01:30:00+00:00",
    ).to_dict()
    assert decision["allowed"] is False
    assert "overnight_limit_only" in decision["checks_failed"]


def test_allows_regular_session_order():
    policy = AlpacaSessionPolicy()
    decision = policy.evaluate_equity_order(
        symbol="SPY",
        order={"type": "market", "time_in_force": "day"},
        asset_metadata={"overnight_tradable": True, "overnight_halted": False},
        timestamp_utc="2026-03-09T15:00:00+00:00",
    ).to_dict()
    assert decision["allowed"] is True
    assert "regular_session_detected" in decision["checks_passed"]


def test_regular_session_reports_intraday_phase():
    policy = AlpacaSessionPolicy()
    decision = policy.evaluate_equity_order(
        symbol="QQQ",
        order={"type": "limit", "time_in_force": "day"},
        asset_metadata={"overnight_tradable": True, "overnight_halted": False},
        timestamp_utc="2026-03-09T17:00:00+00:00",
    ).to_dict()
    assert decision["allowed"] is True
    assert decision["session_context"]["intraday_phase"] == "midday"
    assert "midday_window_detected" in decision["checks_passed"]
