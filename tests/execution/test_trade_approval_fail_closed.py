from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import src.execution.trade_approval as trade_approval


def _order(notional: float = 1000.0, **overrides: object) -> dict[str, object]:
    order = {
        "symbol": "NVDA",
        "side": "buy",
        "qty": 1,
        "limit_price": 1000.0,
        "notional": notional,
        "signal_source": "unit-test-signal",
        "requesting_agent": "unit-test-agent",
        "strategy_style": "momentum_day_trade",
        "account": "day_trade",
        "ticket_id": "ticket-123",
        "approval_token": "approve-token",
        "approval_jti": "approval-jti-123",
        "approval_issued_by": "moses",
        "approval_reason": "approve test trade",
        "approval_exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        "requester_kind": "scheduler",
        "requester_id": "unit-test-agent",
        "requester_channel": "pytest",
        "source_surface": "pytest",
        "time_in_force": "day",
        "order_type": "limit",
    }
    order.update(overrides)
    if "ticket_hash" not in overrides:
        order["ticket_hash"] = trade_approval._ticket_hash(order, str(order["ticket_id"]))
    return order


def _set_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        trade_approval,
        "APPROVAL_LOG_PATH",
        tmp_path / "trade_approvals.jsonl",
    )
    monkeypatch.setattr(trade_approval, "PENDING_DIR", tmp_path / "pending")


def _set_enabled_env(monkeypatch) -> None:
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")
    monkeypatch.delenv("ORCHESTRATOR_TASK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TRADE_APPROVAL_TIMEOUT", raising=False)


def _audit_entries(tmp_path: Path) -> list[dict]:
    log_path = tmp_path / "trade_approvals.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_terminal_audit(
    tmp_path: Path,
    result: dict[str, object],
    reason_substring: str,
    fail_closed_trigger: str | None,
) -> list[dict]:
    file_entries = _audit_entries(tmp_path)

    assert len(file_entries) == 2
    assert [entry["event_type"] for entry in file_entries] == [
        "approval_requested",
        "approval_decision",
    ]

    request_entry = file_entries[0]
    decision_entry = file_entries[1]

    assert request_entry["schema_version"] == trade_approval.APPROVAL_AUDIT_SCHEMA_VERSION
    assert request_entry["approval_id"] == decision_entry["approval_id"]
    assert request_entry["decision"] == "requested"
    assert request_entry["reason"] == "trade approval requested"
    assert request_entry["approved"] is None
    assert request_entry["requesting_agent"] == "unit-test-agent"
    assert request_entry["trade_details"]["symbol"] == "NVDA"
    assert request_entry["trade_details"]["side"] == "buy"
    assert request_entry["trade_details"]["signal_source"] == "unit-test-signal"
    assert request_entry["metadata"]["mediation"] == "orchestrator"
    assert request_entry["metadata"]["telegram_transport_disabled"] is True
    assert request_entry["metadata"]["legacy_pending_file_bridge_disabled"] is True

    assert decision_entry["schema_version"] == trade_approval.APPROVAL_AUDIT_SCHEMA_VERSION
    assert decision_entry["approval_id"] == request_entry["approval_id"]
    assert decision_entry["approved"] is result["approved"]
    assert decision_entry["decision"] == result["decision"]
    assert reason_substring in decision_entry["reason"]
    assert decision_entry["fail_closed_trigger"] == fail_closed_trigger
    assert decision_entry["requesting_agent"] == "unit-test-agent"
    assert decision_entry["trade_details"]["symbol"] == "NVDA"
    assert decision_entry["trade_details"]["side"] == "buy"
    assert not (tmp_path / "state.db").exists()

    return file_entries


def test_request_approval_blocks_when_disabled(tmp_path: Path, monkeypatch, caplog) -> None:
    _set_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "false")

    with caplog.at_level(logging.WARNING, logger="global_sentinel.trade_approval"):
        result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "disabled"
    assert "Trade blocked" in caplog.text
    _assert_terminal_audit(
        tmp_path,
        result,
        "TRADE_APPROVAL_ENABLED is false",
        "approval_disabled",
    )


def test_request_approval_blocks_below_threshold(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)

    result = trade_approval.request_approval(_order(notional=100.0))

    assert result["approved"] is False
    assert result["decision"] == "below_threshold"
    _assert_terminal_audit(
        tmp_path,
        result,
        "blocking without explicit approval",
        "minimum_notional_gate",
    )


def test_request_approval_blocks_missing_guarded_context(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(
        trade_approval,
        "submit_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit_task should not run")),
    )

    result = trade_approval.request_approval(_order(approval_jti=None))

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Missing orchestrator approval context" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Missing orchestrator approval context",
        "missing_guarded_context",
    )


def test_request_approval_blocks_stale_approval_context(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)

    result = trade_approval.request_approval(
        _order(
            approval_exp=int(
                (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()
            )
        )
    )

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Stale orchestrator approval context" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Stale orchestrator approval context",
        "stale_guarded_context",
    )


def test_request_approval_blocks_missing_ticket_hash(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setattr(
        trade_approval,
        "submit_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit_task should not run")),
    )

    result = trade_approval.request_approval(_order(ticket_hash=None))

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Missing orchestrator approval context: ticket_hash" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Missing orchestrator approval context: ticket_hash",
        "missing_guarded_context",
    )


def test_request_approval_blocks_target_mismatch(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)

    result = trade_approval.request_approval(
        _order(target="global-sentinel/trade-ticket/other-ticket")
    )

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Guarded trade target mismatch" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Guarded trade target mismatch",
        "guarded_target_mismatch",
    )


def test_request_approval_blocks_ticket_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)

    result = trade_approval.request_approval(_order(ticket_hash="wrong-hash"))

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Guarded trade ticket hash mismatch" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Guarded trade ticket hash mismatch",
        "guarded_ticket_hash_mismatch",
    )


def test_request_approval_blocks_invalid_config_and_logs_rejection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    monkeypatch.setenv("TRADE_APPROVAL_TIMEOUT", "bad-timeout")

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Invalid trade approval config" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Invalid trade approval config",
        "invalid_approval_config",
    )


def test_request_approval_blocks_orchestrator_submission_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)

    def fake_submit_task(*args, **kwargs):
        raise trade_approval.OrchestratorTaskClientError(
            "orchestrator returned HTTP 403: approval token required"
        )

    monkeypatch.setattr(trade_approval, "submit_task", fake_submit_task)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is False
    assert result["decision"] == "error"
    assert "Orchestrator mediation failed" in result["reason"]
    _assert_terminal_audit(
        tmp_path,
        result,
        "Orchestrator mediation failed",
        "orchestrator_submission_failed",
    )


def test_request_approval_submits_guarded_task_and_logs_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    captured: dict[str, object] = {}

    def fake_submit_task(
        payload: dict[str, object],
        *,
        bearer_token: str = "",
        base_url: str = "",
        timeout: float = 0.0,
    ) -> dict[str, object]:
        captured["payload"] = payload
        captured["bearer_token"] = bearer_token
        captured["base_url"] = base_url
        captured["timeout"] = timeout
        return {"run_id": "run-123", "status": "queued"}

    monkeypatch.setattr(trade_approval, "submit_task", fake_submit_task)

    result = trade_approval.request_approval(_order())

    assert result["approved"] is True
    assert result["decision"] == "approved"
    entries = _assert_terminal_audit(
        tmp_path,
        result,
        "Orchestrator accepted guarded trade execution",
        None,
    )

    assert captured["bearer_token"] == "approve-token"
    assert captured["base_url"] == ""
    assert captured["timeout"] == trade_approval.DEFAULT_TASK_TIMEOUT_SECONDS
    payload = captured["payload"]
    assert payload["project"] == "global-sentinel"
    assert payload["kind"] == "gs.trade.execute_shadow"
    assert payload["target"] == "global-sentinel/trade-ticket/ticket-123"
    assert payload["ticket_id"] == "ticket-123"
    assert payload["ticket_hash"] == _order()["ticket_hash"]
    assert payload["symbol"] == "NVDA"
    assert payload["side"] == "buy"
    assert payload["qty"] == 1
    assert payload["notional"] == 1000.0
    assert payload["requester_kind"] == "scheduler"
    assert payload["requester_id"] == "unit-test-agent"
    assert payload["requester_channel"] == "pytest"
    assert payload["approval_jti"] == "approval-jti-123"
    assert payload["approval_exp"]
    assert payload["approval_issued_by"] == "moses"
    assert payload["approval_context"]["approval_jti"] == "approval-jti-123"
    assert payload["approval_context"]["approval_issued_by"] == "moses"

    decision_entry = entries[-1]
    assert decision_entry["metadata"]["run_id"] == "run-123"
    assert decision_entry["metadata"]["target"] == "global-sentinel/trade-ticket/ticket-123"
    assert decision_entry["metadata"]["approval_jti"] == "approval-jti-123"


def test_request_approval_accepts_nested_approval_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_paths(tmp_path, monkeypatch)
    _set_enabled_env(monkeypatch)
    captured: dict[str, object] = {}

    def fake_submit_task(
        payload: dict[str, object],
        *,
        bearer_token: str = "",
        base_url: str = "",
        timeout: float = 0.0,
    ) -> dict[str, object]:
        captured["payload"] = payload
        captured["bearer_token"] = bearer_token
        return {"run_id": "run-nested", "status": "queued"}

    monkeypatch.setattr(trade_approval, "submit_task", fake_submit_task)
    order = _order(
        approval_token=None,
        approval_jti=None,
        approval_issued_by=None,
        approval_reason=None,
        approval_exp=None,
        approval_context={
            "approval_token": "nested-token",
            "approval_jti": "nested-jti",
            "approval_issued_by": "nested-human",
            "approval_reason": "nested approval",
            "approval_exp": int(
                (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
            ),
        },
    )

    result = trade_approval.request_approval(order)

    assert result["approved"] is True
    assert captured["bearer_token"] == "nested-token"
    assert captured["payload"]["approval_jti"] == "nested-jti"
    assert captured["payload"]["approval_issued_by"] == "nested-human"


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
    assert "Telegram" in caplog.text
    assert not (trade_approval.PENDING_DIR / "good.decision").exists()


def test_get_pending_approvals_returns_empty_when_legacy_files_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_paths(tmp_path, monkeypatch)
    trade_approval.PENDING_DIR.mkdir(parents=True, exist_ok=True)
    (trade_approval.PENDING_DIR / "old.pending").write_text("{}")

    assert trade_approval.get_pending_approvals() == []
