from __future__ import annotations

from src.strategies import OptionsFlowModelStrategy, evaluate_options_flow_model


def test_options_flow_model_emits_bullish_flow_signal() -> None:
    strategy = OptionsFlowModelStrategy()
    ideas = strategy.scan_watchlist(
        market_data={
            "NVDA": {
                "change_pct": 1.9,
                "relative_volume": 2.1,
                "unusual_volume_score": 0.72,
                "unusual_volume_direction": "call",
                "dark_pool_score": 0.58,
                "dark_pool_direction": "long",
                "flow_imbalance_zscore": 2.8,
                "sweep_count": 4,
                "sweep_direction": "call",
                "hourly_gap_direction": "bullish",
                "liquidity_swept": True,
                "ema_13_break": "up",
            }
        },
        scorecard={"component_scores": {"market_volatility": 0.55}},
    )

    assert ideas
    assert ideas[0]["symbol"] == "NVDA"
    assert ideas[0]["direction"] == "long"
    assert ideas[0]["confidence_score"] >= 0.6
    assert "htf_gap_bias" in ideas[0]["metadata"]["flow_model_checklist"]
    assert ideas[0]["take_profit_pct"] == 1.2


def test_options_flow_wrapper_returns_sorted_candidates() -> None:
    ideas = evaluate_options_flow_model(
        market_data={
            "NVDA": {
                "change_pct": 1.9,
                "relative_volume": 2.1,
                "unusual_volume_score": 0.72,
                "unusual_volume_direction": "call",
                "dark_pool_score": 0.58,
                "dark_pool_direction": "long",
                "flow_imbalance_zscore": 2.8,
                "sweep_count": 4,
                "sweep_direction": "call",
                "hourly_gap_direction": "bullish",
                "liquidity_swept": True,
                "ema_13_break": "up",
            },
            "QQQ": {
                "change_pct": -1.2,
                "relative_volume": 1.8,
                "unusual_volume_score": 0.55,
                "unusual_volume_direction": "put",
                "dark_pool_score": 0.44,
                "dark_pool_direction": "short",
                "flow_imbalance_zscore": -2.2,
                "sweep_count": 3,
                "sweep_direction": "put",
                "hourly_gap_direction": "bearish",
                "liquidity_swept": True,
                "ema13_break": "down",
            },
        },
        scorecard={"component_scores": {"market_volatility": 0.55}},
    )

    assert ideas
    assert ideas == sorted(ideas, key=lambda item: item["confidence_score"], reverse=True)
