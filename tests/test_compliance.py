from src.risk.compliance import ComplianceEngine


def test_compliance_pre_trade_check():
    engine = ComplianceEngine()
    result = engine.pre_trade_check(
        {
            "symbol": "XLE",
            "qty": 10,
            "limit_price": 100.0,
            "strategy": "oil_momentum_intraday",
            "direction": "long",
            "avg_daily_volume": 2_000_000,
        },
        {"equity": 100000, "positions": {}, "strategy_position_counts": {"oil_momentum_intraday": 0}},
        {
            "restricted_symbols": ["TSLA"],
            "max_single_name_pct": 0.10,
            "min_avg_daily_volume": 100000,
            "strategy_limits": {"oil_momentum_intraday": {"max_positions": 3}},
        },
    )
    assert result["passed"]


def test_compliance_restricted_symbol():
    engine = ComplianceEngine()
    result = engine.pre_trade_check(
        {"symbol": "TSLA", "qty": 1, "limit_price": 100.0},
        {"equity": 100000, "positions": {}},
        {"restricted_symbols": ["TSLA"]},
    )
    assert not result["passed"]
