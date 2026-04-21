"""Tests for regime-conditioned optimizer."""

from src.research.regime_conditioned_optimizer import RegimeConditionedOptimizer


def test_regime_conditioned_optimizer_selects_hedge_objective_in_crisis():
    optimizer = RegimeConditionedOptimizer()

    candidate_universe = [
        {"symbol": "XOM", "score": 0.9, "event_score": 0.2, "theme": "energy", "sector": "Energy"},
        {"symbol": "LMT", "score": 0.8, "event_score": 0.2, "theme": "defense", "sector": "Industrials"},
        {"symbol": "NVDA", "score": 0.95, "event_score": 0.1, "theme": "ai", "sector": "Technology"},
    ]
    regime_state = {
        "regime_shift_probability": 0.82,
        "macro_state": "inflationary_stress",
        "geopolitical_state": "crisis",
    }
    market_micro = {
        "XOM": {"adv_shares": 15000000, "sigma_daily": 0.025},
        "LMT": {"adv_shares": 2000000, "sigma_daily": 0.020},
        "NVDA": {"adv_shares": 30000000, "sigma_daily": 0.035},
    }

    prepared = optimizer.prepare(
        candidate_universe=candidate_universe,
        regime_state=regime_state,
        market_microstructure=market_micro,
    )

    assert prepared["objective_type"] == "hedge_basket_optimization"
    assert len(prepared["candidate_universe"]) >= 1
    # NVDA (ai theme) should be filtered out for hedge objective
    symbols = [c["symbol"] for c in prepared["candidate_universe"]]
    assert "NVDA" not in symbols


def test_regime_conditioned_optimizer_selects_portfolio_in_growth():
    optimizer = RegimeConditionedOptimizer()

    candidate_universe = [
        {"symbol": "NVDA", "score": 0.95, "event_score": 0.1, "theme": "ai", "sector": "Technology"},
        {"symbol": "XOM", "score": 0.7, "event_score": 0.1, "theme": "energy", "sector": "Energy"},
    ]
    regime_state = {
        "regime_shift_probability": 0.2,
        "macro_state": "growth",
        "geopolitical_state": "monitoring",
    }
    market_micro = {
        "NVDA": {"adv_shares": 30000000, "sigma_daily": 0.035},
        "XOM": {"adv_shares": 15000000, "sigma_daily": 0.025},
    }

    prepared = optimizer.prepare(
        candidate_universe=candidate_universe,
        regime_state=regime_state,
        market_microstructure=market_micro,
    )

    assert prepared["objective_type"] == "portfolio_optimization"
    symbols = [c["symbol"] for c in prepared["candidate_universe"]]
    assert "NVDA" in symbols
