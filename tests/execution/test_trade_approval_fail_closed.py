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
                approval_id,
                event_type,
                timestamp,
                requesting_agent,
                decision,
                reason,
                fail_closed_trigger,
                approved,
                trade_details_json,
                metadata_json
            FROM trade_approval_audit
            ORDER BY event_id
            """
        ).fetchall()

    entries = []
    for row in rows:
        entries.append(
            {
                "approval_id": row["approval_id"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "requesting_agent": row["requesting_agent"],
                "decision": row["decision"],
                "reason": row["reason"],
                "fail_closed_trigger": row["fail_closed_trigger"],
                "approved": None if row["approved"] is None else bool(row["approved"]),
                "trade_details": json.loads(row["trade_details_json"]),
                "metadata": None if row["metadata_json"] is None else json.loads(row["metadata_json"]),
            }
        )
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


def test_poll_callback_query_blocks_invalid_decision_file(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    trade_approval.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    decision_file = trade_approval.PENDING_DIR / "abc123.decision"
    decision_file.write_text(json.dumps({"raw": "missing_decision"}))

    decision, raw = trade_approval._poll_callback_query("abc123", "token", 1)

    assert decision == "error"
    assert raw == "missing_decision"
    assert not decision_file.exists()


def test_resolve_pending_approval_normalizes_and_rejects_invalid_values(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    trade_approval.PENDING_DIR.mkdir(parents=True, exist_ok=True)

    pending_ok = trade_approval.PENDING_DIR / "good.pending"
    pending_ok.write_text("{}")
    assert trade_approval.resolve_pending_approval("good", "approve") is True
    written = json.loads((trade_approval.PENDING_DIR / "good.decision").read_text())
    assert written["decision"] == "approved"

    pending_bad = trade_approval.PENDING_DIR / "bad.pending"
    pending_bad.write_text("{}")
    assert trade_approval.resolve_pending_approval("bad", "maybe") is False
    assert not (trade_approval.PENDING_DIR / "bad.decision").exists()
