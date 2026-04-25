from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import src.monitoring.telegram_command_handler as telegram_command_handler_module
from src.monitoring.telegram_bot_manager import TelegramBotManager
from src.monitoring.telegram_command_handler import TelegramCommandHandler


def test_dispatch_command_accepts_gs_prefix_with_bot_suffix(tmp_path: Path, monkeypatch):
    handler = TelegramCommandHandler(
        bot_token="",
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


def test_status_uses_control_status_booleans_and_execution_mode(tmp_path: Path, monkeypatch) -> None:
    handler = TelegramCommandHandler(
        bot_token="",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )
    calls: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        calls.append(path)
        if path == "/api/heartbeat":
            return {"mode": "NORMAL", "cycle": 17, "status": "healthy"}
        if path == "/api/scorecard/latest":
            return {
                "regime_shift_probability": 0.25,
                "confidence": 0.8,
                "gss_signal": {"signal": "GREEN"},
            }
        if path == "/api/control/status":
            return {
                "kill_switch": True,
                "manual_veto": False,
                "execution_mode": {"day_trade": "manual", "medium_long": "auto"},
            }
        if path == "/api/execution-mode":
            return {"execution_mode": {"day_trade": "fallback", "medium_long": "fallback"}}
        if path == "/api/portfolio":
            return {"positions": [{"symbol": "SPY"}], "equity": 123456.78}
        raise AssertionError(f"unexpected path: {path}")

    def fail_post(*_args, **_kwargs):
        raise AssertionError("status must remain read-only")

    monkeypatch.setattr(handler, "_dashboard_get", fake_get)
    monkeypatch.setattr(handler, "_dashboard_post", fail_post)

    status_text = handler._cmd_status("", "-1003898688720")

    assert calls == [
        "/api/heartbeat",
        "/api/scorecard/latest",
        "/api/control/status",
        "/api/execution-mode",
        "/api/portfolio",
    ]
    assert "/api/controls" not in calls
    assert "Mode: NORMAL" in status_text
    assert "Cycle: 17" in status_text
    assert "Status: healthy" in status_text
    assert "Regime P: 0.250" in status_text
    assert "Confidence: 0.800" in status_text
    assert "GSS Signal: GREEN" in status_text
    assert "Kill Switch: ACTIVE" in status_text
    assert "Manual Veto: OFF" in status_text
    assert "Day Trade: manual" in status_text
    assert "Medium/Long: auto" in status_text
    assert "Positions: 1" in status_text
    assert "Equity: $123,456.78" in status_text


def test_status_falls_back_to_execution_mode_when_control_status_lacks_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    handler = TelegramCommandHandler(
        bot_token="",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )
    calls: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        calls.append(path)
        if path == "/api/heartbeat":
            return {"mode": "NORMAL", "cycle": 17, "status": "healthy"}
        if path == "/api/scorecard/latest":
            return {"regime_shift_probability": 0.25, "confidence": 0.8}
        if path == "/api/control/status":
            return {"kill_switch": False, "manual_veto": True}
        if path == "/api/execution-mode":
            return {"execution_mode": {"day_trade": "manual", "medium_long": "auto"}}
        if path == "/api/portfolio":
            return {"positions": [], "equity": 50000.0}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(handler, "_dashboard_get", fake_get)

    status_text = handler._cmd_status("", "-1003898688720")

    assert calls == [
        "/api/heartbeat",
        "/api/scorecard/latest",
        "/api/control/status",
        "/api/execution-mode",
        "/api/portfolio",
    ]
    assert "Kill Switch: OFF" in status_text
    assert "Manual Veto: ACTIVE" in status_text
    assert "Day Trade: manual" in status_text
    assert "Medium/Long: auto" in status_text


def test_non_command_chat_routes_through_foundry_client_boundary(tmp_path: Path, monkeypatch) -> None:
    handler = TelegramCommandHandler(
        bot_token="",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )
    sent: list[tuple[str, str, str]] = []
    chat_actions: list[tuple[str, str]] = []
    captured: dict[str, object] = {}

    scorecard_path = tmp_path / "logs" / "scorecards" / "latest_signal.json"
    scorecard_path.parent.mkdir(parents=True, exist_ok=True)
    scorecard_path.write_text(
        json.dumps({"mode": "ELEVATED", "regime_shift_probability": 0.42}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        handler,
        "_send_message",
        lambda chat_id, text, parse_mode="": sent.append((chat_id, text, parse_mode)),
    )
    monkeypatch.setattr(
        handler,
        "_send_chat_action",
        lambda chat_id, action: chat_actions.append((chat_id, action)),
    )

    def fake_send_request(
        *,
        intent_type,
        target_role,
        operating_context,
        latency_class,
        trace_context,
        messages,
    ):
        captured["intent_type"] = intent_type
        captured["target_role"] = target_role
        captured["operating_context"] = dict(operating_context)
        captured["latency_class"] = latency_class
        captured["trace_context"] = dict(trace_context)
        captured["messages"] = [dict(message) for message in messages]
        return SimpleNamespace(output="<b>Planner reply</b>")

    monkeypatch.setattr(
        telegram_command_handler_module.foundry_client,
        "send_request",
        fake_send_request,
    )

    handler._dispatch_command("-1003898688720", "What is the market setup?")

    assert chat_actions == [("-1003898688720", "typing")]
    assert captured["intent_type"] == "telegram_freeform_chat"
    assert captured["target_role"] == "planner"
    assert captured["latency_class"] == "interactive"
    assert captured["operating_context"]["source"] == "telegram_command_handler"
    assert captured["operating_context"]["channel"] == "telegram"
    assert captured["operating_context"]["strategy"] == "day_trade"
    assert captured["operating_context"]["mode"] == "ELEVATED"
    assert captured["operating_context"]["regime_shift_probability"] == 0.42
    assert captured["trace_context"]["chat_id"] == "-1003898688720"
    assert captured["messages"][0]["role"] == "system"
    assert "Strategy: day_trade." in captured["messages"][0]["content"]
    assert captured["messages"][1]["role"] == "user"
    assert "[GS context: mode=ELEVATED, regime_shift_prob=0.42]" in captured["messages"][1]["content"]
    assert captured["messages"][1]["content"].endswith("What is the market setup?")
    assert sent == [("-1003898688720", "<b>Planner reply</b>", "HTML")]


def test_non_command_chat_returns_concise_llm_error_on_foundry_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    handler = TelegramCommandHandler(
        bot_token="",
        allowed_chat_ids={"-1003898688720"},
        strategy="day_trade",
        repo_root=tmp_path,
    )
    sent: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        handler,
        "_send_message",
        lambda chat_id, text, parse_mode="": sent.append((chat_id, text, parse_mode)),
    )
    monkeypatch.setattr(handler, "_send_chat_action", lambda chat_id, action: None)
    monkeypatch.setattr(
        telegram_command_handler_module.foundry_client,
        "send_request",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("planner offline")),
    )

    handler._dispatch_command("-1003898688720", "hello there")

    assert sent == [("-1003898688720", "⚠️ LLM error: planner offline", "")]


def test_tier_2_commands_return_orchestrator_stub_without_mutation(tmp_path: Path, monkeypatch):
    handler = TelegramCommandHandler(
        bot_token="",
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
        bot_token="",
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
        bot_token="",
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
