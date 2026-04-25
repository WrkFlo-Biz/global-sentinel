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

    detail = result["close_details"][0]

    assert result["approval_required"] is True
    assert result["manual_approval_required"] is True
    assert result["proposed_close_count"] == 1
    assert result["actions_taken"] == 0
    assert result["actions_executed"] == 0
    assert detail["status"] == "pending_manual_approval"
    assert detail["auto_close_blocked"] is True
    assert detail["approval_required"] is True
    assert detail["project"] == "global-sentinel"
    assert detail["kind"] == "gs.trade.execute_shadow"
    assert detail["ticket_id"].startswith("pm-test-")
    assert detail["target"] == f"global-sentinel/trade-ticket/{detail['ticket_id']}"
    assert detail["orchestrator_command"] == (
        "wrkflo-orchestrator approve --kind gs.trade.execute_shadow "
        f"--target {detail['target']} --reason \"<reason>\""
    )

    handoff = detail["orchestrator_handoff"]
    assert handoff["project"] == "global-sentinel"
    assert handoff["kind"] == "gs.trade.execute_shadow"
    assert handoff["target"] == detail["target"]
    assert handoff["requester"] == "position_manager"
    assert handoff["requester_kind"] == "scheduler"
    assert handoff["requester_id"] == "position_manager"
    assert handoff["requester_channel"] == "position_manager"
    assert handoff["requested_at"]
    assert handoff["ticket_id"] == detail["ticket_id"]
    assert handoff["ticket_hash"]
    assert handoff["strategy"] == "day_trade"
    assert handoff["account"] == "day_trade"
    assert handoff["symbol"] == "TEST"
    assert handoff["side"] == "sell"
    assert handoff["qty"] == 10.0
    assert handoff["asset_class"] == "equity"
    assert handoff["order_type"] == "market"
    assert handoff["time_in_force"] == "day"
    assert handoff["source_surface"] == "position_manager"
    assert handoff["close_reason"] == "take_profit"
    assert handoff["position_side"] == "long"


def test_position_manager_guardrail_blocks_explicit_auto_close(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(position_manager_module, "TelegramNotifier", None)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    manager = PositionManager(repo_root=tmp_path, auto_close_enabled=True)

    assert manager.require_human_approval_for_closures is True
    assert manager.auto_close_enabled is False


def test_position_manager_short_close_handoff_uses_buy_side(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(position_manager_module, "TelegramNotifier", None)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    manager = PositionManager(repo_root=tmp_path)
    manager._notifier = None
    manager._get_open_positions = lambda: [
        {
            "symbol": "TSLA",
            "qty": "-7",
            "side": "short",
            "unrealized_plpc": -0.02,
            "unrealized_pl": -140.0,
            "avg_entry_price": 200.0,
            "current_price": 204.0,
        }
    ]
    manager._check_portfolio_drawdown = lambda positions: {"drawdown_pct": 0.0, "emergency_liquidate": False}
    manager._load_order_history = lambda: {}
    manager._close_position = lambda *args, **kwargs: {
        "status": "should-not-run",
    }

    result = manager.run_check()

    assert result["proposed_close_count"] == 1
    assert result["approval_required"] is True
    assert result["close_details"][0]["reason"] == "stop_loss"
    assert result["close_details"][0]["orchestrator_handoff"]["side"] == "buy"
    assert result["close_details"][0]["orchestrator_handoff"]["qty"] == 7.0
    assert result["close_details"][0]["orchestrator_handoff"]["position_side"] == "short"
