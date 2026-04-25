"""GS-side client helpers for wrkflo-orchestrator task endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

DEFAULT_ORCHESTRATOR_API_BASE = "http://localhost:8100"
DEFAULT_ORCHESTRATOR_INFERENCE_URL = "http://localhost:8100/v1/inference"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_PROJECT = "global-sentinel"


class OrchestratorTaskClientError(RuntimeError):
    """Raised when the orchestrator task API cannot satisfy a request."""


def submit_task(
    payload: Mapping[str, Any],
    *,
    bearer_token: str = "",
    base_url: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST a task payload to wrkflo-orchestrator."""

    return _request_json(
        "POST",
        "/v1/tasks",
        payload=dict(payload),
        bearer_token=bearer_token,
        base_url=base_url,
        timeout=timeout,
    )


def build_guarded_task_payload(
    *,
    kind: str,
    target: str,
    project: str = DEFAULT_PROJECT,
    requester_id: str = "",
    requester_name: str = "",
    requester_channel: str = "",
    approval_jti: str = "",
    approval_reason: str = "",
    approval_exp: Any = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a consistent GS guarded task payload for orchestrator submissions."""

    task_payload = dict(payload or {})
    task_payload["project"] = _require_text(project, "project")
    task_payload["kind"] = _require_text(kind, "kind")
    task_payload["target"] = _require_text(target, "target")

    requester = _guarded_requester_fields(
        requester_id=requester_id,
        requester_name=requester_name,
        requester_channel=requester_channel,
    )
    if requester:
        task_payload.update(requester)
        task_payload["requester"] = dict(requester)

    approval_context = _guarded_approval_fields(
        approval_jti=approval_jti,
        approval_reason=approval_reason,
        approval_exp=approval_exp,
    )
    if approval_context:
        task_payload.update(approval_context)
        task_payload["approval_context"] = dict(approval_context)

    return task_payload


def submit_guarded_task(
    *,
    kind: str,
    target: str,
    project: str = DEFAULT_PROJECT,
    requester_id: str = "",
    requester_name: str = "",
    requester_channel: str = "",
    approval_jti: str = "",
    approval_reason: str = "",
    approval_exp: Any = None,
    payload: Mapping[str, Any] | None = None,
    bearer_token: str = "",
    base_url: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Submit one GS guarded task using the shared payload contract."""

    return submit_task(
        build_guarded_task_payload(
            kind=kind,
            target=target,
            project=project,
            requester_id=requester_id,
            requester_name=requester_name,
            requester_channel=requester_channel,
            approval_jti=approval_jti,
            approval_reason=approval_reason,
            approval_exp=approval_exp,
            payload=payload,
        ),
        bearer_token=bearer_token,
        base_url=base_url,
        timeout=timeout,
    )


def get_run(
    run_id: str,
    *,
    bearer_token: str = "",
    base_url: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Fetch the latest state for a specific orchestrator run."""

    return _request_json(
        "GET",
        f"/v1/runs/{quote(run_id, safe='')}",
        bearer_token=bearer_token,
        base_url=base_url,
        timeout=timeout,
    )


def get_run_history(
    run_id: str,
    *,
    bearer_token: str = "",
    base_url: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Fetch the full persisted history for a specific orchestrator run."""

    return _request_json(
        "GET",
        f"/v1/runs/{quote(run_id, safe='')}/history",
        bearer_token=bearer_token,
        base_url=base_url,
        timeout=timeout,
    )


def _request_json(
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    bearer_token: str = "",
    base_url: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    url = _join_url(_api_base_url(base_url), path)
    headers = _headers(bearer_token=bearer_token, has_json_body=payload is not None)
    request_kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": timeout,
    }
    if payload is not None:
        request_kwargs["json"] = dict(payload)

    try:
        response = httpx.request(method, url, **request_kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OrchestratorTaskClientError(_status_error_message(exc.response)) from exc
    except httpx.RequestError as exc:
        raise OrchestratorTaskClientError(f"orchestrator request failed: {exc}") from exc

    try:
        decoded = response.json()
    except ValueError as exc:
        raise OrchestratorTaskClientError("orchestrator returned invalid JSON") from exc

    if not isinstance(decoded, Mapping):
        raise OrchestratorTaskClientError("orchestrator returned non-object JSON")
    return dict(decoded)


def _headers(*, bearer_token: str, has_json_body: bool) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if has_json_body:
        headers["Content-Type"] = "application/json"

    token = bearer_token.strip()
    if token:
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers["Authorization"] = token
    return headers


def _guarded_requester_fields(
    *,
    requester_id: str,
    requester_name: str,
    requester_channel: str,
) -> dict[str, str]:
    requester: dict[str, str] = {}
    normalized_id = _optional_text(requester_id)
    normalized_name = _optional_text(requester_name)
    normalized_channel = _optional_text(requester_channel)
    if normalized_id is not None:
        requester["requester_id"] = normalized_id
    if normalized_name is not None:
        requester["requester_name"] = normalized_name
    if normalized_channel is not None:
        requester["requester_channel"] = normalized_channel
    return requester


def _guarded_approval_fields(
    *,
    approval_jti: str,
    approval_reason: str,
    approval_exp: Any,
) -> dict[str, Any]:
    approval: dict[str, Any] = {}
    normalized_jti = _optional_text(approval_jti)
    normalized_reason = _optional_text(approval_reason)
    normalized_exp = _optional_exp(approval_exp)
    if normalized_jti is not None:
        approval["approval_jti"] = normalized_jti
    if normalized_reason is not None:
        approval["approval_reason"] = normalized_reason
    if normalized_exp is not None:
        approval["approval_exp"] = normalized_exp
    return approval


def _api_base_url(override: str = "") -> str:
    if override:
        return override.rstrip("/")

    explicit_base = _config_value("ORCHESTRATOR_TASK_API_BASE") or _config_value("ORCHESTRATOR_API_BASE")
    if explicit_base:
        return explicit_base.rstrip("/")

    inference_url = _config_value("ORCHESTRATOR_URL", DEFAULT_ORCHESTRATOR_INFERENCE_URL)
    parsed = urlsplit(inference_url)
    if parsed.scheme and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    return DEFAULT_ORCHESTRATOR_API_BASE


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _status_error_message(response: httpx.Response) -> str:
    detail = _response_detail(response)
    if detail:
        return f"orchestrator returned HTTP {response.status_code}: {detail}"
    return f"orchestrator returned HTTP {response.status_code}"


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, Mapping):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]

    text = response.text.strip()
    if text:
        return text[:200]
    return ""


def _config_value(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value:
        return env_value
    repo_value = _load_repo_env().get(name, "")
    if repo_value:
        return repo_value
    return default


def _require_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_exp(value: Any) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = _optional_text(value)
    return text


def _load_repo_env() -> dict[str, str]:
    repo_root = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[2]))
    env_path = repo_root / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        values[key.strip()] = raw_value.strip().strip('"').strip("'")
    return values
