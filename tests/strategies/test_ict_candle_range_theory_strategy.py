from __future__ import annotations

from src.strategies import (
    ICTCandleRangeTheoryStrategy,
    evaluate_ict_candle_range_theory,
)


def test_ict_crt_detects_buy_side_sweep_reversal() -> None:
    strategy = ICTCandleRangeTheoryStrategy()
    ideas = strategy.scan_watchlist(
        market_data={
            "GLD": {
                "open": 244.8,
                "high": 245.8,
                "low": 241.0,
                "close": 243.2,
                "prior_high": 244.0,
                "prior_low": 240.0,
            }
        ,
            "DXY": {"change_pct": 0.42},
        },
        scorecard={"component_scores": {"geopolitical_tension": 0.5, "macro_news": 0.4}},
    )

    assert ideas
    assert ideas[0]["symbol"] == "GLD"
    assert ideas[0]["direction"] == "short"
    assert ideas[0]["confidence_score"] >= 0.6
    assert ideas[0]["metadata"]["has_fvg"] is True
    assert ideas[0]["metadata"]["dxy_inverse_confirmation"] is True
    assert ideas[0]["metadata"]["news_catalyst_score"] >= 0.4


def test_ict_crt_wrapper_returns_candidates() -> None:
    ideas = evaluate_ict_candle_range_theory(
        market_data={
            "QQQ": {
                "open": 511.2,
                "high": 512.4,
                "low": 505.3,
                "close": 507.2,
                "prior_high": 510.0,
                "prior_low": 506.0,
            }
        },
        scorecard={},
    )

    assert ideas
    assert ideas[0]["strategy"] == "ict_candle_range_theory"
