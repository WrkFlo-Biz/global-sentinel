"""GS-side Foundry client boundary with orchestrator routing and Azure fallback."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

DEFAULT_ORCHESTRATOR_URL = "http://localhost:8100/v1/inference"
DEFAULT_AZURE_ENDPOINT = "https://moses-8586-resource.services.ai.azure.com/"
DEFAULT_AZURE_DEPLOYMENT = "gpt-5-mini"
DEFAULT_AZURE_API_VERSION = "2024-05-01-preview"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = {
    "interactive": 2000,
    "batch": 1500,
    "premium": 2500,
}
DEFAULT_TIMEOUT_SECONDS = {
    "interactive": 30.0,
    "batch": 60.0,
    "premium": 90.0,
}


class TargetRole(str, Enum):
    SUMMARIZER = "summarizer"
    PLANNER = "planner"
    CRITIC = "critic"
    EXECUTOR = "executor"
    EMBEDDINGS = "embeddings"


@dataclass(frozen=True)
class FoundryResponse:
    output: str
    route: dict[str, Any]
    trace_id: str
    policy_annotations: dict[str, Any]


def send_request(
    intent_type: str,
    target_role: TargetRole | str,
    operating_context: Mapping[str, Any] | None,
    latency_class: str,
    trace_context: Mapping[str, Any] | None,
    messages: Sequence[Mapping[str, Any]],
) -> FoundryResponse:
    """Send a GS inference request through wrkflo-orchestrator with Azure fallback."""

    role = TargetRole(target_role).value
    normalized_latency = _normalize_latency_class(latency_class)
    trace_payload = dict(trace_context or {})
    trace_id = str(trace_payload.get("trace_id") or uuid.uuid4().hex)
    trace_payload.setdefault("trace_id", trace_id)
    message_payload = [dict(message) for message in messages]

    envelope = _build_request_envelope(
        intent_type=intent_type,
        target_role=role,
        operating_context=dict(operating_context or {}),
        latency_class=normalized_latency,
        trace_context=trace_payload,
        messages=message_payload,
    )

    orchestrator_url = _config_value("ORCHESTRATOR_URL", DEFAULT_ORCHESTRATOR_URL)
    started = time.perf_counter()
    try:
        response = httpx.post(
            orchestrator_url,
            json=envelope,
            headers={"Content-Type": "application/json"},
            timeout=_timeout_for(normalized_latency),
        )
        response.raise_for_status()
        return _parse_response(
            response.json(),
            default_trace_id=trace_id,
            default_provider="foundry",
            default_model="",
            latency_ms=_latency_ms(started),
        )
    except httpx.RequestError as exc:
        return _send_azure_fallback(
            intent_type=intent_type,
            target_role=role,
            latency_class=normalized_latency,
            trace_id=trace_id,
            messages=message_payload,
            orchestrator_error=exc,
        )


def _build_request_envelope(
    intent_type: str,
    target_role: str,
    operating_context: Mapping[str, Any],
    latency_class: str,
    trace_context: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "intent_type": intent_type,
        "target_role": target_role,
        "operating_context": dict(operating_context),
        "latency_class": latency_class,
        "trace_context": dict(trace_context),
        "messages": [dict(message) for message in messages],
        "options": _options_payload(latency_class),
    }


def _send_azure_fallback(
    intent_type: str,
    target_role: str,
    latency_class: str,
    trace_id: str,
    messages: Sequence[Mapping[str, Any]],
    orchestrator_error: httpx.RequestError,
) -> FoundryResponse:
    if target_role == TargetRole.EMBEDDINGS.value:
        raise RuntimeError(
            "ORCHESTRATOR_URL is unreachable and Azure fallback for embeddings is not implemented"
        ) from orchestrator_error

    endpoint = _config_value("AZURE_OPENAI_ENDPOINT", DEFAULT_AZURE_ENDPOINT).rstrip("/")
    api_key = _config_value("AZURE_OPENAI_API_KEY", _config_value("AZURE_CLAUDE_API_KEY", ""))
    deployment = _config_value("AZURE_OPENAI_DEPLOYMENT", DEFAULT_AZURE_DEPLOYMENT)
    api_version = _config_value("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION)

    if not api_key:
        raise RuntimeError(
            "ORCHESTRATOR_URL is unreachable and AZURE_OPENAI_API_KEY is not set for fallback"
        ) from orchestrator_error

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    started = time.perf_counter()
    response = httpx.post(
        url,
        json={
            "messages": [dict(message) for message in messages],
            **_options_payload(latency_class),
        },
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        timeout=_timeout_for(latency_class),
    )
    response.raise_for_status()
    parsed = _parse_response(
        response.json(),
        default_trace_id=trace_id,
        default_provider="azure",
        default_model=deployment,
        latency_ms=_latency_ms(started),
    )
    annotations = dict(parsed.policy_annotations)
    annotations.setdefault("fallback_mode", "azure_direct")
    annotations.setdefault("intent_type", intent_type)
    annotations.setdefault("orchestrator_error", str(orchestrator_error))
    return FoundryResponse(
        output=parsed.output,
        route=parsed.route,
        trace_id=parsed.trace_id or trace_id,
        policy_annotations=annotations,
    )


def _parse_response(
    payload: Any,
    default_trace_id: str,
    default_provider: str,
    default_model: str,
    latency_ms: int,
) -> FoundryResponse:
    body = _primary_payload(payload)
    output = _extract_output_text(body)

    route_payload = {}
    if isinstance(payload, Mapping) and isinstance(payload.get("route"), Mapping):
        route_payload = dict(payload["route"])
    elif isinstance(body, Mapping) and isinstance(body.get("route"), Mapping):
        route_payload = dict(body["route"])

    model = (
        route_payload.get("model")
        or route_payload.get("deployment")
        or (body.get("model") if isinstance(body, Mapping) else "")
        or (payload.get("model") if isinstance(payload, Mapping) else "")
        or default_model
    )
    route = {
        "provider": route_payload.get("provider") or default_provider,
        "model": model,
        "latency_ms": route_payload.get("latency_ms", route_payload.get("latency", latency_ms)),
        "tokens": _normalize_tokens(
            route_payload.get("tokens")
            or (body.get("usage") if isinstance(body, Mapping) else {})
            or (payload.get("usage") if isinstance(payload, Mapping) else {})
        ),
    }
    for extra_key in ("profile", "deployment", "fallback_chain"):
        if extra_key in route_payload:
            route[extra_key] = route_payload[extra_key]

    annotations_source = payload.get("policy_annotations") if isinstance(payload, Mapping) else None
    if annotations_source is None and isinstance(body, Mapping):
        annotations_source = body.get("policy_annotations")

    trace_id = default_trace_id
    if isinstance(payload, Mapping) and payload.get("trace_id"):
        trace_id = str(payload["trace_id"])
    elif isinstance(body, Mapping) and body.get("trace_id"):
        trace_id = str(body["trace_id"])

    return FoundryResponse(
        output=output,
        route=route,
        trace_id=trace_id,
        policy_annotations=_normalize_annotations(annotations_source),
    )


def _primary_payload(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        for key in ("result", "response", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, Mapping):
                return candidate
    return payload


def _extract_output_text(payload: Any) -> str:
    text = _extract_text(payload)
    if text is not None:
        return text
    if not isinstance(payload, Mapping):
        raise ValueError("Response payload did not include output text")

    for key in ("output", "content", "message"):
        text = _extract_text(payload.get(key))
        if text is not None:
            return text

    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            text = _extract_text(choice)
            if text is not None:
                return text

    raise ValueError("Response payload did not include output text")


def _extract_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "output_text", "content"):
            text = _extract_text(value.get(key))
            if text is not None:
                return text
        return _extract_text(value.get("message"))
    if isinstance(value, list):
        parts = [part for item in value if (part := _extract_text(item))]
        if parts:
            return "\n".join(parts)
    return None


def _normalize_tokens(tokens: Any) -> dict[str, int]:
    if not isinstance(tokens, Mapping):
        return {}

    input_tokens = _coerce_int(tokens.get("input"))
    if input_tokens is None:
        input_tokens = _coerce_int(tokens.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _coerce_int(tokens.get("prompt_tokens"))

    output_tokens = _coerce_int(tokens.get("output"))
    if output_tokens is None:
        output_tokens = _coerce_int(tokens.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _coerce_int(tokens.get("completion_tokens"))

    total_tokens = _coerce_int(tokens.get("total"))
    if total_tokens is None:
        total_tokens = _coerce_int(tokens.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    normalized: dict[str, int] = {}
    if input_tokens is not None:
        normalized["input"] = input_tokens
    if output_tokens is not None:
        normalized["output"] = output_tokens
    if total_tokens is not None:
        normalized["total"] = total_tokens
    return normalized


def _normalize_annotations(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {"raw": value}


def _options_payload(latency_class: str) -> dict[str, Any]:
    normalized = _normalize_latency_class(latency_class)
    return {
        "temperature": DEFAULT_TEMPERATURE,
        "max_tokens": DEFAULT_MAX_TOKENS.get(normalized, DEFAULT_MAX_TOKENS["interactive"]),
    }


def _timeout_for(latency_class: str) -> float:
    normalized = _normalize_latency_class(latency_class)
    return DEFAULT_TIMEOUT_SECONDS.get(normalized, DEFAULT_TIMEOUT_SECONDS["interactive"])


def _normalize_latency_class(latency_class: str) -> str:
    value = (latency_class or "interactive").strip().lower()
    if value in DEFAULT_MAX_TOKENS:
        return value
    return "interactive"


def _config_value(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value:
        return env_value
    repo_value = _load_repo_env().get(name, "")
    if repo_value:
        return repo_value
    return default


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
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _coerce_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
