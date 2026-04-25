from __future__ import annotations

import httpx
import pytest

import src.core.orchestrator_task_client as orchestrator_task_client


def _response(
    method: str,
    url: str,
    *,
    status_code: int = 200,
    payload: object | None = None,
    text: str = "",
) -> httpx.Response:
    kwargs: dict[str, object] = {
        "status_code": status_code,
        "request": httpx.Request(method, url),
    }
    if payload is not None:
        kwargs["json"] = payload
    else:
        kwargs["text"] = text
    return httpx.Response(**kwargs)


@pytest.fixture(autouse=True)
def _isolate_repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_task_client, "_load_repo_env", lambda: {})


def test_submit_task_posts_json_with_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://router.test/v1/inference")

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _response(method, url, status_code=202, payload={"run_id": "run-123", "status": "queued"})

    monkeypatch.setattr(orchestrator_task_client.httpx, "request", fake_request)

    response = orchestrator_task_client.submit_task(
        {
            "kind": "global_sentinel.inference.market_brief",
            "target": "global-sentinel",
            "prompt": "Summarize AAPL setup",
        },
        bearer_token="approve-token",
        timeout=22.5,
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://router.test/v1/tasks"
    assert captured["kwargs"] == {
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer approve-token",
        },
        "timeout": 22.5,
        "json": {
            "kind": "global_sentinel.inference.market_brief",
            "target": "global-sentinel",
            "prompt": "Summarize AAPL setup",
        },
    }
    assert response == {"run_id": "run-123", "status": "queued"}


def test_get_run_uses_run_endpoint_without_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _response(method, url, payload={"run_id": "run/123", "status": "completed"})

    monkeypatch.setattr(orchestrator_task_client.httpx, "request", fake_request)

    response = orchestrator_task_client.get_run("run/123", base_url="http://router.test")

    assert captured["method"] == "GET"
    assert captured["url"] == "http://router.test/v1/runs/run%2F123"
    assert captured["kwargs"] == {
        "headers": {"Accept": "application/json"},
        "timeout": orchestrator_task_client.DEFAULT_TIMEOUT_SECONDS,
    }
    assert response["status"] == "completed"


def test_get_run_history_uses_history_endpoint_and_accepts_prefixed_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _response(method, url, payload={"history": [{"status": "queued"}, {"status": "completed"}]})

    monkeypatch.setattr(orchestrator_task_client.httpx, "request", fake_request)

    response = orchestrator_task_client.get_run_history(
        "run-123",
        base_url="http://router.test/",
        bearer_token="Bearer signed-token",
    )

    assert captured["method"] == "GET"
    assert captured["url"] == "http://router.test/v1/runs/run-123/history"
    assert captured["kwargs"] == {
        "headers": {
            "Accept": "application/json",
            "Authorization": "Bearer signed-token",
        },
        "timeout": orchestrator_task_client.DEFAULT_TIMEOUT_SECONDS,
    }
    assert response["history"][-1]["status"] == "completed"


def test_request_error_maps_to_concise_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("router unavailable", request=httpx.Request(method, url))

    monkeypatch.setattr(orchestrator_task_client.httpx, "request", fake_request)

    with pytest.raises(orchestrator_task_client.OrchestratorTaskClientError) as excinfo:
        orchestrator_task_client.get_run("run-123", base_url="http://router.test")

    assert str(excinfo.value) == "orchestrator request failed: router unavailable"


def test_http_status_error_maps_json_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        return _response(method, url, status_code=403, payload={"error": "approval token required"})

    monkeypatch.setattr(orchestrator_task_client.httpx, "request", fake_request)

    with pytest.raises(orchestrator_task_client.OrchestratorTaskClientError) as excinfo:
        orchestrator_task_client.submit_task(
            {"kind": "gs.control.kill_switch.set", "target": "global-sentinel/control/kill-switch/on"},
            base_url="http://router.test",
        )

    assert str(excinfo.value) == "orchestrator returned HTTP 403: approval token required"


def test_invalid_json_maps_to_concise_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        return _response(method, url, text="not-json")

    monkeypatch.setattr(orchestrator_task_client.httpx, "request", fake_request)

    with pytest.raises(orchestrator_task_client.OrchestratorTaskClientError) as excinfo:
        orchestrator_task_client.get_run_history("run-123", base_url="http://router.test")

    assert str(excinfo.value) == "orchestrator returned invalid JSON"
