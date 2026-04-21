from __future__ import annotations

import json
from pathlib import Path

from src.execution.trade_idea_packager import TradeIdeaPackager


def _scorecard_lunch_lull():
    return {
        "mode": "NORMAL",
        "regime_shift_probability": 0.35,
        "confidence": 0.8,
        "shadow_execution_eligible": True,
        "time_window": {
            "current_window": "lunch_lull",
            "confidence_multiplier": 0.8,
            "size_multiplier": 0.5,
            "risk_budget": {"max_new_positions": 10},
            "strategy_eligibility": {},
            "preferred_setups": [],
            "restrictions": {
                "watchlist_only_unless_exceptional_catalyst": True,
            },
            "thresholds": {
                "watchlist_min_confidence": 0.55,
                "apply_to_holding_periods": [
                    "day",
                    "intraday_scalp",
                    "intraday_momentum",
                ],
            },
            "shadow_execution_window_blocked": False,
        },
    }


def _scorecard_no_gate():
    return {
        "mode": "NORMAL",
        "regime_shift_probability": 0.1,
        "confidence": 0.9,
        "shadow_execution_eligible": True,
        "time_window": {
            "current_window": "power_hour",
            "confidence_multiplier": 1.0,
            "size_multiplier": 1.0,
            "risk_budget": {"max_new_positions": 10},
            "strategy_eligibility": {},
            "preferred_setups": [],
            "restrictions": {},
            "thresholds": {
                "watchlist_min_confidence": 0.55,
                "apply_to_holding_periods": [
                    "day",
                    "intraday_scalp",
                    "intraday_momentum",
                ],
            },
            "shadow_execution_window_blocked": False,
        },
    }


def test_lunch_gate_uses_source_holding_period_not_strategy_style(tmp_path: Path):
    package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis={
            "trade_ideas": [
                {
                    "symbol": "GLD",
                    "side": "long",
                    "historical_win_rate": 0.5,
                    "holding_period": "swing",
                    "strategy_style": "regime_playbook_day_trade",
                },
                {
                    "symbol": "SPY",
                    "side": "short",
                    "historical_win_rate": 0.5,
                    "holding_period": "day",
                    "strategy_style": "regime_playbook_medium_long",
                },
            ]
        },
        scorecard=_scorecard_lunch_lull(),
        microstructure={
            "GLD": {"last_price": 210.0},
            "SPY": {"last_price": 500.0},
        },
    )

    assert [c["symbol"] for c in package["candidates"]] == ["GLD"]
    assert package["blocked_candidates"] == [
        {
            "symbol": "SPY",
            "reason": "below catalyst threshold in lunch_lull",
            "confidence": 0.344,
            "holding_period": "day",
            "threshold": 0.55,
        }
    ]


def test_hard_to_borrow_uses_inverse_fallback_or_skips(tmp_path: Path):
    package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis={
            "trade_ideas": [
                {
                    "symbol": "HYG",
                    "side": "short",
                    "historical_win_rate": 0.5,
                    "holding_period": "swing",
                    "strategy_style": "regime_playbook_medium_long",
                },
                {
                    "symbol": "JETS",
                    "side": "short",
                    "historical_win_rate": 0.5,
                    "holding_period": "swing",
                    "strategy_style": "regime_playbook_medium_long",
                },
                {
                    "symbol": "IYT",
                    "side": "short",
                    "historical_win_rate": 0.5,
                    "holding_period": "swing",
                    "strategy_style": "regime_playbook_medium_long",
                },
            ]
        },
        scorecard=_scorecard_lunch_lull(),
        microstructure={
            "HYG": {"last_price": 77.0},
            "JETS": {"last_price": 20.0},
            "IYT": {"last_price": 65.0},
        },
    )

    assert [c["symbol"] for c in package["candidates"]] == ["SJB"]
    assert [c["side"] for c in package["candidates"]] == ["long"]
    assert package["candidates"][0]["metadata"]["inverse_fallback_used"] is True
    assert package["candidates"][0]["metadata"]["original_symbol"] == "HYG"
    assert all(symbol not in [c["symbol"] for c in package["candidates"]] for symbol in ("JETS", "IYT"))
    assert package["blocked_candidates"] == []


def test_packager_prefers_idea_level_confidence_scores(tmp_path: Path):
    package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis={
            "trade_ideas": [
                {
                    "symbol": "BBB",
                    "side": "long",
                    "historical_win_rate": 0.95,
                    "confidence_adjusted_score": 0.2,
                    "strategy": "NORMAL_to_ELEVATED",
                    "strategy_family": "day_trade",
                    "strategy_style": "regime_playbook_day_trade",
                    "holding_period": "day",
                },
                {
                    "symbol": "AAA",
                    "side": "long",
                    "historical_win_rate": 0.05,
                    "confidence_adjusted_score": 0.92,
                    "strategy": "NORMAL_to_ELEVATED",
                    "strategy_family": "day_trade",
                    "strategy_style": "regime_playbook_day_trade",
                    "holding_period": "day",
                },
            ]
        },
        scorecard=_scorecard_no_gate(),
        microstructure={
            "AAA": {"last_price": 10.0},
            "BBB": {"last_price": 10.0},
        },
    )

    by_symbol = {candidate["symbol"]: candidate for candidate in package["candidates"]}
    assert by_symbol["AAA"]["confidence_score"] > by_symbol["BBB"]["confidence_score"]
    assert by_symbol["AAA"]["metadata"]["confidence_source"] == "confidence_adjusted_score"


def test_packager_applies_strategy_learning_from_feedback_state(tmp_path: Path):
    feedback_path = tmp_path / "logs" / "execution" / "feedback_state.json"
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

    package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis={
            "trade_ideas": [
                {
                    "symbol": "ZIM",
                    "side": "long",
                    "historical_win_rate": 0.6,
                    "confidence_adjusted_score": 0.4,
                    "strategy": "shipping_rate_explosion",
                    "strategy_family": "medium_long",
                    "strategy_style": "regime_playbook_medium_long",
                    "holding_period": "swing",
                    "reason": "shipping rates continue to rise",
                }
            ]
        },
        scorecard=_scorecard_no_gate(),
        microstructure={
            "ZIM": {"last_price": 17.0},
        },
    )

    candidate = package["candidates"][0]
    assert candidate["learning_adjusted"] is True
    assert candidate["confidence_score"] > 0.4
    assert candidate["metadata"]["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"
    assert candidate["metadata"]["learning_adjustment_detail"]["strategy_family"] == "medium_long"


def test_packager_uses_strategy_family_when_style_missing(tmp_path: Path):
    scorecard = _scorecard_no_gate()
    scorecard["time_window"]["strategy_eligibility"] = {
        "medium_long": {
            "eligible": False,
            "reasons_blocked": ["family blocked"],
        }
    }

    package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis={
            "trade_ideas": [
                {
                    "symbol": "WEEK",
                    "side": "long",
                    "historical_win_rate": 0.7,
                    "confidence_adjusted_score": 0.6,
                    "strategy_family": "medium_long",
                    "holding_period": "swing",
                }
            ]
        },
        scorecard=scorecard,
        microstructure={
            "WEEK": {"last_price": 20.0},
        },
    )

    assert package["candidates"] == []
    assert package["blocked_candidates"] == [
        {
            "symbol": "WEEK",
            "reason": "strategy 'medium_long' blocked in power_hour",
            "block_reasons": ["family blocked"],
        }
    ]
