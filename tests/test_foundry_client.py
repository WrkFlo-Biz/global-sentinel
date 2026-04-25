from __future__ import annotations

import httpx
import pytest

import src.inference.foundry_client as foundry_client


def _response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", url),
        json=payload,
    )


def _error_response(url: str, status_code: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", url),
        json=payload or {"error": "request failed"},
    )


@pytest.fixture(autouse=True)
def _isolate_repo_env(monkeypatch):
    monkeypatch.setattr(foundry_client, "_load_repo_env", lambda: {})


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
    assert payload["options"] == {"temperature": 0.3, "max_tokens": 1500}
    assert "request_options" not in payload

    assert response.output == "ok"
    assert response.trace_id == "trace-123"
    assert response.route["provider"] == "foundry"
    assert response.route["tokens"]["total"] == 18


def test_send_request_keeps_successful_calls_on_foundry_boundary(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    calls: list[str] = []

    def fake_post(url, *, json, headers=None, timeout=None):
        calls.append(url)
        return _response(
            url,
            {
                "output": "foundry answer",
                "route": {
                    "provider": "foundry",
                    "model": "critic-1",
                    "latency_ms": 18,
                    "tokens": {"input": 12, "output": 9, "total": 21},
                },
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="decision_support",
        target_role="critic",
        operating_context={"mode": "NORMAL"},
        latency_class="premium",
        trace_context={"intent_id": "critic-1"},
        messages=[{"role": "user", "content": "Challenge this thesis"}],
    )

    assert calls == [orchestrator_url]
    assert response.output == "foundry answer"
    assert response.route["provider"] == "foundry"
    assert response.route["model"] == "critic-1"


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


def test_send_request_uses_azure_claude_api_key_when_openai_key_missing(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    azure_endpoint = "https://azure.test/"
    azure_url = (
        "https://azure.test/openai/deployments/fallback-premium/chat/completions"
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
            raise httpx.ReadTimeout("router timeout", request=httpx.Request("POST", url))
        return _response(
            url,
            {
                "choices": [{"message": {"content": "claude key fallback answer"}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", azure_endpoint)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fallback-premium")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_CLAUDE_API_KEY", "claude-secret")
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="market_query",
        target_role="planner",
        operating_context={"mode": "NORMAL"},
        latency_class="premium",
        trace_context={"intent_id": "market-query-premium"},
        messages=[{"role": "user", "content": "What changed?"}],
    )

    assert [call["url"] for call in calls] == [orchestrator_url, azure_url]
    assert calls[0]["timeout"] == 90.0
    assert calls[1]["timeout"] == 90.0
    assert calls[1]["headers"] == {
        "Content-Type": "application/json",
        "api-key": "claude-secret",
    }
    assert response.output == "claude key fallback answer"
    assert response.route["provider"] == "azure"
    assert response.route["model"] == "fallback-premium"
    assert response.policy_annotations["fallback_mode"] == "azure_direct"


def test_send_request_loads_orchestrator_and_fallback_key_from_repo_env(monkeypatch):
    orchestrator_url = "http://router.repo/v1/inference"
    azure_url = (
        "https://azure.repo/openai/deployments/repo-mini/chat/completions"
        "?api-version=2024-06-01-preview"
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
                "choices": [{"message": {"content": "repo env fallback answer"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            },
        )

    monkeypatch.delenv("ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)
    monkeypatch.setattr(
        foundry_client,
        "_load_repo_env",
        lambda: {
            "ORCHESTRATOR_URL": orchestrator_url,
            "AZURE_OPENAI_ENDPOINT": "https://azure.repo/",
            "AZURE_OPENAI_API_KEY": "repo-secret",
            "AZURE_OPENAI_DEPLOYMENT": "repo-mini",
            "AZURE_OPENAI_API_VERSION": "2024-06-01-preview",
        },
    )
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    response = foundry_client.send_request(
        intent_type="market_query",
        target_role="planner",
        operating_context={"mode": "NORMAL"},
        latency_class="interactive",
        trace_context={"intent_id": "repo-env-route"},
        messages=[{"role": "user", "content": "Route through repo env"}],
    )

    assert [call["url"] for call in calls] == [orchestrator_url, azure_url]
    assert calls[1]["headers"] == {
        "Content-Type": "application/json",
        "api-key": "repo-secret",
    }
    assert calls[1]["timeout"] == 30.0
    assert response.output == "repo env fallback answer"
    assert response.route["provider"] == "azure"
    assert response.route["model"] == "repo-mini"
    assert response.route["tokens"] == {"input": 7, "output": 5, "total": 12}


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


@pytest.mark.parametrize(
    ("latency_class", "expected_latency_class", "expected_timeout", "expected_max_tokens"),
    [
        ("interactive", "interactive", 30.0, 2000),
        ("batch", "batch", 60.0, 1500),
        ("premium", "premium", 90.0, 2500),
        ("unexpected", "interactive", 30.0, 2000),
    ],
)
def test_send_request_normalizes_latency_into_timeouts_and_options_payload(
    monkeypatch,
    latency_class,
    expected_latency_class,
    expected_timeout,
    expected_max_tokens,
):
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers=None, timeout=None):
        captured["json"] = json
        captured["timeout"] = timeout
        return _response(url, {"output": "ok"})

    monkeypatch.setenv("ORCHESTRATOR_URL", "http://router.test/v1/inference")
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    foundry_client.send_request(
        intent_type="daily_thesis",
        target_role="summarizer",
        operating_context={"mode": "NORMAL"},
        latency_class=latency_class,
        trace_context={"intent_id": f"test-{latency_class}"},
        messages=[{"role": "user", "content": "Summarize this"}],
    )

    payload = captured["json"]
    assert captured["timeout"] == expected_timeout
    assert payload["latency_class"] == expected_latency_class
    assert payload["options"] == {"temperature": 0.3, "max_tokens": expected_max_tokens}
    assert "request_options" not in payload


def test_send_request_propagates_orchestrator_http_status_error_without_fallback(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    calls: list[str] = []

    def fake_post(url, *, json, headers=None, timeout=None):
        calls.append(url)
        return _error_response(url, 503)

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        foundry_client.send_request(
            intent_type="market_query",
            target_role="planner",
            operating_context={"mode": "NORMAL"},
            latency_class="interactive",
            trace_context={"intent_id": "market-query-status-error"},
            messages=[{"role": "user", "content": "What broke?"}],
        )

    assert calls == [orchestrator_url]


def test_send_request_propagates_azure_fallback_http_status_error(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    azure_url = (
        "https://azure.test/openai/deployments/fallback-mini/chat/completions"
        "?api-version=2024-06-01-preview"
    )
    calls: list[str] = []

    def fake_post(url, *, json, headers=None, timeout=None):
        calls.append(url)
        if url == orchestrator_url:
            raise httpx.ConnectError("router unavailable", request=httpx.Request("POST", url))
        return _error_response(url, 401, {"error": "bad api key"})

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.test/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "fallback-mini")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-06-01-preview")
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        foundry_client.send_request(
            intent_type="market_query",
            target_role="planner",
            operating_context={"mode": "NORMAL"},
            latency_class="interactive",
            trace_context={"intent_id": "market-query-fallback-status-error"},
            messages=[{"role": "user", "content": "What broke?"}],
        )

    assert calls == [orchestrator_url, azure_url]


def test_send_request_raises_for_missing_output_text(monkeypatch):
    def fake_post(url, *, json, headers=None, timeout=None):
        return _response(
            url,
            {
                "route": {
                    "provider": "foundry",
                    "model": "planner-1",
                }
            },
        )

    monkeypatch.setenv("ORCHESTRATOR_URL", "http://router.test/v1/inference")
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    with pytest.raises(ValueError, match="output text"):
        foundry_client.send_request(
            intent_type="market_query",
            target_role="planner",
            operating_context={"mode": "NORMAL"},
            latency_class="interactive",
            trace_context={"intent_id": "market-query-missing-output"},
            messages=[{"role": "user", "content": "What changed?"}],
        )


def test_send_request_raises_when_fallback_api_key_is_missing(monkeypatch):
    def fake_post(url, *, json, headers=None, timeout=None):
        raise httpx.ConnectError("router unavailable", request=httpx.Request("POST", url))

    monkeypatch.setenv("ORCHESTRATOR_URL", "http://router.test/v1/inference")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY is not set for fallback"):
        foundry_client.send_request(
            intent_type="market_query",
            target_role="planner",
            operating_context={"mode": "NORMAL"},
            latency_class="interactive",
            trace_context={"intent_id": "market-query-no-key"},
            messages=[{"role": "user", "content": "Any setup?"}],
        )


def test_send_request_does_not_attempt_azure_fallback_for_embeddings(monkeypatch):
    orchestrator_url = "http://router.test/v1/inference"
    calls: list[str] = []

    def fake_post(url, *, json, headers=None, timeout=None):
        calls.append(url)
        raise httpx.ConnectError("router unavailable", request=httpx.Request("POST", url))

    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    monkeypatch.setattr(foundry_client.httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="Azure fallback for embeddings is not implemented"):
        foundry_client.send_request(
            intent_type="embed_docs",
            target_role="embeddings",
            operating_context={"mode": "NORMAL"},
            latency_class="batch",
            trace_context={"intent_id": "embed-1"},
            messages=[{"role": "user", "content": "Vectorize this"}],
        )

    assert calls == [orchestrator_url]
