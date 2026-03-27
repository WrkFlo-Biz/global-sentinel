from src.risk.buying_power import BuyingPowerTracker


def test_buying_power_tracker():
    tracker = BuyingPowerTracker()
    state = tracker.compute(
        {
            "equity": 100000,
            "cash": 50000,
            "market_value_long": 20000,
            "market_value_short": 5000,
            "maintenance_margin": 10000,
            "buying_power": 75000,
        },
        [{"qty": 10, "limit_price": 100.0}],
    )
    fit = tracker.will_this_order_fit({"qty": 200, "limit_price": 100.0}, state)
    assert state["effective_buying_power"] == 74000
    assert fit["fits"]
