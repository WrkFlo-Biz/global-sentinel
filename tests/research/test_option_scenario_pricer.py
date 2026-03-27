"""Tests for option scenario pricer."""

from src.research.option_scenario_pricer import OptionScenarioInput, OptionScenarioPricer


def test_option_scenario_pricer_put_upside_from_down_move():
    pricer = OptionScenarioPricer()
    inp = OptionScenarioInput(
        symbol="SPY",
        underlying_price=500.0,
        strike=490.0,
        premium=5.0,
        option_type="put",
        contracts=1,
    )

    result = pricer.price_scenario(inp, scenario_move_pct=-0.05)

    assert result.symbol == "SPY"
    assert result.option_type == "put"
    assert result.scenario_underlying_price < 500.0
    assert result.intrinsic_value > 0.0
    assert result.pnl > 0.0
