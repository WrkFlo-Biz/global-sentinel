#!/usr/bin/env python3
"""
Global Sentinel - Smart Inference Router

Deprecated compatibility shim that preserves the legacy smart router surface
while routing through ``src.inference.foundry_client``.

No in-tree runtime callers remain. New code should call
``src.inference.foundry_client.send_request()`` directly.
"""

from __future__ import annotations

import json
import logging
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from src.inference import foundry_client
except ModuleNotFoundError:
    import sys

    # Preserve direct ``python src/monitoring/smart_inference_router.py`` usage.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.inference import foundry_client

logger = logging.getLogger("global_sentinel.smart_inference_router")

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", str(DEFAULT_REPO_ROOT)))
STATS_PATH = Path(os.getenv("GS_INFERENCE_STATS_PATH", "/tmp/gs_inference_stats.json"))
ROUTING_LOG_PATH = Path(
    os.getenv("GS_INFERENCE_ROUTING_LOG_PATH", str(REPO_ROOT / "logs" / "inference_routing.jsonl"))
)

DEPRECATION_MESSAGE = (
    "SmartInferenceRouter is deprecated and has no in-tree callers; use "
    "src.inference.foundry_client.send_request() directly."
)

# Conservative cost estimates per 1K tokens (input/output avg).
BASELINE_COST_PER_1K = 0.005
TIER_COSTS = {
    "free": 0.0,
    "cheap": 0.00015,
    "premium": BASELINE_COST_PER_1K,
    "foundry": BASELINE_COST_PER_1K,
}

TARGET_ROLE_BY_COMPLEXITY = {
    "simple": foundry_client.TargetRole.SUMMARIZER.value,
    "moderate": foundry_client.TargetRole.PLANNER.value,
    "complex": foundry_client.TargetRole.CRITIC.value,
}
LATENCY_CLASS_BY_COMPLEXITY = {
    "simple": "interactive",
    "moderate": "interactive",
    "complex": "premium",
}
DEFAULT_COST_TIER_BY_COMPLEXITY = {
    "simple": "cheap",
    "moderate": "cheap",
    "complex": "premium",
}

# Classification keywords.
SIMPLE_KEYWORDS = [
    "summarize",
    "format",
    "list",
    "what is",
    "define",
    "explain briefly",
    "reformat",
    "bullet",
    "translate",
    "count",
    "convert",
]
MODERATE_KEYWORDS = [
    "market analysis",
    "signal",
    "interpret",
    "data",
    "analyze",
    "chart",
    "trend",
    "momentum",
    "volume",
    "sector",
    "earnings",
    "thesis",
    "catalyst",
    "valuation",
    "sentiment",
]
COMPLEX_KEYWORDS = [
    "trade",
    "execute",
    "critical",
    "strategy decision",
    "code",
    "multi-step",
    "plan",
    "refactor",
    "implement",
    "debug",
    "portfolio rebalance",
    "risk assessment",
    "hedge",
]


def _classify(prompt: str) -> str:
    """Auto-classify prompt complexity."""
    lower = prompt.lower().strip()
    length = len(prompt)

    for keyword in COMPLEX_KEYWORDS:
        if keyword in lower:
            return "complex"

    for keyword in MODERATE_KEYWORDS:
        if keyword in lower:
            return "moderate"

    if length < 200:
        for keyword in SIMPLE_KEYWORDS:
            if keyword in lower:
                return "simple"
        if length < 80:
            return "simple"

    if length < 200:
        return "simple"
    if length < 600:
        return "moderate"
    return "complex"


class SmartInferenceRouter:
    """Legacy compatibility wrapper around the shared Foundry client."""

    _deprecation_notice_emitted = False

    def __init__(self):
        self._emit_deprecation_notice()
        ROUTING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._load_stats()

    def _emit_deprecation_notice(self) -> None:
        if self.__class__._deprecation_notice_emitted:
            return

        warnings.warn(DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        logger.info(DEPRECATION_MESSAGE)
        self.__class__._deprecation_notice_emitted = True

    # Stats persistence.

    def _load_stats(self) -> None:
        if not STATS_PATH.exists():
            self.stats = self._empty_stats()
            return

        try:
            payload = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            self.stats = self._empty_stats()
            return

        self.stats = self._normalize_stats(payload)

    def _empty_stats(self) -> dict[str, Any]:
        return {
            "total_queries": 0,
            "tier_counts": {"free": 0, "cheap": 0, "premium": 0, "foundry": 0},
            "tier_fallbacks": {
                "free_to_cheap": 0,
                "free_to_premium": 0,
                "cheap_to_premium": 0,
                "foundry_managed": 0,
            },
            "estimated_cost": 0.0,
            "baseline_cost": 0.0,
            "savings": 0.0,
            "last_reset": datetime.now(timezone.utc).isoformat(),
        }

    def _normalize_stats(self, payload: Any) -> dict[str, Any]:
        stats = self._empty_stats()
        if not isinstance(payload, Mapping):
            return stats

        total_queries = _coerce_int(payload.get("total_queries"))
        if total_queries is not None:
            stats["total_queries"] = total_queries

        for key in ("estimated_cost", "baseline_cost", "savings"):
            value = _coerce_float(payload.get(key))
            if value is not None:
                stats[key] = value

        last_reset = payload.get("last_reset")
        if isinstance(last_reset, str) and last_reset.strip():
            stats["last_reset"] = last_reset

        tier_counts = payload.get("tier_counts")
        if isinstance(tier_counts, Mapping):
            for key, value in tier_counts.items():
                normalized = _coerce_int(value)
                if normalized is not None:
                    stats["tier_counts"][str(key)] = normalized

        fallbacks = payload.get("tier_fallbacks")
        if isinstance(fallbacks, Mapping):
            for key, value in fallbacks.items():
                normalized = _coerce_int(value)
                if normalized is not None:
                    stats["tier_fallbacks"][str(key)] = normalized

        return stats

    def _save_stats(self) -> None:
        try:
            STATS_PATH.write_text(json.dumps(self.stats, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save inference stats: %s", exc)

    def _log_routing(self, entry: dict[str, Any]) -> None:
        try:
            with ROUTING_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.warning("Failed to log routing decision: %s", exc)

    # Main routing method.

    def query(
        self,
        prompt: str,
        system: str = "",
        complexity: str = "auto",
    ) -> dict[str, Any]:
        """
        Route a query through the shared Foundry client.

        Returns:
            dict with keys: response, tier_used, classified_as, fallback_chain, latency_ms
        """

        classified = _normalize_complexity(prompt=prompt, complexity=complexity)
        target_role = TARGET_ROLE_BY_COMPLEXITY[classified]
        latency_class = LATENCY_CLASS_BY_COMPLEXITY[classified]
        start = time.perf_counter()

        route: dict[str, Any] = {}
        trace_id = ""
        policy_annotations: dict[str, Any] = {}

        try:
            result = foundry_client.send_request(
                intent_type="legacy_smart_router",
                target_role=target_role,
                operating_context={
                    "source": "smart_inference_router",
                    "legacy_complexity": classified,
                },
                latency_class=latency_class,
                trace_context={"source": "smart_inference_router"},
                messages=_build_messages(prompt=prompt, system=system),
            )
            response_text = result.output
            route = dict(result.route)
            trace_id = result.trace_id
            policy_annotations = dict(result.policy_annotations)
            latency_ms = _coerce_int(route.get("latency_ms")) or _elapsed_ms(start)
            tier_used = _infer_legacy_tier(route=route, classified=classified)
            fallback_chain = _normalize_fallback_chain(
                route=route,
                target_role=target_role,
                tier_used=tier_used,
            )
        except Exception as exc:
            response_text = f"[ALL TIERS FAILED] Last error: {exc}"
            latency_ms = _elapsed_ms(start)
            tier_used = "none"
            fallback_chain = [
                {
                    "tier": "foundry",
                    "role": target_role,
                    "status": "failed",
                    "error": str(exc)[:200],
                }
            ]
            logger.warning("Foundry request failed in smart router shim: %s", exc)

        token_count = _route_token_total(route) or _estimate_tokens(prompt=prompt, response=response_text)
        cost_basis_tier = _cost_basis_tier(tier_used=tier_used, classified=classified)
        actual_cost, baseline_cost = _estimate_costs(
            token_count=token_count,
            tier_used=tier_used,
            cost_basis_tier=cost_basis_tier,
        )

        self.stats["total_queries"] += 1
        if tier_used in self.stats["tier_counts"]:
            self.stats["tier_counts"][tier_used] += 1
        self.stats["estimated_cost"] += actual_cost
        self.stats["baseline_cost"] += baseline_cost
        self.stats["savings"] = self.stats["baseline_cost"] - self.stats["estimated_cost"]

        _record_fallback_stats(self.stats, fallback_chain)
        self._save_stats()

        self._log_routing(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "classified_as": classified,
                "target_role": target_role,
                "latency_class": latency_class,
                "tier_used": tier_used,
                "cost_basis_tier": cost_basis_tier,
                "fallback_chain": fallback_chain,
                "latency_ms": latency_ms,
                "prompt_len": len(prompt),
                "response_len": len(response_text),
                "est_tokens": round(token_count),
                "actual_cost": round(actual_cost, 6),
                "baseline_cost": round(baseline_cost, 6),
                "trace_id": trace_id,
                "route": route,
                "policy_annotations": policy_annotations,
            }
        )

        return {
            "response": response_text,
            "tier_used": tier_used,
            "classified_as": classified,
            "fallback_chain": fallback_chain,
            "latency_ms": latency_ms,
        }

    # Daily cost report.

    def daily_cost_report(self) -> str:
        """Generate a daily cost report string for audit output."""
        stats = self.stats
        total = stats["total_queries"]
        if total == 0:
            return "Inference Router: No queries routed today."

        lines = [
            "=== SMART INFERENCE ROUTER - DAILY COST REPORT ===",
            f"Total queries routed: {total}",
            "",
            "Queries per tier:",
            (
                f"  Tier 1 (Free/Nemotron):  {stats['tier_counts'].get('free', 0)} "
                f"({_pct(stats['tier_counts'].get('free', 0), total)})"
            ),
            (
                f"  Tier 2 (Cheap/gpt-5m):   {stats['tier_counts'].get('cheap', 0)} "
                f"({_pct(stats['tier_counts'].get('cheap', 0), total)})"
            ),
            (
                f"  Tier 3 (Premium/gpt-4o): {stats['tier_counts'].get('premium', 0)} "
                f"({_pct(stats['tier_counts'].get('premium', 0), total)})"
            ),
            (
                f"  Foundry (opaque):        {stats['tier_counts'].get('foundry', 0)} "
                f"({_pct(stats['tier_counts'].get('foundry', 0), total)})"
            ),
            "",
            f"Estimated actual cost:   ${stats['estimated_cost']:.4f}",
            f"Baseline cost (all 4o):  ${stats['baseline_cost']:.4f}",
            (
                "Savings:                 "
                f"${stats['savings']:.4f} "
                f"({_pct(stats['savings'], stats['baseline_cost']) if stats['baseline_cost'] > 0 else '0%'})"
            ),
            "",
            "Fallbacks:",
        ]

        if any(value > 0 for value in stats.get("tier_fallbacks", {}).values()):
            for key, value in stats.get("tier_fallbacks", {}).items():
                if value > 0:
                    lines.append(f"  {key}: {value}")
        else:
            lines.append("  (none)")

        return "\n".join(lines)

    def reset_daily_stats(self) -> None:
        """Reset stats for a new day."""
        self.stats = self._empty_stats()
        self._save_stats()


def _normalize_complexity(prompt: str, complexity: str) -> str:
    value = (complexity or "auto").strip().lower()
    if value == "auto":
        return _classify(prompt)
    if value in TARGET_ROLE_BY_COMPLEXITY:
        return value

    logger.warning("Unknown complexity '%s'; falling back to auto classification", complexity)
    return _classify(prompt)


def _build_messages(prompt: str, system: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _infer_legacy_tier(route: Mapping[str, Any], classified: str) -> str:
    provider = str(route.get("provider") or "").lower()
    model_markers = " ".join(
        str(route.get(key) or "")
        for key in ("model", "deployment", "profile")
    ).lower()
    combined = f"{provider} {model_markers}".strip()

    if any(marker in combined for marker in ("nemotron", "openrouter", ":free", "free-tier")):
        return "free"
    if any(marker in combined for marker in ("mini", "small", "summarizer", "planner")):
        return "cheap"
    if any(marker in combined for marker in ("4o", "claude", "opus", "critic", "premium")):
        return "premium"
    if provider == "azure":
        return DEFAULT_COST_TIER_BY_COMPLEXITY[classified]
    if provider == "foundry":
        return "foundry"
    return "foundry"


def _normalize_fallback_chain(
    route: Mapping[str, Any],
    target_role: str,
    tier_used: str,
) -> list[dict[str, Any]]:
    raw_chain = route.get("fallback_chain")
    if isinstance(raw_chain, list) and raw_chain:
        normalized: list[dict[str, Any]] = []
        for item in raw_chain:
            if not isinstance(item, Mapping):
                continue

            entry = dict(item)
            entry.setdefault("role", target_role)
            entry.setdefault("tier", _infer_legacy_tier(entry, _classify_from_role(target_role)))
            normalized.append(entry)

        if normalized:
            normalized[-1].setdefault("status", "success")
            return normalized

    return [
        {
            "tier": tier_used if tier_used != "none" else "foundry",
            "role": target_role,
            "status": "success" if tier_used != "none" else "failed",
            "provider": route.get("provider") or "foundry",
            "model": route.get("model") or route.get("deployment") or "",
        }
    ]


def _classify_from_role(target_role: str) -> str:
    for complexity, role in TARGET_ROLE_BY_COMPLEXITY.items():
        if role == target_role:
            return complexity
    return "moderate"


def _route_token_total(route: Mapping[str, Any]) -> int | None:
    tokens = route.get("tokens")
    if not isinstance(tokens, Mapping):
        return None

    total = _coerce_int(tokens.get("total"))
    if total is not None:
        return total

    input_tokens = _coerce_int(tokens.get("input"))
    output_tokens = _coerce_int(tokens.get("output"))
    if input_tokens is not None and output_tokens is not None:
        return input_tokens + output_tokens
    return None


def _estimate_tokens(prompt: str, response: str) -> int:
    return round((len(prompt) + len(response)) / 4)


def _cost_basis_tier(tier_used: str, classified: str) -> str:
    if tier_used in ("free", "cheap", "premium"):
        return tier_used
    return DEFAULT_COST_TIER_BY_COMPLEXITY[classified]


def _estimate_costs(token_count: int, tier_used: str, cost_basis_tier: str) -> tuple[float, float]:
    if tier_used == "none":
        return 0.0, 0.0

    est_cost_1k = token_count / 1000
    actual_cost = est_cost_1k * TIER_COSTS.get(cost_basis_tier, BASELINE_COST_PER_1K)
    baseline_cost = est_cost_1k * BASELINE_COST_PER_1K
    return actual_cost, baseline_cost


def _record_fallback_stats(stats: dict[str, Any], fallback_chain: list[dict[str, Any]]) -> None:
    if len(fallback_chain) <= 1:
        return

    first_tier = fallback_chain[0].get("tier")
    final_tier = next(
        (entry.get("tier") for entry in reversed(fallback_chain) if entry.get("status") == "success"),
        fallback_chain[-1].get("tier"),
    )
    if first_tier and final_tier and first_tier != final_tier:
        key = f"{first_tier}_to_{final_tier}"
        stats["tier_fallbacks"][key] = stats["tier_fallbacks"].get(key, 0) + 1
        return

    stats["tier_fallbacks"]["foundry_managed"] = stats["tier_fallbacks"].get("foundry_managed", 0) + 1


def _coerce_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _pct(part: float, total: float) -> str:
    if total == 0:
        return "0%"
    return f"{part / total * 100:.1f}%"


# Singleton for easy import.
_router_instance: Optional[SmartInferenceRouter] = None


def get_router() -> SmartInferenceRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = SmartInferenceRouter()
    return _router_instance


def query(prompt: str, system: str = "", complexity: str = "auto") -> dict[str, Any]:
    """Convenience function: route a query through the singleton router."""
    return get_router().query(prompt, system=system, complexity=complexity)


def daily_cost_report() -> str:
    """Convenience function: get daily cost report."""
    return get_router().daily_cost_report()


# CLI for testing / cron.
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Smart Inference Router")
    parser.add_argument("--query", type=str, help="Test query to route")
    parser.add_argument(
        "--complexity",
        type=str,
        default="auto",
        choices=["simple", "moderate", "complex", "auto"],
    )
    parser.add_argument("--report", action="store_true", help="Print daily cost report")
    parser.add_argument("--reset", action="store_true", help="Reset daily stats")
    args = parser.parse_args()

    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

    router = SmartInferenceRouter()

    if args.reset:
        router.reset_daily_stats()
        print("Stats reset.")
    elif args.report:
        print(router.daily_cost_report())
    elif args.query:
        result = router.query(args.query, complexity=args.complexity)
        print(f"Tier: {result['tier_used']} (classified: {result['classified_as']})")
        print(f"Latency: {result['latency_ms']}ms")
        print(f"Fallbacks: {result['fallback_chain']}")
        print(f"\nResponse:\n{result['response'][:500]}")
    else:
        parser.print_help()
