#!/usr/bin/env python3
"""Tests for MarketDataSanityCheck."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so imports resolve.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.execution.market_data_sanity_check import MarketDataSanityCheck


def _good_quote(**overrides):
    """Return a quote dict that passes all checks by default."""
    base = {
        "bid": 100.0,
        "ask": 100.10,
        "last": 100.05,
        "volume": 50000,
        "quote_age_seconds": 5,
        "prev_close": 99.50,
    }
    base.update(overrides)
    return base


@pytest.fixture
def checker():
    return MarketDataSanityCheck()


# ---------------------------------------------------------------
# Healthy quote — all pass
# ---------------------------------------------------------------
class TestAllPass:
    def test_good_quote_passes(self, checker):
        result = checker.check(_good_quote())
        assert result["pass"] is True
        assert "all checks passed" in result["reason"]

    def test_all_individual_checks_pass(self, checker):
        result = checker.check(_good_quote())
        for c in result["checks"]:
            assert c["passed"] is True, f"{c['name']} should pass"


# ---------------------------------------------------------------
# missing_fields
# ---------------------------------------------------------------
class TestMissingFields:
    def test_missing_bid(self, checker):
        q = _good_quote()
        del q["bid"]
        result = checker.check(q)
        assert result["pass"] is False
        chk = _find(result, "missing_fields")
        assert not chk["passed"]
        assert "bid" in chk["value"]

    def test_missing_multiple(self, checker):
        result = checker.check({"quote_age_seconds": 1})
        chk = _find(result, "missing_fields")
        assert not chk["passed"]
        assert set(chk["value"]) == {"bid", "ask", "last", "volume"}

    def test_none_value_counts_as_missing(self, checker):
        result = checker.check(_good_quote(volume=None))
        chk = _find(result, "missing_fields")
        assert not chk["passed"]


# ---------------------------------------------------------------
# zero_price
# ---------------------------------------------------------------
class TestZeroPrice:
    def test_zero_bid_fails(self, checker):
        result = checker.check(_good_quote(bid=0))
        chk = _find(result, "zero_price")
        assert not chk["passed"]

    def test_negative_ask_fails(self, checker):
        result = checker.check(_good_quote(ask=-1))
        chk = _find(result, "zero_price")
        assert not chk["passed"]

    def test_positive_prices_pass(self, checker):
        result = checker.check(_good_quote())
        chk = _find(result, "zero_price")
        assert chk["passed"]


# ---------------------------------------------------------------
# stale_quote
# ---------------------------------------------------------------
class TestStaleQuote:
    def test_stale_fails(self, checker):
        result = checker.check(_good_quote(quote_age_seconds=60))
        assert result["pass"] is False
        chk = _find(result, "stale_quote")
        assert not chk["passed"]

    def test_fresh_passes(self, checker):
        result = checker.check(_good_quote(quote_age_seconds=10))
        chk = _find(result, "stale_quote")
        assert chk["passed"]

    def test_edge_exact_threshold(self, checker):
        result = checker.check(_good_quote(quote_age_seconds=30))
        chk = _find(result, "stale_quote")
        assert chk["passed"]  # <= threshold

    def test_custom_threshold(self):
        c = MarketDataSanityCheck(max_quote_age_seconds=5)
        result = c.check(_good_quote(quote_age_seconds=10))
        chk = _find(result, "stale_quote")
        assert not chk["passed"]


# ---------------------------------------------------------------
# crossed_market
# ---------------------------------------------------------------
class TestCrossedMarket:
    def test_crossed_fails(self, checker):
        result = checker.check(_good_quote(bid=101, ask=100))
        assert result["pass"] is False
        chk = _find(result, "crossed_market")
        assert not chk["passed"]

    def test_normal_passes(self, checker):
        result = checker.check(_good_quote(bid=100, ask=100.10))
        chk = _find(result, "crossed_market")
        assert chk["passed"]


# ---------------------------------------------------------------
# locked_market (warn, still passes)
# ---------------------------------------------------------------
class TestLockedMarket:
    def test_locked_warns_but_passes(self, checker):
        result = checker.check(_good_quote(bid=100, ask=100))
        assert result["pass"] is True
        chk = _find(result, "locked_market")
        assert chk["passed"]
        assert chk["severity"] == "warn"

    def test_normal_no_warn(self, checker):
        result = checker.check(_good_quote(bid=100, ask=100.10))
        chk = _find(result, "locked_market")
        assert chk["severity"] != "warn"


# ---------------------------------------------------------------
# impossible_jump
# ---------------------------------------------------------------
class TestImpossibleJump:
    def test_huge_jump_fails(self, checker):
        result = checker.check(_good_quote(last=130, prev_close=100))
        assert result["pass"] is False
        chk = _find(result, "impossible_jump")
        assert not chk["passed"]

    def test_small_move_passes(self, checker):
        result = checker.check(_good_quote(last=101, prev_close=100))
        chk = _find(result, "impossible_jump")
        assert chk["passed"]

    def test_no_prev_close_skipped(self, checker):
        q = _good_quote()
        del q["prev_close"]
        result = checker.check(q)
        chk = _find(result, "impossible_jump")
        assert chk["passed"]
        assert chk["severity"] == "skip"

    def test_exactly_20_pct_passes(self, checker):
        result = checker.check(_good_quote(last=120, prev_close=100))
        chk = _find(result, "impossible_jump")
        assert chk["passed"]  # <= 0.20


# ---------------------------------------------------------------
# spread_too_wide
# ---------------------------------------------------------------
class TestSpreadTooWide:
    def test_wide_spread_fails(self, checker):
        # spread = 10/105 ≈ 9.5% > 5%
        result = checker.check(_good_quote(bid=100, ask=110))
        assert result["pass"] is False
        chk = _find(result, "spread_too_wide")
        assert not chk["passed"]

    def test_narrow_spread_passes(self, checker):
        result = checker.check(_good_quote(bid=100, ask=100.01))
        chk = _find(result, "spread_too_wide")
        assert chk["passed"]

    def test_custom_threshold(self):
        c = MarketDataSanityCheck(max_spread_pct=0.10)
        # spread = 10/105 ≈ 9.5% < 10%
        result = c.check(_good_quote(bid=100, ask=110))
        chk = _find(result, "spread_too_wide")
        assert chk["passed"]


# ---------------------------------------------------------------
# volume_too_low (warn, still passes)
# ---------------------------------------------------------------
class TestVolumeTooLow:
    def test_low_volume_warns_but_passes(self, checker):
        result = checker.check(_good_quote(volume=50))
        assert result["pass"] is True
        chk = _find(result, "volume_too_low")
        assert chk["passed"]
        assert chk["severity"] == "warn"

    def test_high_volume_ok(self, checker):
        result = checker.check(_good_quote(volume=10000))
        chk = _find(result, "volume_too_low")
        assert chk["severity"] == "ok"


# ---------------------------------------------------------------
# Structured result shape
# ---------------------------------------------------------------
class TestResultShape:
    def test_keys_present(self, checker):
        result = checker.check(_good_quote())
        assert "pass" in result
        assert "reason" in result
        assert "checks" in result
        assert "timestamp" in result

    def test_all_check_names_present(self, checker):
        result = checker.check(_good_quote())
        names = {c["name"] for c in result["checks"]}
        expected = {
            "missing_fields",
            "zero_price",
            "stale_quote",
            "crossed_market",
            "locked_market",
            "impossible_jump",
            "spread_too_wide",
            "volume_too_low",
        }
        assert names == expected


# ---------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------
class TestConfigLoading:
    def test_from_yaml_file_missing(self, tmp_path):
        c = MarketDataSanityCheck.from_yaml_file(tmp_path / "nope.yaml")
        assert c.max_quote_age_seconds == 30.0  # default

    def test_from_yaml_file_valid(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("max_quote_age_seconds: 10\nmin_volume: 500\n")
        c = MarketDataSanityCheck.from_yaml_file(f)
        assert c.max_quote_age_seconds == 10
        assert c.min_volume == 500


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def _find(result: dict, name: str) -> dict:
    for c in result["checks"]:
        if c["name"] == name:
            return c
    raise KeyError(f"check '{name}' not found in result")
