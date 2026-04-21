from __future__ import annotations

from src.monitoring.position_threshold_monitor import relation_to_threshold, threshold_crossed


def test_relation_to_threshold_handles_missing_values():
    assert relation_to_threshold(None, 1.0) is None


def test_threshold_crossed_up_only_fires_on_reclaim():
    assert relation_to_threshold(0.90, 0.95) == "below"
    assert relation_to_threshold(0.95, 0.95) == "above_or_equal"
    assert threshold_crossed("below", "above_or_equal", "up") is True
    assert threshold_crossed("above_or_equal", "above_or_equal", "up") is False


def test_threshold_crossed_down_only_fires_on_break():
    assert threshold_crossed("above_or_equal", "below", "down") is True
    assert threshold_crossed("below", "below", "down") is False
