from src.execution.slippage_model import SlippageModel


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
