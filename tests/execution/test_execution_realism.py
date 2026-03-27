#!/usr/bin/env python3
"""Tests for ExecutionRealismEngine — Pack 4.3 execution realism extensions."""
from __future__ import annotations

import pytest

from src.execution.execution_realism import (
    DELAYED_ACK,
    NORMAL_LIQUIDITY,
    SPREAD_WIDENING,
    STALE_BROKER_STATE,
    VOLATILITY_AUCTION_RISK,
    ExecutionRealismEngine,
)


@pytest.fixture
def engine() -> ExecutionRealismEngine:
    return ExecutionRealismEngine()


# ------------------------------------------------------------------
# 1. estimate_queue_position
# ------------------------------------------------------------------

class TestEstimateQueuePosition:
    def test_aggressive_limit_has_high_fill_prob(self, engine: ExecutionRealismEngine) -> None:
        order = {"limit_price": 100.05, "side": "buy", "qty": 100}
        market = {"bid": 100.00, "ask": 100.05, "adv": 1_000_000}
        result = engine.estimate_queue_position(order, market)
        assert result["fill_probability"] >= 0.9
        assert result["expected_wait_seconds"] > 0

    def test_passive_limit_has_low_fill_prob(self, engine: ExecutionRealismEngine) -> None:
        order = {"limit_price": 99.00, "side": "buy", "qty": 100}
        market = {"bid": 100.00, "ask": 100.05, "adv": 1_000_000}
        result = engine.estimate_queue_position(order, market)
        assert result["fill_probability"] < 0.5

    def test_large_order_penalised(self, engine: ExecutionRealismEngine) -> None:
        order = {"limit_price": 100.05, "side": "buy", "qty": 200_000}
        market = {"bid": 100.00, "ask": 100.05, "adv": 1_000_000}
        result = engine.estimate_queue_position(order, market)
        # 20% of ADV should degrade fill probability
        assert result["fill_probability"] < 0.9
        assert result["adv_ratio"] > 0.1


# ------------------------------------------------------------------
# 2. estimate_cancel_replace_latency
# ------------------------------------------------------------------

class TestCancelReplaceLatency:
    def test_normal_regime_fast(self, engine: ExecutionRealismEngine) -> None:
        result = engine.estimate_cancel_replace_latency(NORMAL_LIQUIDITY)
        assert result["latency_ms"] == 50.0
        assert result["reliability"] == 1.0

    def test_delayed_ack_slow(self, engine: ExecutionRealismEngine) -> None:
        result = engine.estimate_cancel_replace_latency(DELAYED_ACK)
        assert result["latency_ms"] == 500.0
        assert result["reliability"] < 0.6

    def test_unknown_regime_defaults_normal(self, engine: ExecutionRealismEngine) -> None:
        result = engine.estimate_cancel_replace_latency("UNKNOWN_REGIME")
        assert result["latency_ms"] == 50.0


# ------------------------------------------------------------------
# 3. check_auction_window
# ------------------------------------------------------------------

class TestAuctionWindow:
    def test_opening_auction(self, engine: ExecutionRealismEngine) -> None:
        # 09:35 ET = 13:35 UTC (EDT offset -4)
        result = engine.check_auction_window("2026-03-07T13:35:00+00:00")
        assert result["window_type"] == "opening_auction"
        assert result["auction_risk_level"] == "high"
        assert result["recommended_order_type"] == "limit"

    def test_closing_auction(self, engine: ExecutionRealismEngine) -> None:
        # 15:55 ET = 19:55 UTC
        result = engine.check_auction_window("2026-03-07T19:55:00+00:00")
        assert result["window_type"] == "closing_auction"
        assert result["auction_risk_level"] == "elevated"

    def test_regular_session(self, engine: ExecutionRealismEngine) -> None:
        # 12:00 ET = 16:00 UTC
        result = engine.check_auction_window("2026-03-07T16:00:00+00:00")
        assert result["window_type"] == "regular_session"
        assert result["auction_risk_level"] == "low"


# ------------------------------------------------------------------
# 4. check_luld_halt_risk
# ------------------------------------------------------------------

class TestLULDHaltRisk:
    def test_low_risk(self, engine: ExecutionRealismEngine) -> None:
        result = engine.check_luld_halt_risk("AAPL", 150.0, 150.0)
        assert result["halt_risk_level"] == "low"
        assert result["distance_to_band_pct"] == 5.0

    def test_imminent_risk(self, engine: ExecutionRealismEngine) -> None:
        # 4.8% move on a large-cap (5% band) → 0.2% from band
        result = engine.check_luld_halt_risk("AAPL", 157.2, 150.0)
        assert result["halt_risk_level"] == "imminent"
        assert result["distance_to_band_pct"] < 0.5

    def test_small_cap_wider_band(self, engine: ExecutionRealismEngine) -> None:
        # ref_price < 50 → uses 10% band
        result = engine.check_luld_halt_risk("PENNY", 10.0, 10.0)
        assert result["band_pct"] == 10.0
        assert result["halt_risk_level"] == "low"


# ------------------------------------------------------------------
# 5. estimate_overnight_gap_risk
# ------------------------------------------------------------------

class TestOvernightGapRisk:
    def test_low_vol_low_gap(self, engine: ExecutionRealismEngine) -> None:
        result = engine.estimate_overnight_gap_risk("AAPL", 150.0, 0.15)
        assert result["gap_risk_level"] == "low"
        assert result["expected_gap_pct"] < 1.5

    def test_high_vol_high_gap(self, engine: ExecutionRealismEngine) -> None:
        result = engine.estimate_overnight_gap_risk("MEME", 20.0, 1.20)
        assert result["gap_risk_level"] == "high"
        assert result["expected_gap_pct"] >= 3.0


# ------------------------------------------------------------------
# 6. assess_venue_slippage
# ------------------------------------------------------------------

class TestVenueSlippage:
    def test_normal_regime_low_slippage(self, engine: ExecutionRealismEngine) -> None:
        order = {"qty": 100, "limit_price": 100.0}
        result = engine.assess_venue_slippage(order, NORMAL_LIQUIDITY)
        assert result["expected_slippage_bps"] < 3.0
        assert result["confidence"] > 0.7

    def test_stressed_regime_higher_slippage(self, engine: ExecutionRealismEngine) -> None:
        order = {"qty": 100, "limit_price": 100.0}
        normal = engine.assess_venue_slippage(order, NORMAL_LIQUIDITY)
        stressed = engine.assess_venue_slippage(order, VOLATILITY_AUCTION_RISK)
        assert stressed["expected_slippage_bps"] > normal["expected_slippage_bps"]


# ------------------------------------------------------------------
# 7. full_assessment
# ------------------------------------------------------------------

class TestFullAssessment:
    def test_returns_all_keys(self, engine: ExecutionRealismEngine) -> None:
        order = {"symbol": "AAPL", "limit_price": 150.0, "side": "buy", "qty": 100}
        market = {"bid": 149.95, "ask": 150.05, "adv": 50_000_000, "ref_price": 150.0, "last_close": 149.50, "volatility": 0.25}
        result = engine.full_assessment(order, market, NORMAL_LIQUIDITY, "2026-03-07T16:00:00+00:00")
        assert "overall_realism_score" in result
        assert "queue_position" in result
        assert "cancel_replace_latency" in result
        assert "auction_window" in result
        assert "luld_halt_risk" in result
        assert "overnight_gap_risk" in result
        assert "venue_slippage" in result
        assert 0.0 <= result["overall_realism_score"] <= 1.0

    def test_stressed_regime_lowers_score(self, engine: ExecutionRealismEngine) -> None:
        order = {"symbol": "AAPL", "limit_price": 150.0, "side": "buy", "qty": 100}
        market = {"bid": 149.95, "ask": 150.05, "adv": 50_000_000, "ref_price": 150.0, "last_close": 149.50, "volatility": 0.25}
        ts = "2026-03-07T16:00:00+00:00"
        normal = engine.full_assessment(order, market, NORMAL_LIQUIDITY, ts)
        stressed = engine.full_assessment(order, market, STALE_BROKER_STATE, ts)
        assert stressed["overall_realism_score"] < normal["overall_realism_score"]
