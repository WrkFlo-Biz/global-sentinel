from __future__ import annotations

from src.strategies import CommodityRegimeRotationStrategy


def test_commodity_regime_rotation_picks_leader_and_laggard() -> None:
    strategy = CommodityRegimeRotationStrategy()
    market_data = {
        "USO": {"price": 81.0, "change_pct": 2.4, "curve_state": "backwardation", "front_spread_pct": 0.6, "relative_volume": 1.7},
        "XLE": {"price": 95.0, "change_pct": 1.3, "curve_state": "backwardation", "front_spread_pct": 0.3, "relative_volume": 1.4},
        "GLD": {"price": 228.0, "change_pct": 0.4, "curve_state": "contango", "front_spread_pct": -0.1, "relative_volume": 1.0},
        "DBA": {"price": 23.0, "change_pct": -2.1, "curve_state": "contango", "front_spread_pct": -0.8, "relative_volume": 1.2},
        "WEAT": {"price": 5.8, "change_pct": -1.9, "curve_state": "contango", "front_spread_pct": -0.7, "relative_volume": 1.1},
        "CORN": {"price": 17.2, "change_pct": -1.6, "curve_state": "contango", "front_spread_pct": -0.6, "relative_volume": 1.0},
    }
    scorecard = {"v6_oil_regime": "SHOCK", "component_scores": {"commodity_shock": 0.52}}

    ideas = strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)

    assert ideas
    assert ideas[0]["strategy"] == "commodity_regime_rotation"
    assert ideas[0]["metadata"]["sector"] == "energy"
    assert ideas[0]["direction"] == "long"
