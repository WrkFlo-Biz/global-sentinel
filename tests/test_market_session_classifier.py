"""Tests for market session classification."""
from __future__ import annotations

from src.core.market_session_classifier import MarketSessionClassifier


def test_classifies_overnight_session():
    classifier = MarketSessionClassifier()
    result = classifier.classify("2026-03-09T01:30:00+00:00", asset_class="equity").to_dict()
    assert result["session"] == "overnight"
    assert result["constraints"]["limit_only"] is True
    assert result["constraints"]["allowed_time_in_force"] == ["day"]


def test_classifies_regular_session():
    classifier = MarketSessionClassifier()
    result = classifier.classify("2026-03-09T15:00:00+00:00", asset_class="equity").to_dict()
    assert result["session"] == "regular"
    assert result["is_market_open"] is True
    assert result["is_extended_hours"] is False


def test_classifies_intraday_phases():
    classifier = MarketSessionClassifier()

    opening = classifier.classify("2026-03-09T14:00:00+00:00", asset_class="equity").to_dict()
    midday = classifier.classify("2026-03-09T17:00:00+00:00", asset_class="equity").to_dict()
    power_hour = classifier.classify("2026-03-09T19:00:00+00:00", asset_class="equity").to_dict()

    assert opening["session"] == "regular"
    assert opening["intraday_phase"] == "opening"
    assert opening["session_bucket"] == "opening"
    assert opening["constraints"]["liquidity_profile"] == "opening_whipsaw"

    assert midday["intraday_phase"] == "midday"
    assert midday["session_bucket"] == "midday"
    assert midday["constraints"]["liquidity_profile"] == "midday_lull"

    assert power_hour["intraday_phase"] == "power_hour"
    assert power_hour["session_bucket"] == "power_hour"
    assert power_hour["constraints"]["liquidity_profile"] == "power_hour"


def test_classifies_crypto_continuous():
    classifier = MarketSessionClassifier()
    result = classifier.classify("2026-03-08T12:00:00+00:00", asset_class="crypto").to_dict()
    assert result["session"] == "continuous"
    assert result["is_market_open"] is True
    assert result["intraday_phase"] == "continuous"
