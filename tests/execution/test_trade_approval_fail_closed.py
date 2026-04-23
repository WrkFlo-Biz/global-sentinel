from __future__ import annotations

import json
import logging
from pathlib import Path

import src.execution.trade_approval as trade_approval


def _order(notional: float = 1000.0) -> dict:
    return {
        "symbol": "NVDA",
        "side": "buy",
        "qty": 1,
        "limit_price": 1000.0,
        "notional": notional,
    }


def _set_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(trade_approval, "APPROVAL_LOG_PATH", tmp_path / "trade_approvals.jsonl")
    monkeypatch.setattr(trade_approval, "PENDING_DIR", tmp_path / "pending")


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


def _assert_rejection_logged(tmp_path: Path, result: dict, reason_substring: str) -> None:
    entries = _audit_entries(tmp_path)
    assert len(entries) == 1

    entry = entries[0]
    assert entry["approved"] is False
    assert entry["decision"] == result["decision"]
    assert reason_substring in entry["reason"]
    assert entry["order"]["symbol"] == "NVDA"
    assert entry["order"]["side"] == "buy"


def test_request_approval_blocks_when_disabled(tmp_path: Path, monkeypatch, caplog) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "false")

    with caplog.at_level(logging.WARNING, logger="global_sentinel.trade_approval"):
        result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "disabled"
    assert "Trade blocked" in caplog.text
    _assert_rejection_logged(tmp_path, result, "TRADE_APPROVAL_ENABLED is false")


def test_request_approval_blocks_below_threshold(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")

    result = trade_approval.request_approval(_order(notional=100.0))

    assert result["approved"] is False
    assert result["decision"] == "below_threshold"
    _assert_rejection_logged(tmp_path, result, "blocking without explicit approval")


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
    _assert_rejection_logged(tmp_path, result, "Missing Telegram config")


def test_request_approval_blocks_send_failure(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(trade_approval, "_send_with_buttons", lambda *args, **kwargs: None)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Failed to send Telegram approval request" in result["reason"]
    _assert_rejection_logged(tmp_path, result, "Failed to send Telegram approval request")


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
    _assert_rejection_logged(tmp_path, result, "blocking trade")


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
    _assert_rejection_logged(tmp_path, result, "Invalid approval decision")


def test_request_approval_blocks_message_format_failure(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(trade_approval, "_format_approval_message", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad payload")))

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Failed to build trade approval request" in result["reason"]
    _assert_rejection_logged(tmp_path, result, "Failed to build trade approval request")


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
        }
    )

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Invalid trade sizing" in result["reason"]
    _assert_rejection_logged(tmp_path, result, "Invalid trade sizing")


def test_request_approval_blocks_invalid_config_and_logs_rejection(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_TIMEOUT", "bad-timeout")

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Invalid trade approval config" in result["reason"]
    _assert_rejection_logged(tmp_path, result, "Invalid trade approval config")


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
    _assert_rejection_logged(tmp_path, result, "User rejected")


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
