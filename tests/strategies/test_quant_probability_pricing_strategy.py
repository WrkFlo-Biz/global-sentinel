from __future__ import annotations

from src.strategies import (
    QuantProbabilityPricingStrategy,
    evaluate_quant_probability_pricing,
)


def test_quant_probability_detects_short_vol_signal() -> None:
    strategy = QuantProbabilityPricingStrategy()
    ideas = strategy.scan_watchlist(
        market_data={
            "SPY": {
                "implied_volatility": 0.32,
                "realized_volatility": 0.20,
                "iv_percentile": 82.0,
                "put_iv": 0.34,
                "call_iv": 0.28,
                "front_month_iv": 0.30,
                "back_month_iv": 0.27,
            }
        },
        scorecard={"component_scores": {"geopolitical_tension": 0.1}},
    )

    assert ideas
    assert ideas[0]["symbol"] == "SPY"
    assert ideas[0]["direction"] == "short_vol"
    assert ideas[0]["confidence_score"] >= 0.6


def test_quant_probability_wrapper_detects_long_vol_signal() -> None:
    ideas = evaluate_quant_probability_pricing(
        market_data={
            "TSLA": {
                "implied_volatility": 0.24,
                "realized_volatility": 0.36,
                "iv_percentile": 28.0,
                "front_month_iv": 0.21,
                "back_month_iv": 0.27,
            }
        },
        scorecard={"component_scores": {"geopolitical_tension": 0.55}},
    )

    assert ideas
    assert ideas[0]["symbol"] == "TSLA"
    assert ideas[0]["direction"] == "long_vol"
    assert ideas[0]["metadata"]["iv_rv_ratio"] < 0.78

