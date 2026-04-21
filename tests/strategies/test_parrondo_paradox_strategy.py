from __future__ import annotations

from src.strategies import ParrondoParadoxStrategy, evaluate_parrondo_paradox


def test_parrondo_uses_mean_reversion_when_recent_pnl_is_positive() -> None:
    strategy = ParrondoParadoxStrategy()
    strategy.set_recent_pnl(125.0)

    ideas = strategy.scan_watchlist(
        market_data={
            "AAPL": {"rsi": 24.0, "change_pct": -2.4},
            "MSFT": {"rsi": 29.0, "change_pct": -1.8},
        },
        scorecard={"component_scores": {"volatility_regime": 0.65}},
    )

    assert ideas
    assert ideas[0]["strategy"] == "parrondo_paradox"
    assert ideas[0]["active_sub"] == "A"
    assert ideas[0]["metadata"]["sub_strategy"] == "A_mean_reversion"
    assert ideas[0]["confidence_score"] == ideas[0]["confidence"]


def test_parrondo_wrapper_switches_to_momentum_when_recent_pnl_is_negative() -> None:
    ideas = evaluate_parrondo_paradox(
        market_data={
            "NVDA": {"change_pct": 4.2, "relative_volume": 2.4},
            "TSLA": {"change_pct": 2.8, "relative_volume": 2.0},
        },
        scorecard={"component_scores": {"geopolitical_tension": 0.6}},
        recent_pnl=-50.0,
    )

    assert ideas
    assert ideas[0]["symbol"] == "NVDA"
    assert ideas[0]["active_sub"] == "B"
    assert ideas[0]["direction"] == "long"

