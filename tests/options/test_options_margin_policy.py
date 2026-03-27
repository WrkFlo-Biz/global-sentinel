"""Tests for ``src.options.options_margin_policy``."""
from src.options.options_margin_policy import OptionsMarginPolicy


def test_covered_call_passes_with_sufficient_shares():
    policy = OptionsMarginPolicy()
    result = policy.check_margin(
        {
            "strategy_type": "covered_call",
            "shares_owned": 100,
            "legs": [{"side": "sell", "contract_type": "call", "quantity": 1, "strike": 500, "bid": 1.0, "ask": 1.2}],
        },
        account_equity=50_000,
    )
    assert result["pass"] is True
    assert result["reason"] == "covered_call_margin_ok"


def test_covered_call_fails_with_insufficient_shares():
    policy = OptionsMarginPolicy()
    result = policy.check_margin(
        {
            "strategy_type": "covered_call",
            "shares_owned": 0,
            "legs": [{"side": "sell", "contract_type": "call", "quantity": 1, "strike": 500, "bid": 1.0, "ask": 1.2}],
        },
        account_equity=50_000,
    )
    assert result["pass"] is False
    assert result["reason"] == "insufficient_shares_for_covered_call"


def test_vertical_spread_margin_passes():
    policy = OptionsMarginPolicy(max_margin_fraction=0.50)
    result = policy.check_margin(
        {
            "strategy_type": "vertical_spread",
            "legs": [
                {"side": "buy", "contract_type": "call", "quantity": 1, "strike": 500, "bid": 2.0, "ask": 2.2},
                {"side": "sell", "contract_type": "call", "quantity": 1, "strike": 510, "bid": 0.9, "ask": 1.0},
            ],
        },
        account_equity=10_000,
    )
    assert result["pass"] is True
    assert result["required_margin"] > 0


def test_naked_position_fails_equity_minimum():
    policy = OptionsMarginPolicy(min_naked_equity=25_000)
    result = policy.check_margin(
        {
            "strategy_type": "naked_call",
            "legs": [{"side": "sell", "contract_type": "call", "quantity": 1, "strike": 200, "underlying_price": 205}],
        },
        account_equity=10_000,
    )
    assert result["pass"] is False
    assert result["reason"] == "account_equity_below_naked_minimum"


def test_naked_position_fails_margin_fraction():
    policy = OptionsMarginPolicy(min_naked_equity=25_000, max_margin_fraction=0.10, naked_short_margin_ratio=0.30)
    result = policy.check_margin(
        {
            "strategy_type": "naked_put",
            "legs": [{"side": "sell", "contract_type": "put", "quantity": 2, "strike": 400, "underlying_price": 395}],
        },
        account_equity=30_000,
    )
    assert result["pass"] is False
    assert result["reason"] == "required_margin_exceeds_policy_fraction"
