import json
import shutil
from pathlib import Path

from src.alpha.strategy_engine import StrategyEngine


REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed_repo(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "config" / "war_strategies.yaml", config_dir / "war_strategies.yaml")
    return tmp_path


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


def test_strategy_engine_applies_strategy_specific_feedback(tmp_path: Path):
    repo_root = _seed_repo(tmp_path)
    engine = StrategyEngine(repo_root=str(repo_root))
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

    baseline = engine.evaluate_entries(scorecard=scorecard, bridge_results=scorecard, market_data={})
    base_shipping = next(idea for idea in baseline if idea["strategy"] == "shipping_rate_explosion")

    feedback_path = repo_root / "logs" / "execution" / "feedback_state.json"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "signal_adjustments": {},
                "strategy_confidence_adjustments": {
                    "shipping_rate_explosion": 0.08,
                    "medium_long": 0.03,
                },
                "strategy_adjustments": {
                    "shipping_rate_explosion": {
                        "stop_loss_tightness": 1.2,
                        "profit_target_mult": 1.1,
                    },
                    "medium_long": {
                        "stop_loss_tightness": 1.05,
                        "profit_target_mult": 1.04,
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    learned = engine.evaluate_entries(scorecard=scorecard, bridge_results=scorecard, market_data={})
    learned_shipping = next(idea for idea in learned if idea["strategy"] == "shipping_rate_explosion")

    assert learned_shipping["learning_adjusted"] is True
    assert learned_shipping["strategy_family"] == "medium_long"
    assert learned_shipping["confidence"] > base_shipping["confidence"]
    assert learned_shipping["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"
    assert learned_shipping["learning_adjustment_detail"]["profit_target_mult"] >= 1.04
