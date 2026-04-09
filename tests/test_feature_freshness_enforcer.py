"""Tests for src/core/feature_freshness_enforcer.py"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from src.core.feature_freshness_enforcer import FeatureFreshnessEnforcer


@pytest.fixture
def enforcer():
    return FeatureFreshnessEnforcer(config_dir=Path("config"))


def test_loads_config(enforcer):
    assert enforcer.is_loaded is True


def test_fresh_feature(enforcer):
    now = datetime.now(timezone.utc)
    result = enforcer.check_feature("base_score", last_updated=now - timedelta(minutes=5), now=now)
    assert result.status == "fresh"
    assert result.confidence_penalty == 0.0
    assert result.stale_reason == ""


def test_stale_feature(enforcer):
    now = datetime.now(timezone.utc)
    # base_score TTL is 60 min, 90 min ago = stale
    result = enforcer.check_feature("base_score", last_updated=now - timedelta(minutes=90), now=now)
    assert result.status == "stale"
    assert result.confidence_penalty > 0


def test_expired_feature(enforcer):
    now = datetime.now(timezone.utc)
    # base_score TTL is 60 min, 200 min ago = expired (>2x TTL)
    result = enforcer.check_feature("base_score", last_updated=now - timedelta(minutes=200), now=now)
    assert result.status == "expired"
    assert result.confidence_penalty == 1.0


def test_no_timestamp_is_expired(enforcer):
    result = enforcer.check_feature("base_score", last_updated=None)
    assert result.status == "expired"
    assert result.confidence_penalty == 1.0


def test_unknown_feature(enforcer):
    result = enforcer.check_feature("nonexistent_feature")
    assert result.status == "unknown"
    assert result.confidence_penalty == 0.5


def test_liquidity_score_short_ttl(enforcer):
    now = datetime.now(timezone.utc)
    # liquidity_score TTL is 5 min
    result = enforcer.check_feature("liquidity_score", last_updated=now - timedelta(minutes=3), now=now)
    assert result.status == "fresh"
    result2 = enforcer.check_feature("liquidity_score", last_updated=now - timedelta(minutes=8), now=now)
    assert result2.status == "stale"


def test_group_all_fresh(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "liquidity_score": now - timedelta(minutes=1),
        "volatility_penalty": now - timedelta(minutes=2),
    }
    result = enforcer.check_group("market_microstructure", timestamps, now)
    assert result.compliant is True
    assert result.fresh_count == 2
    assert result.degraded is False


def test_group_all_fresh_fails_on_stale(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "liquidity_score": now - timedelta(minutes=1),
        "volatility_penalty": now - timedelta(minutes=30),  # TTL=5, way stale
    }
    result = enforcer.check_group("market_microstructure", timestamps, now)
    assert result.compliant is False
    assert result.degraded is True


def test_group_best_effort_always_compliant(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "regime_alignment": now - timedelta(minutes=100),  # stale
        "gpr_index": now - timedelta(minutes=10),  # fresh (TTL=1440)
    }
    result = enforcer.check_group("regime_context", timestamps, now)
    assert result.compliant is True
    assert result.confidence_penalty > 0  # penalized for stale feature


def test_summary_separates_critical_and_advisory_groups(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "liquidity_score": now,
        "volatility_penalty": now,
        "put_call_ratio": now - timedelta(minutes=40),  # stale; advisory only
        "implied_volatility": now,
        "gamma_squeeze_risk": now,
    }

    summary = enforcer.summary(timestamps, now)
    groups = summary["groups"]

    assert groups["market_microstructure"]["operational_critical"] is True
    assert groups["options_greeks"]["operational_critical"] is False
    assert summary["critical_degraded_groups"] == 0
    assert summary["advisory_degraded_groups"] >= 1
    assert summary["max_confidence_penalty"] == 0.0
    assert summary["overall_max_confidence_penalty"] > 0.0


def test_group_quorum(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "gpr_index": now - timedelta(minutes=10),
        "cds_spread": now - timedelta(minutes=30),
        "semiconductor_supply_index": None,  # missing
        "maritime_congestion": None,  # missing
    }
    result = enforcer.check_group("geopolitical_signals", timestamps, now)
    # quorum requires min_fresh_ratio=0.5 and min_features_required=2
    assert result.fresh_count == 2
    assert result.compliant is True


def test_group_quorum_fails(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "gpr_index": now - timedelta(minutes=10),
        "cds_spread": None,
        "semiconductor_supply_index": None,
        "maritime_congestion": None,
    }
    result = enforcer.check_group("geopolitical_signals", timestamps, now)
    assert result.fresh_count == 1
    assert result.compliant is False


def test_check_all_groups(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {
        "base_score": now,
        "event_score": now,
        "quality_score": now,
        "anomaly_score": now,
        "liquidity_score": now,
        "volatility_penalty": now,
        "regime_alignment": now,
        "gpr_index": now,
        "put_call_ratio": now,
        "implied_volatility": now,
        "gamma_squeeze_risk": now,
        "cds_spread": now,
        "semiconductor_supply_index": now,
        "maritime_congestion": now,
        "preopt_feature_score": now,
    }
    results = enforcer.check_all_groups(timestamps, now)
    assert len(results) == 6
    assert all(r.compliant for r in results.values())


def test_summary(enforcer):
    now = datetime.now(timezone.utc)
    timestamps = {"base_score": now, "event_score": now}
    s = enforcer.summary(timestamps, now)
    assert "total_groups" in s
    assert "compliant_groups" in s
    assert "groups" in s


def test_unknown_group(enforcer):
    result = enforcer.check_group("nonexistent_group", {})
    assert result.compliant is False
    assert result.degraded is True
