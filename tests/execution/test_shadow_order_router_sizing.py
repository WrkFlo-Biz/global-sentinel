from src.execution.shadow_order_router import ShadowOrderRouter


def test_notional_sizing_is_not_clipped_by_legacy_medium_long_cap():
    router = ShadowOrderRouter.__new__(ShadowOrderRouter)
    router._resolve_decision_price = lambda package, candidate: (210.0, "price_hints.decision_price")
    router._get_account_equity = lambda: 500000.0

    order_request = ShadowOrderRouter._candidate_to_order_request(
        router,
        package={"window_context": {"time_window_name": "post_lunch_reacceleration"}},
        candidate={
            "symbol": "GLD",
            "side": "buy",
            "direction": "bullish",
            "instrument_types": ["equity"],
            "confidence_score": 0.5,
            "size_multiplier_suggestion": 1.0,
            "fill_sim_assessment": {"expected_slippage_bps": 10.0},
            "execution_constraints": {},
        },
        strategy_config={
            "name": "medium_long",
            "holding_period": "swing",
            "time_in_force": "gtc",
            "extended_hours": False,
            "position_sizing": {
                "method": "notional_pct",
                "base_pct_of_equity": 5.0,
                "high_confidence_pct": 8.0,
                "max_single_position_pct": 12.0,
                "min_notional": 1500.0,
            },
        },
    )

    assert order_request["qty"] == 71
    assert order_request["qty"] > 30
    assert order_request["time_in_force"] == "gtc"
    assert order_request["_gs_sizing"]["sizing_method_used"] == "notional_pct"
    assert order_request["_gs_sizing"]["target_notional"] == 15000.0
    assert order_request["_gs_sizing"]["max_notional"] == 60000.0
    assert order_request["_gs_sizing"]["final_qty"] == order_request["qty"]
    assert order_request["_gs_sizing"]["qty_cap"] == 10000
    assert order_request["_gs_sizing"]["qty_cap_source"] == "notional_default"
