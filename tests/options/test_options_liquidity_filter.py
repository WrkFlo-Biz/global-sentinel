"""Tests for ``src.options.options_liquidity_filter``."""
from src.options.options_liquidity_filter import OptionsLiquidityFilter


def test_filter_passes_liquid_contract():
    liquidity_filter = OptionsLiquidityFilter()
    passed, rejected = liquidity_filter.filter(
        [{"OI": 200, "volume": 25, "bid": 1.0, "ask": 1.1, "expiry": "2026-04-17", "strike": 500}]
    )
    assert len(passed) == 1
    assert rejected == []
    assert passed[0]["liquidity_filter_passed"] is True


def test_filter_rejects_low_open_interest():
    liquidity_filter = OptionsLiquidityFilter(min_open_interest=150)
    passed, rejected = liquidity_filter.filter([{"OI": 10, "volume": 30, "bid": 1.0, "ask": 1.05}])
    assert passed == []
    assert rejected[0]["liquidity_rejection_reasons"] == ["open_interest_below_150"]


def test_filter_rejects_low_volume_and_wide_spread():
    liquidity_filter = OptionsLiquidityFilter(max_spread_pct=0.10, min_volume=20)
    _, rejected = liquidity_filter.filter([{"OI": 500, "volume": 5, "bid": 1.0, "ask": 1.3}])
    reasons = rejected[0]["liquidity_rejection_reasons"]
    assert "volume_below_20" in reasons
    assert "spread_pct_above_0.10" in reasons


def test_filter_rejects_invalid_market():
    liquidity_filter = OptionsLiquidityFilter()
    _, rejected = liquidity_filter.filter([{"OI": 500, "volume": 50, "bid": 2.0, "ask": 1.5}])
    assert rejected[0]["liquidity_rejection_reasons"] == ["invalid_bid_ask"]
