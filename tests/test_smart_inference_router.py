from __future__ import annotations

import json

import pytest

from src.inference.foundry_client import FoundryResponse
import src.monitoring.smart_inference_router as smart_router


def _router(monkeypatch, tmp_path):
    monkeypatch.setattr(smart_router, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(
        smart_router,
        "ROUTING_LOG_PATH",
        tmp_path / "logs" / "inference_routing.jsonl",
    )
    smart_router.SmartInferenceRouter._deprecation_notice_emitted = True
    return smart_router.SmartInferenceRouter()


def test_query_routes_simple_prompt_to_foundry_summarizer(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_send_request(
        *,
        intent_type,
        target_role,
        operating_context,
        latency_class,
        trace_context,
        messages,
    ):
        captured["intent_type"] = intent_type
        captured["target_role"] = target_role
        captured["operating_context"] = operating_context
        captured["latency_class"] = latency_class
        captured["trace_context"] = trace_context
        captured["messages"] = messages
        return FoundryResponse(
            output="summary complete",
            route={
                "provider": "foundry",
                "model": "gpt-5-mini",
                "latency_ms": 42,
                "tokens": {"input": 12, "output": 8, "total": 20},
            },
            trace_id="trace-1",
            policy_annotations={"policy": "allow"},
        )

    monkeypatch.setattr(smart_router.foundry_client, "send_request", fake_send_request)

    router = _router(monkeypatch, tmp_path)
    result = router.query("Summarize the overnight move", system="Be concise")

    assert captured["intent_type"] == "legacy_smart_router"
    assert captured["target_role"] == "summarizer"
    assert captured["latency_class"] == "interactive"
    assert captured["operating_context"] == {
        "source": "smart_inference_router",
        "legacy_complexity": "simple",
    }
    assert captured["trace_context"] == {"source": "smart_inference_router"}
    assert captured["messages"] == [
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Summarize the overnight move"},
    ]

    assert result == {
        "response": "summary complete",
        "tier_used": "cheap",
        "classified_as": "simple",
        "fallback_chain": [
            {
                "tier": "cheap",
                "role": "summarizer",
                "status": "success",
                "provider": "foundry",
                "model": "gpt-5-mini",
            }
        ],
        "latency_ms": 42,
    }

    stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert stats["total_queries"] == 1
    assert stats["tier_counts"]["cheap"] == 1
    assert stats["estimated_cost"] == pytest.approx(0.000003)
    assert stats["baseline_cost"] == pytest.approx(0.0001)

    routing_log = (tmp_path / "logs" / "inference_routing.jsonl").read_text(encoding="utf-8")
    assert '"target_role": "summarizer"' in routing_log
    assert '"trace_id": "trace-1"' in routing_log


def test_query_tracks_opaque_foundry_routes_and_fallbacks(monkeypatch, tmp_path):
    def fake_send_request(
        *,
        intent_type,
        target_role,
        operating_context,
        latency_class,
        trace_context,
        messages,
    ):
        return FoundryResponse(
            output="planner answer",
            route={
                "provider": "foundry",
                "model": "",
                "latency_ms": 77,
                "tokens": {"input": 50, "output": 25, "total": 75},
                "fallback_chain": [
                    {"provider": "foundry", "model": "planner-a", "status": "failed"},
                    {"provider": "foundry", "model": "planner-b", "status": "success"},
                ],
            },
            trace_id="trace-2",
            policy_annotations={},
        )

    monkeypatch.setattr(smart_router.foundry_client, "send_request", fake_send_request)

    router = _router(monkeypatch, tmp_path)
    result = router.query("Give me a market analysis of sector rotation and volume")

    assert result["classified_as"] == "moderate"
    assert result["tier_used"] == "foundry"
    assert result["fallback_chain"] == [
        {
            "provider": "foundry",
            "model": "planner-a",
            "status": "failed",
            "role": "planner",
            "tier": "cheap",
        },
        {
            "provider": "foundry",
            "model": "planner-b",
            "status": "success",
            "role": "planner",
            "tier": "cheap",
        },
    ]

    stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert stats["tier_counts"]["foundry"] == 1
    assert stats["tier_fallbacks"]["foundry_managed"] == 1
    assert stats["estimated_cost"] == pytest.approx(0.00001125)
    assert stats["baseline_cost"] == pytest.approx(0.000375)

    report = router.daily_cost_report()
    assert "Foundry (opaque):        1 (100.0%)" in report
    assert "foundry_managed: 1" in report


def test_query_returns_legacy_failure_shape_when_foundry_raises(monkeypatch, tmp_path):
    def fake_send_request(
        *,
        intent_type,
        target_role,
        operating_context,
        latency_class,
        trace_context,
        messages,
    ):
        raise RuntimeError("router unavailable")

    monkeypatch.setattr(smart_router.foundry_client, "send_request", fake_send_request)

    router = _router(monkeypatch, tmp_path)
    result = router.query("Implement a hedge plan for portfolio risk", complexity="complex")

    assert result["classified_as"] == "complex"
    assert result["tier_used"] == "none"
    assert result["response"] == "[ALL TIERS FAILED] Last error: router unavailable"
    assert result["fallback_chain"] == [
        {
            "tier": "foundry",
            "role": "critic",
            "status": "failed",
            "error": "router unavailable",
        }
    ]
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0

    stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert stats["total_queries"] == 1
    assert stats["estimated_cost"] == 0.0
    assert stats["baseline_cost"] == 0.0
    assert stats["tier_counts"]["free"] == 0
    assert stats["tier_counts"]["cheap"] == 0
    assert stats["tier_counts"]["premium"] == 0
    assert stats["tier_counts"]["foundry"] == 0
