from __future__ import annotations

from src.research.portfolio_constraint_builder import PortfolioConstraintBuilder


def test_portfolio_constraints_reflect_session_and_window_risk():
    builder = PortfolioConstraintBuilder()

    regular = builder.build(
        objective_type="hedge_basket_optimization",
        runtime_flags={"incident_mode": False},
        time_window_state={
            "window": "mid_morning",
            "impact_multiplier": 1.0,
            "confidence_multiplier": 1.0,
        },
        regime_state={"regime_shift_probability": 0.40},
        session_context={"session": "regular", "intraday_phase": "midday"},
    )

    overnight = builder.build(
        objective_type="hedge_basket_optimization",
        runtime_flags={"incident_mode": False},
        time_window_state={
            "window": "overnight",
            "impact_multiplier": 1.0,
            "confidence_multiplier": 1.0,
        },
        regime_state={"regime_shift_probability": 0.40},
        session_context={"session": "overnight", "intraday_phase": "overnight"},
    )

    opening = builder.build(
        objective_type="hedge_basket_optimization",
        runtime_flags={"incident_mode": False},
        time_window_state={
            "window": "opening_range_breakout_window",
            "impact_multiplier": 1.0,
            "confidence_multiplier": 1.0,
        },
        regime_state={"regime_shift_probability": 0.40},
        session_context={"session": "regular", "intraday_phase": "opening"},
    )

    watchlist = builder.build(
        objective_type="hedge_basket_optimization",
        runtime_flags={"incident_mode": False},
        time_window_state={
            "window": "close_exhaustion_watch",
            "impact_multiplier": 1.0,
            "confidence_multiplier": 1.0,
            "watchlist_only_window": True,
            "shadow_execution_window_blocked": True,
        },
        regime_state={"regime_shift_probability": 0.40},
        session_context={"session": "regular", "intraday_phase": "midday"},
    )

    assert overnight["max_participation_rate"] < regular["max_participation_rate"]
    assert opening["impact_budget_bps"] > regular["impact_budget_bps"]
    assert watchlist["max_names"] <= 4
    assert watchlist["max_participation_rate"] < regular["max_participation_rate"]
    assert overnight["session"] == "overnight"
    assert opening["intraday_phase"] == "opening"
