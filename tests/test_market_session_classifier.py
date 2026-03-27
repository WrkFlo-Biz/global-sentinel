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


def test_classifies_crypto_continuous():
    classifier = MarketSessionClassifier()
    result = classifier.classify("2026-03-08T12:00:00+00:00", asset_class="crypto").to_dict()
    assert result["session"] == "continuous"
    assert result["is_market_open"] is True
