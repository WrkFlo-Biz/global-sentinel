from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.alpha.trade_analysis_engine import TradeAnalysisEngine


REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_repo(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "config" / "thresholds.yaml", config_dir / "thresholds.yaml")
    shutil.copyfile(REPO_ROOT / "config" / "assets_watchlist.yaml", config_dir / "assets_watchlist.yaml")
    return tmp_path


def test_trade_analysis_engine_applies_learning_to_equity_and_option_ideas(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    engine = TradeAnalysisEngine(repo_root)

    scorecard = {
        "mode": "NORMAL",
        "regime_shift_probability": 0.35,
        "confidence": 0.95,
        "component_scores": {
            "geopolitical_tension": 0.05,
            "market_volatility": 0.5,
            "currency_stress": 0.5854,
            "commodity_shock": 0.6364,
            "policy_signals": 0.85,
        },
        "time_window": {},
    }
    microstructure = {
        "RTX": {"last_price": 160.0, "sigma_daily_pct": 1.8},
        "SPY": {"last_price": 500.0, "sigma_daily_pct": 1.2},
        "QQQ": {"last_price": 430.0, "sigma_daily_pct": 1.4},
    }

    baseline = engine.analyze(scorecard, microstructure=microstructure)
    base_rtx = next(idea for idea in baseline["trade_ideas"] if idea["symbol"] == "RTX" and idea.get("instrument_type") != "option")
    base_spy_option = next(
        idea for idea in baseline["trade_ideas"]
        if idea.get("instrument_type") == "option" and idea["symbol"] == "SPY"
    )

    feedback_path = repo_root / "logs" / "execution" / "feedback_state.json"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "signal_adjustments": {},
                "strategy_confidence_adjustments": {
                    "NORMAL_to_ELEVATED": 0.08,
                    "day_trade": 0.03,
                },
                "strategy_adjustments": {
                    "NORMAL_to_ELEVATED": {
                        "stop_loss_tightness": 1.1,
                        "profit_target_mult": 1.08,
                    },
                    "day_trade": {
                        "stop_loss_tightness": 1.02,
                        "profit_target_mult": 1.04,
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    learned = engine.analyze(scorecard, microstructure=microstructure)
    learned_rtx = next(idea for idea in learned["trade_ideas"] if idea["symbol"] == "RTX" and idea.get("instrument_type") != "option")
    learned_spy_option = next(
        idea for idea in learned["trade_ideas"]
        if idea.get("instrument_type") == "option" and idea["symbol"] == "SPY"
    )

    assert learned_rtx["learning_adjusted"] is True
    assert learned_rtx["strategy"] == "NORMAL_to_ELEVATED"
    assert learned_rtx["strategy_family"] == "day_trade"
    assert learned_rtx["confidence_adjusted_score"] > base_rtx["confidence_adjusted_score"]
    assert learned_rtx["learning_adjustment_detail"]["strategy"] == "NORMAL_to_ELEVATED"

    assert learned_spy_option["learning_adjusted"] is True
    assert learned_spy_option["strategy"] == "NORMAL_to_ELEVATED_option"
    assert learned_spy_option["underlying_strategy"] == "NORMAL_to_ELEVATED"
    assert learned_spy_option["confidence_adjusted_score"] > base_spy_option["confidence_adjusted_score"]
