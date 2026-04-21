from __future__ import annotations

from pathlib import Path

import src.execution.position_manager as position_manager_module
from src.execution.position_manager import PositionManager


def test_position_manager_proposes_closes_by_default_without_executing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(position_manager_module, "TelegramNotifier", None)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    manager = PositionManager(repo_root=tmp_path)
    manager._notifier = None

    assert manager.auto_close_enabled is False
    assert manager.require_human_approval_for_closures is True

    manager._get_open_positions = lambda: [
        {
            "symbol": "TEST",
            "qty": "10",
            "side": "long",
            "unrealized_plpc": 0.05,
            "unrealized_pl": 500.0,
            "avg_entry_price": 100.0,
            "current_price": 105.0,
        }
    ]
    manager._check_portfolio_drawdown = lambda positions: {"drawdown_pct": 0.0, "emergency_liquidate": False}
    manager._load_order_history = lambda: {}

    def _fail_close(*args, **kwargs):
        raise AssertionError("auto-close must not be called without approval")

    manager._close_position = _fail_close

    result = manager.run_check()

    assert result["manual_approval_required"] is True
    assert result["proposed_close_count"] == 1
    assert result["actions_taken"] == 0
    assert result["actions_executed"] == 0
    assert result["close_details"][0]["status"] == "pending_manual_approval"
    assert result["close_details"][0]["auto_close_blocked"] is True


def test_position_manager_guardrail_blocks_explicit_auto_close(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(position_manager_module, "TelegramNotifier", None)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    manager = PositionManager(repo_root=tmp_path, auto_close_enabled=True)

    assert manager.require_human_approval_for_closures is True
    assert manager.auto_close_enabled is False
