"""Tests for trade outcome evaluation logic."""

from src.research.evaluate_trade_outcomes import evaluate
from src.research.research_score_writer import build_research_score
from src.research.option_scenario_pricer import OptionScenarioPricer, OptionScenarioInput
from src.research.attach_research_score_to_snapshot import attach_research_score


def test_evaluate_trade_outcomes_basic():
    classical = {
        "request_id": "req-1",
        "package_id": "pkg-1",
        "ranked_solutions": [
            {"symbol": "XOM", "direction": "long"},
            {"symbol": "LMT", "direction": "long"},
        ],
    }
    quantum = {
        "request_id": "req-1",
        "package_id": "pkg-1",
        "ranked_solutions": [
            {"symbol": "GLD", "direction": "long"},
            {"symbol": "XOM", "direction": "long"},
        ],
    }
    outcomes = {
        "trades": [
            {"symbol": "XOM", "trade_executed": True, "realized_return_bps": 100},
            {"symbol": "LMT", "trade_executed": True, "realized_return_bps": 20},
            {"symbol": "GLD", "trade_executed": True, "realized_return_bps": 80},
        ]
    }

    result = evaluate(
        classical_result=classical,
        quantum_result=quantum,
        trade_outcomes=outcomes,
    )

    assert result["request_id"] == "req-1"
    assert result["winner"] in {"classical", "quantum", "tie"}
    assert result["classical_overlap_score"] >= 0.0
    assert result["quantum_overlap_score"] >= 0.0


def test_evaluate_quantum_wins():
    classical = {
        "request_id": "req-2",
        "ranked_solutions": [{"symbol": "LMT", "direction": "long"}],
    }
    quantum = {
        "request_id": "req-2",
        "ranked_solutions": [{"symbol": "XOM", "direction": "long"}],
    }
    outcomes = {
        "trades": [
            {"symbol": "XOM", "trade_executed": True, "realized_return_bps": 300},
            {"symbol": "LMT", "trade_executed": True, "realized_return_bps": -50},
        ]
    }

    result = evaluate(classical_result=classical, quantum_result=quantum, trade_outcomes=outcomes)
    assert result["winner"] == "quantum"
    assert result["quantum_realized_return_bps_sum"] > result["classical_realized_return_bps_sum"]


def test_option_scenario_pricer_call():
    pricer = OptionScenarioPricer()
    inp = OptionScenarioInput(
        symbol="SPY", underlying_price=500.0, strike=510.0,
        premium=5.0, option_type="call", contracts=1,
    )
    result = pricer.price_scenario(inp, scenario_move_pct=0.05)
    assert result.scenario_underlying_price == 525.0
    assert result.intrinsic_value == 15.0
    assert result.pnl == (15.0 - 5.0) * 100


def test_option_scenario_pricer_put():
    pricer = OptionScenarioPricer()
    inp = OptionScenarioInput(
        symbol="SPY", underlying_price=500.0, strike=490.0,
        premium=4.0, option_type="put", contracts=2,
    )
    result = pricer.price_scenario(inp, scenario_move_pct=-0.05)
    assert result.scenario_underlying_price == 475.0
    assert result.intrinsic_value == 15.0
    assert result.pnl == (15.0 - 4.0) * 2 * 100


def test_option_scenario_grid():
    pricer = OptionScenarioPricer()
    inp = OptionScenarioInput(
        symbol="QQQ", underlying_price=400.0, strike=410.0,
        premium=6.0, option_type="call",
    )
    grid = pricer.price_grid(inp, [-0.10, -0.05, 0.0, 0.05, 0.10])
    assert len(grid) == 5
    assert grid[0]["pnl"] < grid[4]["pnl"]  # call gains on upside


def test_attach_research_score():
    snapshot = {"timestamp_utc": "2026-03-07T00:00:00Z", "packet_count": 42}
    score = {
        "research_score": 0.72,
        "recommended_influence": "research_positive",
        "guardrails": {"not_for_direct_execution": True},
        "request_id": "req-1",
        "package_id": "pkg-1",
    }
    result = attach_research_score(snapshot, score)
    assert result["research_overlays"]["quantum_research_score"]["research_score"] == 0.72
    assert result["runtime_flags"]["quantum_direct_execution_forbidden"] is True
    assert result["packet_count"] == 42  # original data preserved
