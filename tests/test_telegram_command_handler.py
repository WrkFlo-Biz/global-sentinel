from __future__ import annotations

import json
from pathlib import Path

from src.monitoring.telegram_bot_manager import TelegramBotManager
from src.monitoring.telegram_command_handler import TelegramCommandHandler


def test_dispatch_command_accepts_gs_prefix_with_bot_suffix(tmp_path: Path, monkeypatch):
    handler = TelegramCommandHandler(
        bot_token="test-token",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )
    sent: list[tuple[str, str]] = []

    monkeypatch.setattr(
        handler,
        "_send_message",
        lambda chat_id, text, parse_mode="": sent.append((chat_id, text)),
    )
    monkeypatch.setattr(handler, "_cmd_status", lambda args, chat_id: "SYSTEM STATUS")
    handler._commands["status"] = handler._cmd_status

    handler._dispatch_command("-1003898688720", "/gs_status@mo2darkbot")

    assert sent == [("-1003898688720", "SYSTEM STATUS")]


def test_tier_2_commands_return_orchestrator_stub_without_mutation(tmp_path: Path, monkeypatch):
    handler = TelegramCommandHandler(
        bot_token="test-token",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )

    def fail(*args, **kwargs):
        raise AssertionError("demoted command should not hit legacy mutation path")

    monkeypatch.setattr(handler, "_dashboard_post", fail)
    monkeypatch.setattr(handler, "_request_confirmation", fail)
    monkeypatch.setattr(handler, "_log_command", fail)

    assert handler._cmd_mode("auto day_trade", "-1003898688720") == handler.ORCHESTRATOR_MODE_APPROVAL_MESSAGE
    assert handler._cmd_kill("on emergency", "-1003898688720") == handler.ORCHESTRATOR_KILL_APPROVAL_MESSAGE
    assert handler._cmd_veto("on block", "-1003898688720") == handler.ORCHESTRATOR_VETO_APPROVAL_MESSAGE
    assert handler._cmd_approve("", "-1003898688720") == handler.ORCHESTRATOR_TRADE_APPROVAL_MESSAGE
    assert handler._cmd_reject("", "-1003898688720") == handler.ORCHESTRATOR_TRADE_APPROVAL_MESSAGE
    assert handler._cmd_refresh("", "-1003898688720") == handler.ORCHESTRATOR_REFRESH_APPROVAL_MESSAGE
    assert handler._execute_kill_switch(True, "emergency") == handler.ORCHESTRATOR_KILL_APPROVAL_MESSAGE
    assert handler._execute_veto(True, "block") == handler.ORCHESTRATOR_VETO_APPROVAL_MESSAGE

    assert "--target global-sentinel/control/execution-mode/day_trade/manual" in handler.ORCHESTRATOR_MODE_APPROVAL_MESSAGE
    assert "--target global-sentinel/control/kill-switch/on" in handler.ORCHESTRATOR_KILL_APPROVAL_MESSAGE
    assert "--target global-sentinel/control/manual-veto/on" in handler.ORCHESTRATOR_VETO_APPROVAL_MESSAGE
    assert "--target global-sentinel/trade-ticket/<ticket_id>" in handler.ORCHESTRATOR_TRADE_APPROVAL_MESSAGE

    assert not (tmp_path / "control" / "kill_switch.json").exists()
    assert not (tmp_path / "control" / "manual_veto.json").exists()


def test_help_lists_scoped_guarded_target_examples(tmp_path: Path) -> None:
    handler = TelegramCommandHandler(
        bot_token="test-token",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )

    help_text = handler._cmd_help("", "-1003898688720")

    assert "--target global-sentinel" not in help_text
    assert "gs.control.execution_mode.set -> global-sentinel/control/execution-mode/day_trade/manual" in help_text
    assert "gs.control.kill_switch.set -> global-sentinel/control/kill-switch/on" in help_text
    assert "gs.control.manual_veto.set -> global-sentinel/control/manual-veto/on" in help_text
    assert "gs.trade.execute_shadow -> global-sentinel/trade-ticket/<ticket_id>" in help_text


def test_log_unauthorized_chat_records_private_chat_metadata(tmp_path: Path):
    handler = TelegramCommandHandler(
        bot_token="test-token",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )

    handler._log_unauthorized_chat(
        {
            "chat": {"id": 123456789, "type": "private"},
            "from": {"id": 42, "username": "moses", "first_name": "Moses"},
            "text": "/gs_status",
            "message_thread_id": None,
        }
    )

    log_path = tmp_path / "logs" / "notifications" / "telegram_unauthorized.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 1
    assert rows[0]["event_type"] == "unauthorized_chat_attempt"
    assert rows[0]["chat_id"] == "123456789"
    assert rows[0]["chat_type"] == "private"
    assert rows[0]["from_username"] == "moses"
    assert rows[0]["text_preview"] == "/gs_status"


def test_bot_manager_parses_extra_darkbot_chat_ids(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1003898688720")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_DARKBOT", "-1003898688720")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS_DARKBOT", "123456789, 987654321")
    monkeypatch.delenv("TELEGRAM_CHAT_ID_DRKBOT", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_IDS_DRKBOT", raising=False)

    manager = TelegramBotManager(tmp_path)

    assert manager.darkbot_chat_ids == {"-1003898688720", "123456789", "987654321"}
