from __future__ import annotations

from src.strategies import KronosForecastOverlayStrategy


def test_kronos_forecast_overlay_generates_long_signal() -> None:
    strategy = KronosForecastOverlayStrategy()
    market_data = {
        "QQQ": {
            "price": 441.0,
            "vwap": 439.6,
            "change_pct": 0.7,
            "relative_volume": 1.5,
            "kronos_forecast_return_1h": 0.82,
            "kronos_confidence": 0.76,
            "kronos_volatility_forecast": 1.1,
        }
    }

    idea = strategy.evaluate("QQQ", market_data=market_data, scorecard={})

    assert idea is not None
    assert idea["direction"] == "long"
    assert idea["strategy"] == "kronos_forecast_overlay"
    assert idea["confidence_score"] >= 0.7


def test_kronos_forecast_overlay_requires_alignment() -> None:
    strategy = KronosForecastOverlayStrategy()
    market_data = {
        "QQQ": {
            "price": 438.0,
            "vwap": 439.6,
            "change_pct": -0.4,
            "relative_volume": 1.5,
            "kronos_forecast_return_1h": 0.82,
            "kronos_confidence": 0.76,
            "kronos_volatility_forecast": 1.1,
        }
    }

    assert strategy.evaluate("QQQ", market_data=market_data, scorecard={}) is None
