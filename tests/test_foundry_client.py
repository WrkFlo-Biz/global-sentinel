from __future__ import annotations

import httpx

import src.inference.foundry_client as foundry_client


def _response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", url),
        json=payload,
    )


def test_send_request_posts_expected_envelope(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _response(
            url,
            {
                "output": "ok",
                "route": {
                    "provider": "foundry",
                    "model": "gpt-5-mini",
                    "latency_ms": 41,
                    "tokens": {"input": 11, "output": 7, "total": 18},
                },
                "trace_id": "trace-123",
                "policy_annotations": {"approval": "not_required"},
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="daily_thesis",
        target_role="summarizer",
        operating_context={
            "mode": "NORMAL",
            "regime_shift_probability": 0.18,
            "kill_switch": False,
            "manual_veto": False,
            "execution_sensitivity": "research_only",
        },
        latency_class="batch",
        trace_context={"package_id": "pkg-1", "intent_id": "intent-1"},
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
    )

    assert captured["url"] == orchestrator_url
    assert captured["headers"] == {"Content-Type": "application/json"}
    assert captured["timeout"] == 60.0

    payload = captured["json"]
    assert payload["intent_type"] == "daily_thesis"
    assert payload["target_role"] == "summarizer"
    assert payload["latency_class"] == "batch"
    assert payload["messages"][1]["content"] == "user"
    assert payload["trace_context"]["package_id"] == "pkg-1"
    assert payload["trace_context"]["intent_id"] == "intent-1"
    assert payload["trace_context"]["trace_id"]
    assert payload["request_options"] == {"temperature": 0.3, "max_tokens": 1500}

    assert response.output == "ok"
    assert response.trace_id == "trace-123"
    assert response.route["provider"] == "foundry"
    assert response.route["tokens"]["total"] == 18


def test_send_request_falls_back_to_azure_when_orchestrator_unreachable(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    azure_endpoint = "https://azure.test/"
    azure_url = (
        "https://azure.test/openai/deployments/fallback-mini/chat/completions"
        "?api-version=2024-05-01-preview"
    )
    calls: list[dict[str, object]] = []

    def fake_post(url, *, json, headers=None, timeout=None):
        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if url == orchestrator_url:
            raise httpx.ConnectError("router unavailable", request=httpx.Request("POST", url))
        return _response(
            url,
            {
                "choices": [{"message": {"content": "azure fallback answer"}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30},
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", azure_endpoint)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fallback-mini")
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="market_query",
        target_role="planner",
        operating_context={"mode": "NORMAL"},
        latency_class="interactive",
        trace_context={"intent_id": "market-query-1"},
        messages=[{"role": "user", "content": "What is the setup?"}],
    )

    assert [call["url"] for call in calls] == [orchestrator_url, azure_url]
    assert calls[1]["headers"] == {
        "Content-Type": "application/json",
        "api-key": "secret",
    }
    assert calls[1]["json"]["messages"][0]["content"] == "What is the setup?"
    assert calls[1]["json"]["max_tokens"] == 2000
    assert response.output == "azure fallback answer"
    assert response.route["provider"] == "azure"
    assert response.route["model"] == "fallback-mini"
    assert response.route["tokens"] == {"input": 21, "output": 9, "total": 30}
    assert response.policy_annotations["fallback_mode"] == "azure_direct"


def test_send_request_parses_openai_style_response(monkeypatch):
    def fake_post(url, *, json, headers=None, timeout=None):
        return _response(
            url,
            {
                "choices": [{"message": {"content": "parsed answer"}}],
                "model": "foundry-planner-1",
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 5,
                    "total_tokens": 18,
                },
                "trace_id": "trace-xyz",
                "policy_annotations": {"policy": "allow"},
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", "http://router.test/v1/inference")
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="market_query",
        target_role="planner",
        operating_context={"mode": "NORMAL"},
        latency_class="interactive",
        trace_context={"intent_id": "market-query-1"},
        messages=[{"role": "user", "content": "What changed?"}],
    )

    assert response.output == "parsed answer"
    assert response.trace_id == "trace-xyz"
    assert response.route["provider"] == "foundry"
    assert response.route["model"] == "foundry-planner-1"
    assert response.route["tokens"] == {"input": 13, "output": 5, "total": 18}
    assert isinstance(response.route["latency_ms"], int)
    assert response.route["latency_ms"] >= 0
    assert response.policy_annotations == {"policy": "allow"}
