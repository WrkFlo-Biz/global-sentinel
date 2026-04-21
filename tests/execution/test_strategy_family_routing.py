from __future__ import annotations

from pathlib import Path

from src.execution.position_manager import PositionManager
from src.execution.strategy_manager import StrategyManager


def test_strategy_manager_split_uses_shared_family_inference(tmp_path: Path) -> None:
    manager = StrategyManager(repo_root=tmp_path)

    ideas = [
        {
            "symbol": "WEEK",
            "metadata": {"strategy_style": "regime_playbook_weekly"},
            "holding_period": "day",
        },
        {
            "symbol": "OVRN",
            "order_metadata": {"strategy_style": "regime_playbook_overnight"},
            "holding_period": "day",
        },
        {
            "symbol": "EVT",
            "strategy_style": "event_driven_breakout",
            "holding_period": "day",
        },
    ]

    buckets = manager.split_ideas_by_strategy(ideas)

    assert [idea["symbol"] for idea in buckets["medium_long"]] == ["WEEK", "OVRN"]
    assert [idea["symbol"] for idea in buckets["day_trade"]] == ["EVT"]


def test_position_manager_maps_weekly_overnight_and_family_overrides(tmp_path: Path) -> None:
    manager = PositionManager(repo_root=tmp_path)

    weekly = manager._get_strategy_params("weekly")
    overnight = manager._get_strategy_params("overnight")
    event_driven = manager._get_strategy_params("event_driven")
    family_override = manager._get_strategy_params("day", strategy_family="medium_long")

    assert weekly["strategy_name"] == "medium_long"
    assert overnight["strategy_name"] == "medium_long"
    assert event_driven["strategy_name"] == "day_trade"
    assert family_override["strategy_name"] == "medium_long"
