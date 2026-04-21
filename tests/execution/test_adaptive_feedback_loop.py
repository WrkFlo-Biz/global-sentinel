from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop


def _write_trade_history(repo_root: Path) -> None:
    history_path = repo_root / "logs" / "execution" / "performance_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    trades = [
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 180.0,
            "pnl_pct": 4.5,
            "win": True,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
                "signal_boost_detail": {"shipping": 0.05},
            },
            "order_metadata": {
                "signal_boost_detail": {"shipping": 0.05},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 95.0,
            "pnl_pct": 2.2,
            "win": True,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
                "signal_boost_detail": {"shipping": 0.05},
            },
            "order_metadata": {
                "signal_boost_detail": {"shipping": 0.05},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 60.0,
            "pnl_pct": 1.3,
            "win": True,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
                "signal_boost_detail": {"shipping": 0.05},
            },
            "order_metadata": {
                "signal_boost_detail": {"shipping": 0.05},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -40.0,
            "pnl_pct": -0.9,
            "win": False,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
                "signal_boost_detail": {"shipping": 0.05},
            },
            "order_metadata": {
                "signal_boost_detail": {"shipping": 0.05},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 70.0,
            "pnl_pct": 1.6,
            "win": True,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
                "signal_boost_detail": {"airline": -0.04},
            },
            "order_metadata": {
                "signal_boost_detail": {"airline": -0.04},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -50.0,
            "pnl_pct": -1.1,
            "win": False,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
                "signal_boost_detail": {"airline": -0.04},
            },
            "order_metadata": {
                "signal_boost_detail": {"airline": -0.04},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -80.0,
            "pnl_pct": -1.8,
            "win": False,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
                "signal_boost_detail": {"airline": -0.04},
            },
            "order_metadata": {
                "signal_boost_detail": {"airline": -0.04},
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -35.0,
            "pnl_pct": -0.8,
            "win": False,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
                "signal_boost_detail": {"airline": -0.04},
            },
            "order_metadata": {
                "signal_boost_detail": {"airline": -0.04},
            },
        },
    ]

    history_path.write_text(
        "\n".join(json.dumps(row) for row in trades) + "\n",
        encoding="utf-8",
    )


def test_feedback_loop_groups_by_exact_strategy_and_family(tmp_path: Path) -> None:
    repo_root = tmp_path
    _write_trade_history(repo_root)

    loop = AdaptiveFeedbackLoop(repo_root)
    result = loop.analyze_and_adjust()

    assert result["status"] == "active"
    assert result["trades_analyzed"] == 8
    assert result["strategy_stats"]["shipping_rate_explosion"]["sample_size"] == 4
    assert result["strategy_stats"]["airline_short"]["sample_size"] == 4

    strategy_conf = result["strategy_confidence_adjustments"]
    assert strategy_conf["shipping_rate_explosion"] > 0
    assert strategy_conf["medium_long"] > 0
    assert strategy_conf["airline_short"] < 0
    assert strategy_conf["day_trade"] < 0

    strategy_params = result["strategy_adjustments"]
    assert strategy_params["shipping_rate_explosion"]["profit_target_mult"] >= 1.0
    assert strategy_params["airline_short"]["profit_target_mult"] >= 1.0

    saved_state = json.loads((repo_root / "logs" / "execution" / "feedback_state.json").read_text(encoding="utf-8"))
    assert "shipping_rate_explosion" in saved_state["strategy_confidence_adjustments"]
    assert "medium_long" in saved_state["strategy_adjustments"]
