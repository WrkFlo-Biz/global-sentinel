from __future__ import annotations

import json as jsonlib
import uuid
from pathlib import Path

import httpx
import pytest

import src.execution.trade_approval as trade_approval
import src.inference.foundry_client as foundry_client
from src.execution.shadow_order_router import ShadowOrderRouter


CALLBACK_OFFSET_PATH = Path("/tmp/gs_callback_offset.json")
APPROVAL_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
APPROVAL_ID = APPROVAL_UUID.hex[:12]


class _RequestsResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or jsonlib.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _isolate_foundry_repo_env(monkeypatch) -> None:
    monkeypatch.setattr(foundry_client, "_load_repo_env", lambda: {})


@pytest.fixture(autouse=True)
def _cleanup_callback_offset() -> None:
    CALLBACK_OFFSET_PATH.unlink(missing_ok=True)
    yield
    CALLBACK_OFFSET_PATH.unlink(missing_ok=True)


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
    monkeypatch.setenv("TRADE_APPROVAL_AUTO_EXECUTE", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "trade-chat")
    monkeypatch.setenv("TELEGRAM_TRADING_THREAD_ID", "0")
    monkeypatch.setattr(trade_approval, "APPROVAL_LOG_PATH", tmp_path / "logs" / "trade_approvals.jsonl")
    monkeypatch.setattr(trade_approval, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(trade_approval.uuid, "uuid4", lambda: APPROVAL_UUID)


def _stub_telegram_api(monkeypatch, *, decision: str) -> tuple[list[dict], list[dict], list[dict]]:
    post_calls: list[dict] = []
    get_calls: list[dict] = []
    answer_calls: list[dict] = []

    def fake_post(url, json=None, timeout=None, **kwargs):
        call = {
            "url": url,
            "json": dict(json or {}),
            "timeout": timeout,
        }
        if url.endswith("/answerCallbackQuery"):
            answer_calls.append(call)
            return _RequestsResponse(200, {"ok": True})

        post_calls.append(call)
        if url.endswith("/sendMessage") and "reply_markup" in (json or {}):
            return _RequestsResponse(200, {"result": {"message_id": 321}})
        return _RequestsResponse(200, {"ok": True})

    def fake_get(url, params=None, timeout=None, **kwargs):
        get_calls.append(
            {
                "url": url,
                "params": dict(params or {}),
                "timeout": timeout,
            }
        )
        callback_action = "approve" if decision == "approved" else "reject"
        return _RequestsResponse(
            200,
            {
                "result": [
                    {
                        "update_id": 77,
                        "callback_query": {
                            "id": "callback-1",
                            "data": f"{callback_action}:{APPROVAL_ID}",
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(trade_approval.requests, "post", fake_post)
    monkeypatch.setattr(trade_approval.requests, "get", fake_get)
    return post_calls, get_calls, answer_calls


def _approval_order_info(trade_request: dict) -> dict:
    return {
        "symbol": trade_request["symbol"],
        "side": trade_request["side"],
        "qty": trade_request["qty"],
        "limit_price": trade_request["limit_price"],
        "notional": trade_request["notional"],
        "signal_source": trade_request["signal_source"],
        "strategy_style": trade_request["strategy_style"],
        "asset_class": "equity",
    }


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

    _configure_trade_approval(tmp_path, monkeypatch)
    post_calls, get_calls, answer_calls = _stub_telegram_api(monkeypatch, decision="approved")

    approval = trade_approval.request_approval(_approval_order_info(trade_request))

    assert foundry_call["url"] == "http://router.test/v1/inference"
    assert foundry_call["timeout"] == 30.0
    assert foundry_call["json"]["intent_type"] == "trade_request"
    assert response.route["provider"] == "foundry"
    assert response.trace_id == "trace-foundry-integration"

    assert approval == {
        "approved": True,
        "decision": "approved",
        "reason": "User approved: 'inline_button_yes'",
    }
    assert post_calls[0]["json"]["message_thread_id"] == 0
    assert "TRADE APPROVAL REQUIRED" in post_calls[0]["json"]["text"]
    assert "reply_markup" in post_calls[0]["json"]
    assert post_calls[-1]["json"]["text"].startswith("\u2705 Trade EXECUTED")
    assert get_calls[0]["params"]["allowed_updates"] == "[\"callback_query\"]"
    assert answer_calls[0]["json"] == {"callback_query_id": "callback-1"}

    package, strategy_config = _build_router_inputs(trade_request)
    route = ShadowOrderRouter(repo_root=tmp_path, broker_name="mock").route_package(
        package=package,
        max_orders=1,
        strategy_config=strategy_config,
    )

    approval_rows = _read_jsonl(tmp_path / "logs" / "trade_approvals.jsonl")
    assert approval_rows[-1]["approval_id"] == APPROVAL_ID
    assert approval_rows[-1]["approved"] is True
    assert approval_rows[-1]["order"]["symbol"] == "AAPL"

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

    _configure_trade_approval(tmp_path, monkeypatch)
    post_calls, _, _ = _stub_telegram_api(monkeypatch, decision="rejected")

    approval = trade_approval.request_approval(_approval_order_info(trade_request))

    if approval["approved"]:
        package, strategy_config = _build_router_inputs(trade_request)
        ShadowOrderRouter(repo_root=tmp_path, broker_name="mock").route_package(
            package=package,
            max_orders=1,
            strategy_config=strategy_config,
        )

    approval_rows = _read_jsonl(tmp_path / "logs" / "trade_approvals.jsonl")
    assert approval["approved"] is False
    assert approval["decision"] == "rejected"
    assert "User rejected" in approval["reason"]
    assert approval_rows[-1]["approved"] is False
    assert approval_rows[-1]["order"]["symbol"] == "MSFT"
    assert post_calls[-1]["json"]["text"].startswith("\u274c Trade SKIPPED")

    assert not (tmp_path / "logs" / "execution" / "order_intents.jsonl").exists()
    assert not (tmp_path / "logs" / "execution" / "shadow_order_router.jsonl").exists()
    assert not (tmp_path / "logs" / "execution" / "router_order_bindings.jsonl").exists()
