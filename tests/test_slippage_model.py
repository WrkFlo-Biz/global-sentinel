from src.execution.slippage_model import SlippageModel, compute_global_net_ev_ranking


def test_slippage_model_estimate():
    estimate = SlippageModel().estimate(
        symbol="XLE",
        direction="long",
        quantity=100,
        order_type="market",
        market_data={
            "bid": 99.9,
            "ask": 100.1,
            "last_price": 100.0,
            "avg_daily_volume": 1_000_000,
            "realized_vol": 0.25,
            "vix": 30,
        },
    )
    assert estimate["total_expected_cost_bps"] > 0
    assert estimate["war_premium_bps"] > 0


def test_global_net_ev_ranking_prefers_higher_net_ev_over_confidence():
    high_conf_low_net = compute_global_net_ev_ranking(
        expected_edge_bps=40.0,
        expected_cost_bps=35.0,
        confidence_score=0.95,
        size_multiplier=1.0,
        fill_feasibility_score=0.9,
        fill_quality_score=0.9,
        session_liquidity_score=0.9,
        reject_risk_probability=0.01,
    )
    low_conf_high_net = compute_global_net_ev_ranking(
        expected_edge_bps=110.0,
        expected_cost_bps=25.0,
        confidence_score=0.30,
        size_multiplier=1.0,
        fill_feasibility_score=0.9,
        fill_quality_score=0.9,
        session_liquidity_score=0.9,
        reject_risk_probability=0.01,
    )

    assert low_conf_high_net["net_expected_value_bps"] > high_conf_low_net["net_expected_value_bps"]
    assert low_conf_high_net["ranking_score"] > high_conf_low_net["ranking_score"]
