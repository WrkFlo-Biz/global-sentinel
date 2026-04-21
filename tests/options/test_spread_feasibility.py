"""Tests for ``src.options.spread_feasibility_checker``."""
from src.options.spread_feasibility_checker import SpreadFeasibilityChecker


def test_vertical_spread_is_feasible():
    checker = SpreadFeasibilityChecker(max_combined_spread_pct=0.20)
    result = checker.check(
        [
            {"side": "buy", "bid": 2.0, "ask": 2.2, "strike": 500, "expiry": "2026-04-17", "contract_type": "call"},
            {"side": "sell", "bid": 0.9, "ask": 1.0, "strike": 510, "expiry": "2026-04-17", "contract_type": "call"},
        ]
    )
    assert result["pass"] is True
    assert result["reason"] == "spread_feasible"
    assert result["details"]["net_premium"] > 0


def test_missing_leg_fails():
    checker = SpreadFeasibilityChecker()
    result = checker.check(
        [
            {"side": "buy", "bid": 1.0, "ask": 1.2, "strike": 100, "expiry": "2026-04-17", "contract_type": "call"},
            {"side": "sell", "bid": 0.8, "ask": 0.9, "strike": 105, "contract_type": "call"},
        ]
    )
    assert result["pass"] is False
    assert result["reason"] == "legs_unavailable"


def test_combined_spread_too_wide_fails():
    checker = SpreadFeasibilityChecker(max_combined_spread_pct=0.05)
    result = checker.check(
        [
            {"side": "buy", "bid": 1.0, "ask": 2.0, "strike": 100, "expiry": "2026-04-17", "contract_type": "call"},
            {"side": "sell", "bid": 0.5, "ask": 1.5, "strike": 105, "expiry": "2026-04-17", "contract_type": "call"},
        ]
    )
    assert result["pass"] is False
    assert result["reason"] == "combined_spread_too_wide"


def test_no_legs_fails():
    checker = SpreadFeasibilityChecker()
    result = checker.check([])
    assert result["pass"] is False
    assert result["reason"] == "no_legs_provided"
