from __future__ import annotations

from datetime import datetime, timezone

from src.strategies import MGCAIOptimizedStrategy, evaluate_mgc_ai_optimized


def _ts(hour_ct: int, minute_ct: int = 0) -> float:
    return datetime(2026, 4, 8, hour_ct + 5, minute_ct, tzinfo=timezone.utc).timestamp()


def test_mgc_ai_strategy_emits_long_breakout_signal() -> None:
    strategy = MGCAIOptimizedStrategy()
    market_data = {
        "MGC": {
            "price": 2561.0,
            "opening_range_high": 2554.0,
            "opening_range_low": 2547.0,
            "vwap": 2555.0,
            "relative_volume": 1.8,
            "atr_14": 4.8,
            "change_pct": 0.74,
            "timestamp": _ts(9, 5),
        },
        "DXY": {"price": 102.1, "change_pct": -0.32},
        "TNX": {"price": 4.19, "change_pct": -0.41},
        "SPY": {"price": 511.0, "change_pct": -0.52},
    }
    scorecard = {
        "component_scores": {
            "geopolitical_tension": 0.18,
            "market_volatility": 0.21,
        }
    }

    idea = strategy.evaluate("MGC", market_data=market_data, scorecard=scorecard)

    assert idea is not None
    assert idea["direction"] == "long"
    assert idea["strategy"] == "mgc_ai_optimized"
    assert idea["confidence_score"] >= 0.60
    assert idea["metadata"]["setup_type"] == "orb_breakout"


def test_mgc_ai_strategy_emits_short_breakdown_signal() -> None:
    strategy = MGCAIOptimizedStrategy()
    market_data = {
        "GLD": {
            "price": 238.10,
            "opening_range_high": 239.05,
            "opening_range_low": 238.55,
            "vwap": 238.42,
            "relative_volume": 1.55,
            "atr_14": 0.44,
            "change_pct": -0.61,
            "timestamp": _ts(8, 42),
        },
        "UUP": {"price": 29.8, "change_pct": 0.35},
        "TNX": {"price": 4.31, "change_pct": 0.52},
        "QQQ": {"price": 432.0, "change_pct": 0.48},
    }
    scorecard = {
        "component_scores": {
            "geopolitical_tension": 0.04,
            "market_volatility": 0.08,
        }
    }

    idea = strategy.evaluate("GLD", market_data=market_data, scorecard=scorecard)

    assert idea is not None
    assert idea["direction"] == "short"
    assert idea["metadata"]["setup_type"] == "orb_breakdown"
    assert idea["confidence_score"] >= 0.55


def test_mgc_ai_strategy_respects_trade_window() -> None:
    strategy = MGCAIOptimizedStrategy()
    market_data = {
        "MGC": {
            "price": 2559.0,
            "opening_range_high": 2554.0,
            "opening_range_low": 2548.0,
            "vwap": 2555.0,
            "relative_volume": 1.9,
            "atr_14": 4.5,
            "change_pct": 0.62,
            "timestamp": _ts(13, 0),
        },
        "DXY": {"price": 102.1, "change_pct": -0.28},
        "TNX": {"price": 4.10, "change_pct": -0.40},
    }

    assert strategy.evaluate("MGC", market_data=market_data, scorecard={}) is None


def test_evaluate_wrapper_returns_sorted_watchlist_candidates() -> None:
    market_data = {
        "MGC": {
            "price": 2561.0,
            "opening_range_high": 2554.0,
            "opening_range_low": 2547.0,
            "vwap": 2555.0,
            "relative_volume": 1.8,
            "atr_14": 4.8,
            "change_pct": 0.74,
            "timestamp": _ts(9, 5),
        },
        "GLD": {
            "price": 238.10,
            "opening_range_high": 239.05,
            "opening_range_low": 238.55,
            "vwap": 238.42,
            "relative_volume": 1.55,
            "atr_14": 0.44,
            "change_pct": -0.61,
            "timestamp": _ts(8, 42),
        },
        "DXY": {"price": 102.1, "change_pct": -0.32},
        "TNX": {"price": 4.19, "change_pct": -0.41},
        "SPY": {"price": 511.0, "change_pct": -0.52},
    }
    scorecard = {"component_scores": {"geopolitical_tension": 0.12, "market_volatility": 0.18}}

    ideas = evaluate_mgc_ai_optimized(market_data=market_data, scorecard=scorecard)

    assert ideas
    assert ideas == sorted(ideas, key=lambda item: item["confidence_score"], reverse=True)


def test_mgc_ai_strategy_supports_asia_session_orb_profile() -> None:
    strategy = MGCAIOptimizedStrategy()
    market_data = {
        "MGC": {
            "price": 2562.4,
            "opening_range_high": 2558.0,
            "opening_range_low": 2550.0,
            "vwap": 2557.2,
            "relative_volume": 1.35,
            "atr_14": 4.1,
            "change_pct": 0.66,
            "timestamp": _ts(17, 45),  # 18:45 ET
        },
        "DXY": {"price": 101.8, "change_pct": -0.24},
        "TNX": {"price": 4.11, "change_pct": -0.18},
    }

    idea = strategy.evaluate("MGC", market_data=market_data, scorecard={})

    assert idea is not None
    assert idea["direction"] == "long"
    assert idea["metadata"]["active_session"] == "asia_session_orb"
    assert idea["metadata"]["asia_range_minutes"] == 25
