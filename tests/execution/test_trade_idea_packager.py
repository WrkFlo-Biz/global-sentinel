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


def test_lunch_gate_uses_source_holding_period_not_strategy_style():
    package = TradeIdeaPackager().build_package(
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


def test_hard_to_borrow_uses_inverse_fallback_or_skips():
    package = TradeIdeaPackager().build_package(
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
