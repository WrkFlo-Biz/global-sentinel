from pathlib import Path

from src.alpha.strategy_engine import StrategyEngine


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_strategy_engine_exposes_loaded_strategies():
    engine = StrategyEngine(repo_root=str(REPO_ROOT))
    assert len(engine.strategies) >= 15
    assert "shipping_rate_explosion" in engine.strategies


def test_strategy_engine_fires_regime_based_strategies_without_market_data():
    engine = StrategyEngine(repo_root=str(REPO_ROOT))
    scorecard = {
        "regime_shift_probability": 0.35,
        "chokepoint_risk": {
            "hormuz": 0.0,
            "bab_el_mandeb": 0.2,
            "eastern_med": 0.0,
            "composite": 0.07,
        },
        "component_scores": {
            "geopolitical_tension": 0.05,
            "market_volatility": 0.5,
            "currency_stress": 0.5854,
            "commodity_shock": 0.6364,
            "policy_signals": 0.85,
        },
        "v6_scanner_discoveries": [
            {"symbol": "RKLB", "category": "satellite_isr", "confidence": 0.6},
        ],
    }

    ideas = engine.evaluate_entries(scorecard=scorecard, bridge_results=scorecard, market_data={})
    strategies = {idea["strategy"] for idea in ideas}

    assert "shipping_rate_explosion" in strategies
    assert "defense_accumulation" in strategies
    assert "gold_safe_haven" in strategies
    assert "airline_short" in strategies
    assert "europe_energy_crisis" in strategies
    assert "nuclear_renaissance" in strategies
