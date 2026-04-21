from __future__ import annotations

from src.strategies import (
    HormuzOsintGeopoliticalStrategy,
    evaluate_hormuz_osint_geopolitical,
)


def test_hormuz_strategy_emits_energy_and_disruption_signals() -> None:
    strategy = HormuzOsintGeopoliticalStrategy()
    ideas = strategy.scan_watchlist(
        market_data={
            "USO": {"change_pct": 2.8, "relative_volume": 2.2},
            "XLE": {"change_pct": 1.9, "relative_volume": 1.8},
            "GLD": {"change_pct": 0.9, "relative_volume": 1.4},
            "DAL": {"change_pct": -2.6, "relative_volume": 2.0},
            "ZIM": {"change_pct": -2.2, "relative_volume": 1.7},
        },
        scorecard={
            "component_scores": {
                "geopolitical_tension": 0.75,
                "commodity_shock": 0.60,
            },
            "v6_oil_regime": "SHOCK",
        },
        osint_data={
            "gdelt": {"hormuz_conflict_score": 0.82},
            "exa": {"escalation_score": 0.76},
            "maritime": {"hormuz": {"disruption_score": 0.68, "dark_vessel_count_24h": 6, "toll_usd_per_barrel": 1.5}},
        },
    )

    assert ideas
    assert any(idea["symbol"] in {"USO", "XLE", "GLD"} and idea["direction"] == "long" for idea in ideas)
    assert any(idea["symbol"] in {"DAL", "ZIM"} and idea["direction"] == "short" for idea in ideas)
    assert any(idea["metadata"]["dark_vessel_score"] > 0 for idea in ideas)
    assert any(idea["metadata"]["toll_cost_score"] > 0 for idea in ideas)


def test_hormuz_wrapper_blocks_low_escalation() -> None:
    ideas = evaluate_hormuz_osint_geopolitical(
        market_data={"USO": {"change_pct": 0.4, "relative_volume": 1.1}},
        scorecard={"component_scores": {"geopolitical_tension": 0.1, "commodity_shock": 0.1}},
        osint_data={
            "gdelt": {"hormuz_conflict_score": 0.05},
            "exa": {"escalation_score": 0.08},
            "maritime": {"hormuz": {"disruption_score": 0.02}},
        },
    )

    assert ideas == []
