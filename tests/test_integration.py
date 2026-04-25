from __future__ import annotations

import json as jsonlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import src.execution.trade_approval as trade_approval
import src.inference.foundry_client as foundry_client
from src.execution.shadow_order_router import ShadowOrderRouter


APPROVAL_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
APPROVAL_ID = APPROVAL_UUID.hex[:12]


@pytest.fixture(autouse=True)
def _isolate_foundry_repo_env(monkeypatch) -> None:
    monkeypatch.setattr(foundry_client, "_load_repo_env", lambda: {})


def _httpx_response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", url),
        json=payload,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        jsonlib.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _trade_request(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "side": "buy",
        "direction": "long",
        "qty": 8,
        "limit_price": 187.5,
        "notional": 1500.0,
        "confidence_score": 0.82,
        "size_multiplier_suggestion": 1.0,
        "strategy": "integration_alpha",
        "strategy_style": "manual_approval",
        "strategy_family": "medium_long",
        "holding_period": "swing",
        "signal_source": "foundry_integration",
        "reason": f"{symbol} setup cleared the mocked thesis gate",
    }


def _request_trade_from_foundry(monkeypatch, trade_request: dict) -> tuple[foundry_client.FoundryResponse, dict[str, object]]:
    orchestrator_url = "http://router.test/v1/inference"
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _httpx_response(
            url,
            {
                "output": jsonlib.dumps(trade_request),
                "route": {
                    "provider": "foundry",
                    "model": "gpt-5-mini",
                    "latency_ms": 28,
                    "tokens": {"input": 64, "output": 24, "total": 88},
                },
                "trace_id": "trace-foundry-integration",
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="trade_request",
        target_role=foundry_client.TargetRole.PLANNER,
        operating_context={"mode": "NORMAL", "source": "integration_test"},
        latency_class="interactive",
        trace_context={"trace_id": "trace-request-1", "intent_id": "integration-trade-request"},
        messages=[
            {"role": "system", "content": "Return a single JSON trade request."},
            {"role": "user", "content": "Generate one trade request for the integration test."},
        ],
    )

    return response, captured


def _configure_trade_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRADE_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("TRADE_APPROVAL_TIMEOUT", "5")
    monkeypatch.setattr(trade_approval, "APPROVAL_LOG_PATH", tmp_path / "logs" / "trade_approvals.jsonl")
    monkeypatch.setattr(trade_approval, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(trade_approval.uuid, "uuid4", lambda: APPROVAL_UUID)


def _stub_guarded_submit(
    monkeypatch,
    *,
    run_id: str,
    status: str,
) -> dict[str, object]:
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
        return {"run_id": run_id, "status": status}

    monkeypatch.setattr(trade_approval, "submit_task", fake_submit_task)
    return captured


def _approval_order_info(trade_request: dict, *, guarded: bool) -> dict:
    order = {
        "symbol": trade_request["symbol"],
        "side": trade_request["side"],
        "qty": trade_request["qty"],
        "limit_price": trade_request["limit_price"],
        "notional": trade_request["notional"],
        "signal_source": trade_request["signal_source"],
        "strategy": trade_request["strategy"],
        "strategy_style": trade_request["strategy_style"],
        "strategy_family": trade_request["strategy_family"],
        "asset_class": "equity",
        "requesting_agent": "integration-test-agent",
        "requester_kind": "scheduler",
        "requester_id": "integration-test-agent",
        "requester_channel": "pytest",
        "source_surface": "integration_test",
        "order_type": "limit",
        "time_in_force": "day",
    }
    if guarded:
        symbol_slug = str(trade_request["symbol"]).lower()
        order.update(
            {
                "ticket_id": f"integration-{symbol_slug}-ticket",
                "approval_token": f"approval-token-{symbol_slug}",
                "approval_jti": f"approval-jti-{symbol_slug}",
                "approval_issued_by": "integration-human",
                "approval_reason": f"approve {trade_request['symbol']} integration trade",
                "approval_exp": int(
                    (datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()
                ),
            }
        )
        order["ticket_hash"] = trade_approval._ticket_hash(order, order["ticket_id"])
    return order


def _build_router_inputs(trade_request: dict) -> tuple[dict, dict]:
    package = {
        "package_id": "pkg-integration-flow",
        "timestamp_utc": "2026-04-23T00:00:00+00:00",
        "package_type": "integration_flow",
        "effective_mode": "NORMAL",
        "window_context": {
            "time_window_name": "regular_session",
            "watchlist_only_window": False,
        },
        "global_blocks": [],
        "blocked_candidates": [],
        "snapshot": {
            "gross_exposure": 0.0,
            "positions": {},
            "market_microstructure": {
                trade_request["symbol"]: {
                    "last_price": trade_request["limit_price"],
                    "bid": trade_request["limit_price"] - 0.1,
                    "ask": trade_request["limit_price"] + 0.1,
                    "avg_daily_volume": 1_000_000,
                    "adv_shares": 1_000_000,
                    "volatility": 0.2,
                    "sigma_daily_pct": 2.1,
                    "rvol": 1.1,
                }
            },
        },
        "candidates": [
            {
                "candidate_id": "cand-integration-flow",
                "symbol": trade_request["symbol"],
                "direction": trade_request["direction"],
                "confidence_score": trade_request["confidence_score"],
                "size_multiplier_suggestion": trade_request["size_multiplier_suggestion"],
                "strategy": trade_request["strategy"],
                "strategy_style": trade_request["strategy_style"],
                "strategy_family": trade_request["strategy_family"],
                "holding_period": trade_request["holding_period"],
                "reason": trade_request["reason"],
                "price_hints": {"decision_price": trade_request["limit_price"]},
                "fill_sim_assessment": {
                    "expected_slippage_bps": 4.0,
                    "fill_feasibility_score": 0.95,
                    "reject_risk_probability": 0.01,
                    "fill_quality_score": 0.9,
                    "session_liquidity_score": 0.9,
                },
                "execution_constraints": {},
                "metadata": {
                    "strategy": trade_request["strategy"],
                    "strategy_family": trade_request["strategy_family"],
                },
            }
        ],
    }
    strategy_config = {
        "name": trade_request["strategy"],
        "holding_period": trade_request["holding_period"],
        "time_in_force": "day",
        "extended_hours": False,
        "position_sizing": {
            "method": "notional_pct",
            "base_pct_of_equity": 1.0,
            "high_confidence_pct": 1.5,
            "max_single_position_pct": 2.0,
            "min_notional": 1000.0,
            "max_qty_cap": 50,
        },
    }
    return package, strategy_config


def test_trade_request_approval_and_execution_logging_flow(tmp_path: Path, monkeypatch) -> None:
    response, foundry_call = _request_trade_from_foundry(monkeypatch, _trade_request("AAPL"))
    trade_request = jsonlib.loads(response.output)
    approval_order = _approval_order_info(trade_request, guarded=True)

    _configure_trade_approval(tmp_path, monkeypatch)
    captured_submit = _stub_guarded_submit(
        monkeypatch,
        run_id="run-approval-1",
        status="queued",
    )

    approval = trade_approval.request_approval(approval_order)

    assert foundry_call["url"] == "http://router.test/v1/inference"
    assert foundry_call["timeout"] == 30.0
    assert foundry_call["json"]["intent_type"] == "trade_request"
    assert response.route["provider"] == "foundry"
    assert response.trace_id == "trace-foundry-integration"
    assert approval == {
        "approved": True,
        "decision": "approved",
        "reason": "Orchestrator accepted guarded trade execution (run_id=run-approval-1)",
    }
    assert captured_submit["bearer_token"] == "approval-token-aapl"
    assert captured_submit["base_url"] == ""
    assert captured_submit["timeout"] == 5.0
    payload = captured_submit["payload"]
    assert payload["project"] == "global-sentinel"
    assert payload["kind"] == "gs.trade.execute_shadow"
    assert payload["target"] == "global-sentinel/trade-ticket/integration-aapl-ticket"
    assert payload["ticket_id"] == "integration-aapl-ticket"
    assert payload["ticket_hash"] == approval_order["ticket_hash"]
    assert payload["symbol"] == "AAPL"
    assert payload["approval_context"]["approval_jti"] == "approval-jti-aapl"
    assert payload["approval_context"]["approval_issued_by"] == "integration-human"

    package, strategy_config = _build_router_inputs(trade_request)
    route = ShadowOrderRouter(repo_root=tmp_path, broker_name="mock").route_package(
        package=package,
        max_orders=1,
        strategy_config=strategy_config,
    )

    approval_rows = _read_jsonl(tmp_path / "logs" / "trade_approvals.jsonl")
    assert approval_rows[-1]["approval_id"] == APPROVAL_ID
    assert approval_rows[-1]["event_type"] == "approval_decision"
    assert approval_rows[-1]["approved"] is True
    assert approval_rows[-1]["order"]["symbol"] == "AAPL"
    assert approval_rows[-1]["metadata"]["run_id"] == "run-approval-1"

    assert route["submitted_open_or_ack_count"] == 1
    assert route["broker_rejected_count"] == 0
    assert route["selected_candidates"][0]["symbol"] == "AAPL"
    assert route["bound_order_attempts"][0]["broker_status"] == "accepted"

    intent_rows = _read_jsonl(tmp_path / "logs" / "execution" / "order_intents.jsonl")
    route_rows = _read_jsonl(tmp_path / "logs" / "execution" / "shadow_order_router.jsonl")
    binding_rows = _read_jsonl(tmp_path / "logs" / "execution" / "router_order_bindings.jsonl")

    assert len(intent_rows) >= 2
    assert intent_rows[-1]["status"] == "open"
    assert intent_rows[-1]["candidate_context"]["symbol"] == "AAPL"
    assert intent_rows[-1]["broker_binding"]["broker_order_id"]
    assert route_rows[-1]["event_type"] == "route_package_complete"
    assert route_rows[-1]["payload"]["submitted_open_or_ack_count"] == 1
    assert binding_rows[-1]["symbol"] == "AAPL"
    assert binding_rows[-1]["broker_status"] == "accepted"


def test_rejected_trade_request_stops_before_execution_logging(tmp_path: Path, monkeypatch) -> None:
    response, _ = _request_trade_from_foundry(monkeypatch, _trade_request("MSFT"))
    trade_request = jsonlib.loads(response.output)
    approval_order = _approval_order_info(trade_request, guarded=True)
    approval_order["limit_price"] = float(approval_order["limit_price"]) + 1.0

    _configure_trade_approval(tmp_path, monkeypatch)
    monkeypatch.setattr(
        trade_approval,
        "submit_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit_task should not run")),
    )

    approval = trade_approval.request_approval(approval_order)

    if approval["approved"]:
        package, strategy_config = _build_router_inputs(trade_request)
        ShadowOrderRouter(repo_root=tmp_path, broker_name="mock").route_package(
            package=package,
            max_orders=1,
            strategy_config=strategy_config,
        )

    approval_rows = _read_jsonl(tmp_path / "logs" / "trade_approvals.jsonl")
    assert approval["approved"] is False
    assert approval["decision"] == "error"
    assert "Guarded trade ticket hash mismatch" in approval["reason"]
    assert approval_rows[-1]["approved"] is False
    assert approval_rows[-1]["decision"] == "error"
    assert approval_rows[-1]["order"]["symbol"] == "MSFT"
    assert approval_rows[-1]["metadata"]["mediation"] == "orchestrator"

    assert not (tmp_path / "logs" / "execution" / "order_intents.jsonl").exists()
    assert not (tmp_path / "logs" / "execution" / "shadow_order_router.jsonl").exists()
    assert not (tmp_path / "logs" / "execution" / "router_order_bindings.jsonl").exists()
