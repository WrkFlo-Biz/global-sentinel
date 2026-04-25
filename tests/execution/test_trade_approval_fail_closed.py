from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import src.execution.trade_approval as trade_approval


def _order(notional: float = 1000.0) -> dict:
    return {
        "symbol": "NVDA",
        "side": "buy",
        "qty": 1,
        "limit_price": 1000.0,
        "notional": notional,
        "signal_source": "unit-test-signal",
        "requesting_agent": "unit-test-agent",
    }


def _set_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(trade_approval, "APPROVAL_LOG_PATH", tmp_path / "trade_approvals.jsonl")
    monkeypatch.setattr(trade_approval, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setenv("OPENCLAW_STATE_DB_PATH", str(tmp_path / "state.db"))


def _set_enabled_env(monkeypatch) -> None:
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("TELEGRAM_TRADING_THREAD_ID", "0")


def _audit_entries(tmp_path: Path) -> list[dict]:
    log_path = tmp_path / "trade_approvals.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _audit_db_entries(tmp_path: Path) -> list[dict]:
    db_path = tmp_path / "state.db"
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                event_type,
                timestamp,
                agent_id,
                decision,
                reason,
                entry_json
            FROM audit_log
            ORDER BY audit_id
            """
        ).fetchall()

    entries = []
    for row in rows:
        entry = json.loads(row["entry_json"])
        assert row["event_type"] == entry["event_type"]
        assert row["timestamp"] == entry["timestamp"]
        assert row["agent_id"] == entry["agent_id"]
        assert row["decision"] == entry["decision"]
        assert row["reason"] == entry["reason"]
        entries.append(entry)
    return entries


def _assert_terminal_audit(
    tmp_path: Path,
    result: dict,
    reason_substring: str,
    fail_closed_trigger: str | None,
) -> None:
    file_entries = _audit_entries(tmp_path)
    db_entries = _audit_db_entries(tmp_path)

    assert len(file_entries) == 2
    assert len(db_entries) == 2

    assert [entry["event_type"] for entry in file_entries] == [
        "approval_requested",
        "approval_decision",
    ]
    assert [entry["event_type"] for entry in db_entries] == [
        "approval_requested",
        "approval_decision",
    ]

    request_entry = file_entries[0]
    decision_entry = file_entries[1]

    assert request_entry["schema_version"] == trade_approval.APPROVAL_AUDIT_SCHEMA_VERSION
    assert request_entry["approval_id"] == decision_entry["approval_id"]
    assert request_entry["decision"] == "requested"
    assert request_entry["reason"] == "trade approval requested"
    assert request_entry["requesting_agent"] == "unit-test-agent"
    assert request_entry["approved"] is None
    assert request_entry["trade_details"]["symbol"] == "NVDA"
    assert request_entry["trade_details"]["side"] == "buy"
    assert request_entry["trade_details"]["signal_source"] == "unit-test-signal"

    assert decision_entry["schema_version"] == trade_approval.APPROVAL_AUDIT_SCHEMA_VERSION
    assert decision_entry["approval_id"] == request_entry["approval_id"]
    assert decision_entry["approved"] is result["approved"]
    assert decision_entry["decision"] == result["decision"]
    assert reason_substring in decision_entry["reason"]
    assert decision_entry["fail_closed_trigger"] == fail_closed_trigger
    assert decision_entry["requesting_agent"] == "unit-test-agent"
    assert decision_entry["trade_details"]["symbol"] == "NVDA"
    assert decision_entry["trade_details"]["side"] == "buy"

    for file_entry, db_entry in zip(file_entries, db_entries):
        for key in (
            "approval_id",
            "event_type",
            "timestamp",
            "requesting_agent",
            "decision",
            "reason",
            "fail_closed_trigger",
            "approved",
            "trade_details",
            "metadata",
        ):
            assert db_entry[key] == file_entry[key]


def test_request_approval_blocks_when_disabled(tmp_path: Path, monkeypatch, caplog) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "false")

    with caplog.at_level(logging.WARNING, logger="global_sentinel.trade_approval"):
        result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "disabled"
    assert "Trade blocked" in caplog.text
    _assert_terminal_audit(tmp_path, result, "TRADE_APPROVAL_ENABLED is false", "approval_disabled")


def test_request_approval_blocks_below_threshold(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")

    result = trade_approval.request_approval(_order(notional=100.0))

    assert result["approved"] is False
    assert result["decision"] == "below_threshold"
    _assert_terminal_audit(tmp_path, result, "blocking without explicit approval", "minimum_notional_gate")


def test_request_approval_blocks_missing_telegram_config(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_TOPIC_CHAT_ID", raising=False)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Missing Telegram config" in result["reason"]
    _assert_terminal_audit(tmp_path, result, "Missing Telegram config", "missing_telegram_config")


def test_request_approval_blocks_send_failure(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(trade_approval, "_send_with_buttons", lambda *args, **kwargs: None)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Failed to send Telegram approval request" in result["reason"]
    _assert_terminal_audit(tmp_path, result, "Failed to send Telegram approval request", "telegram_send_failure")


def test_request_approval_blocks_timeout_even_when_auto_execute_is_true(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_AUTO_EXECUTE", "true")
    monkeypatch.setattr(trade_approval, "_send_with_buttons", lambda *args, **kwargs: 123)
    monkeypatch.setattr(trade_approval, "_poll_callback_query", lambda *args, **kwargs: ("timeout", None))
    monkeypatch.setattr(trade_approval, "_send_confirmation", lambda *args, **kwargs: None)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "timeout"
    _assert_terminal_audit(tmp_path, result, "blocking trade", "approval_timeout")


def test_request_approval_blocks_unknown_decision(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(trade_approval, "_send_with_buttons", lambda *args, **kwargs: 123)
    monkeypatch.setattr(trade_approval, "_poll_callback_query", lambda *args, **kwargs: ("maybe", "raw_text"))
    monkeypatch.setattr(trade_approval, "_send_confirmation", lambda *args, **kwargs: None)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Invalid approval decision" in result["reason"]
    _assert_terminal_audit(tmp_path, result, "Invalid approval decision", "invalid_approval_decision")


def test_request_approval_blocks_message_format_failure(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(
        trade_approval,
        "_format_approval_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad payload")),
    )

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Failed to build trade approval request" in result["reason"]
    _assert_terminal_audit(tmp_path, result, "Failed to build trade approval request", "approval_payload_build_error")


def test_request_approval_blocks_invalid_trade_sizing_and_logs_rejection(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")

    result = trade_approval.request_approval(
        {
            "symbol": "NVDA",
            "side": "buy",
            "qty": 1,
            "limit_price": 1000.0,
            "notional": "bad-notional",
            "signal_source": "unit-test-signal",
            "requesting_agent": "unit-test-agent",
        }
    )

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Invalid trade sizing" in result["reason"]
    _assert_terminal_audit(tmp_path, result, "Invalid trade sizing", "invalid_trade_sizing")


def test_request_approval_blocks_invalid_config_and_logs_rejection(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_TIMEOUT", "bad-timeout")

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Invalid trade approval config" in result["reason"]
    _assert_terminal_audit(tmp_path, result, "Invalid trade approval config", "invalid_approval_config")


def test_request_approval_logs_explicit_approval(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(trade_approval, "_send_with_buttons", lambda *args, **kwargs: 123)
    monkeypatch.setattr(trade_approval, "_poll_callback_query", lambda *args, **kwargs: ("approved", "manual_yes"))
    monkeypatch.setattr(trade_approval, "_send_confirmation", lambda *args, **kwargs: None)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is True
    assert result["decision"] == "approved"
    _assert_terminal_audit(tmp_path, result, "User approved", None)


def test_request_approval_logs_explicit_rejection(tmp_path: Path, monkeypatch, caplog) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(trade_approval, "_send_with_buttons", lambda *args, **kwargs: 123)
    monkeypatch.setattr(trade_approval, "_poll_callback_query", lambda *args, **kwargs: ("rejected", "manual_no"))
    monkeypatch.setattr(trade_approval, "_send_confirmation", lambda *args, **kwargs: None)

    with caplog.at_level(logging.WARNING, logger="global_sentinel.trade_approval"):
        result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "rejected"
    assert "Trade blocked" in caplog.text
    _assert_terminal_audit(tmp_path, result, "User rejected", None)


def test_format_approval_message_requires_inline_buttons_only() -> None:
    text = trade_approval._format_approval_message(_order(), timeout=60, auto_exec=False)

    assert "Use the inline approval buttons below" in text
    assert "reply YES/NO" not in text


def test_poll_callback_query_ignores_legacy_decision_file(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    trade_approval.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    decision_file = trade_approval.PENDING_DIR / "abc123.decision"
    decision_file.write_text(json.dumps({"decision": "approved", "raw": "legacy_file"}))

    decision, raw = trade_approval._poll_callback_query("abc123", "token", 0)

    assert decision == "timeout"
    assert raw is None


def test_resolve_pending_approval_rejects_legacy_local_file_bridge(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    _set_paths(tmp_path, monkeypatch)
    trade_approval.PENDING_DIR.mkdir(parents=True, exist_ok=True)

    pending_ok = trade_approval.PENDING_DIR / "good.pending"
    pending_ok.write_text("{}")

    with caplog.at_level(logging.WARNING, logger="global_sentinel.trade_approval"):
        assert trade_approval.resolve_pending_approval("good", "approve") is False

    assert "orchestrator approval tokens instead" in caplog.text
    assert not (trade_approval.PENDING_DIR / "good.decision").exists()


def test_get_pending_approvals_returns_empty_when_legacy_files_exist(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    trade_approval.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    (trade_approval.PENDING_DIR / "old.pending").write_text("{}")

    assert trade_approval.get_pending_approvals() == []
