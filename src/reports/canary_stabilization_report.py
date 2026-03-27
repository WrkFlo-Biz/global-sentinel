#!/usr/bin/env python3
"""Stabilization-window report for evidence-only canary observations."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.core.blob_persistence_health import BlobPersistenceHealthChecker
from src.core.market_session_classifier import MarketSessionClassifier

POLICY_GATES = frozenset({"policy_check", "guardrail_check", "frozen_mode", "promotion_blocked"})
MATURITY_GATES = frozenset({"min_eval_days", "min_trade_count"})
SIGNAL_QUALITY_GATES = frozenset({"max_drawdown_delta", "min_win_delta", "max_failure_rate"})
SESSION_SENSITIVE_GATES = frozenset({"max_drawdown_delta", "min_win_delta", "max_failure_rate"})
LIQUIDITY_SESSIONS = frozenset({"overnight", "pre_market", "after_hours"})
LOWER_IS_BETTER_METRICS = frozenset(
    {
        "drawdown_delta_bps",
        "failure_rate",
        "blocked_rate",
        "degraded_rate",
        "cumulative_drift_std",
        "blocked_count",
        "degraded_count",
    }
)
HIGHER_IS_BETTER_METRICS = frozenset(
    {
        "trade_count",
        "eval_days",
        "slippage_adjusted_win_delta_bps",
        "avg_confidence",
        "avg_regime_shift_probability",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CanaryStabilizationReportBuilder:
    """Aggregate canary artifacts into a stabilization-window review."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.canary_dir = repo_root / "reports" / "research" / "canary"

    def build_report(self, *, limit: int = 50) -> Dict[str, Any]:
        artifacts = self._load_artifacts(limit=limit)
        blob_health = BlobPersistenceHealthChecker(self.repo_root).check().to_dict()
        failure_counter: Counter[str] = Counter()
        failure_category_counter: Counter[str] = Counter()
        divergence_counter: Counter[str] = Counter()
        config_fingerprint_counter: Counter[str] = Counter()
        session_summary: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "artifact_count": 0,
                "rollback_recommended_count": 0,
                "avg_trade_count": 0.0,
                "avg_eval_days": 0.0,
                "avg_degraded_rate": 0.0,
                "avg_failure_rate": 0.0,
                "liquidity_sensitive_count": 0,
                "degraded_driver_count": 0,
                "divergence_states": Counter(),
                "gate_failures": Counter(),
                "failure_categories": Counter(),
                "session_constraints": {},
                "session_interpretation": "",
            }
        )
        trend_rows: List[Dict[str, Any]] = []

        for artifact in artifacts:
            failed_gates = self._failed_gates(artifact)
            failure_counter.update(failed_gates)
            session_context = artifact.get("session_context") or {}
            session = str(session_context.get("session", "unknown"))
            eval_metrics = artifact.get("eval_metrics", {}) or {}
            failure_categories = self._classify_failure_categories(
                artifact,
                failed_gates=failed_gates,
                session=session,
            )
            failure_category = failure_categories[0]
            divergence_state = self._classify_divergence_state(artifact)
            degraded_driver = self._has_degraded_scorecard_contribution(artifact)
            liquidity_driver = self._has_session_liquidity_driver(
                artifact,
                failed_gates=failed_gates,
                session=session,
            )
            failure_category_counter[failure_category] += 1
            for cat in failure_categories:
                failure_category_counter["_multi_" + cat] += 1
            divergence_counter[divergence_state] += 1
            for fingerprint in self._artifact_config_fingerprints(artifact):
                config_fingerprint_counter[fingerprint] += 1

            session_entry = session_summary[session]
            session_entry["artifact_count"] += 1
            session_entry["rollback_recommended_count"] += int(bool(artifact.get("rollback_recommended")))
            session_entry["avg_trade_count"] += float(eval_metrics.get("trade_count", 0.0))
            session_entry["avg_eval_days"] += float(eval_metrics.get("eval_days", 0.0))
            session_entry["avg_degraded_rate"] += float(eval_metrics.get("degraded_rate", 0.0))
            session_entry["avg_failure_rate"] += float(eval_metrics.get("failure_rate", 0.0))
            session_entry["liquidity_sensitive_count"] += int(liquidity_driver)
            session_entry["degraded_driver_count"] += int(degraded_driver)
            session_entry["divergence_states"][divergence_state] += 1
            session_entry["failure_categories"][failure_category] += 1
            session_entry["gate_failures"].update(failed_gates)
            session_entry["session_constraints"] = (
                session_entry["session_constraints"]
                or dict(session_context.get("constraints", {}) or self._default_session_constraints(session))
            )
            session_entry["session_interpretation"] = self._session_interpretation(session)

            trend_rows.append({
                "generated_at": artifact.get("generated_at"),
                "session": session,
                "session_constraints": dict(session_context.get("constraints", {}) or self._default_session_constraints(session)),
                "trade_count": eval_metrics.get("trade_count", 0),
                "eval_days": eval_metrics.get("eval_days", 0),
                "failure_rate": eval_metrics.get("failure_rate", 0.0),
                "blocked_rate": eval_metrics.get("blocked_rate", 0.0),
                "degraded_rate": eval_metrics.get("degraded_rate", 0.0),
                "drawdown_delta_bps": eval_metrics.get("drawdown_delta_bps", 0.0),
                "rollback_recommended": bool(artifact.get("rollback_recommended")),
                "promotion_allowed_if_not_canary": bool(artifact.get("promotion_allowed_if_not_canary")),
                "failed_gates": failed_gates,
                "failure_categories": failure_categories,
                "dominant_failure_category": failure_category,
                "degraded_scorecard_contribution": degraded_driver,
                "session_liquidity_contribution": liquidity_driver,
                "divergence_state": divergence_state,
                "config_fingerprints": self._artifact_config_fingerprints(artifact),
                "reason": artifact.get("reason", ""),
            })

        for session, entry in session_summary.items():
            count = max(entry["artifact_count"], 1)
            entry["avg_trade_count"] = round(entry["avg_trade_count"] / count, 6)
            entry["avg_eval_days"] = round(entry["avg_eval_days"] / count, 6)
            entry["avg_degraded_rate"] = round(entry["avg_degraded_rate"] / count, 6)
            entry["avg_failure_rate"] = round(entry["avg_failure_rate"] / count, 6)
            entry["liquidity_sensitive_ratio"] = round(entry["liquidity_sensitive_count"] / count, 6)
            entry["degraded_driver_ratio"] = round(entry["degraded_driver_count"] / count, 6)
            entry["divergence_states"] = dict(entry["divergence_states"])
            entry["gate_failures"] = dict(entry["gate_failures"])
            entry["failure_categories"] = dict(entry["failure_categories"])
            entry["dominant_failure_category"] = self._dominant_counter_key(entry["failure_categories"])

        latest = trend_rows[-1] if trend_rows else {}
        return {
            "schema_version": "canary_stabilization_report.v1",
            "generated_at": _utc_now_iso(),
            "period": {
                "artifacts_analyzed": len(artifacts),
                "start": artifacts[0].get("generated_at") if artifacts else None,
                "end": artifacts[-1].get("generated_at") if artifacts else None,
            },
            "summary": {
                "artifact_count": len(artifacts),
                "market_sessions": sorted(session_summary.keys()),
                "latest_session": latest.get("session"),
                "latest_trade_count": latest.get("trade_count"),
                "latest_eval_days": latest.get("eval_days"),
                "latest_rollback_recommended": latest.get("rollback_recommended"),
                "rollback_recommended_ratio": round(
                    sum(1 for item in artifacts if item.get("rollback_recommended")) / max(len(artifacts), 1),
                    6,
                ),
                "promotion_eligible_ratio": round(
                    sum(1 for item in artifacts if item.get("promotion_allowed_if_not_canary")) / max(len(artifacts), 1),
                    6,
                ),
                "dominant_failure_category": self._dominant_counter_key(failure_category_counter),
                "degraded_driver_ratio": round(
                    sum(1 for row in trend_rows if row.get("degraded_scorecard_contribution")) / max(len(trend_rows), 1),
                    6,
                ),
            },
            "gate_failure_trends": dict(failure_counter),
            "gate_failure_directions": self._gate_failure_directions(trend_rows),
            "failure_category_breakdown": {
                k: v for k, v in failure_category_counter.items() if not k.startswith("_multi_")
            },
            "failure_category_multi": {
                k[7:]: v for k, v in failure_category_counter.items() if k.startswith("_multi_")
            },
            "divergence_summary": {
                "states": dict(divergence_counter),
                "regression_emerging": self._regression_emerging(trend_rows),
            },
            "config_fingerprint_state": {
                "values": dict(config_fingerprint_counter),
                "consistent": len([value for value in config_fingerprint_counter if value != "missing"]) <= 1,
            },
            "persistence_confirmation": {
                "status": blob_health.get("status"),
                "persistence_mode": blob_health.get("persistence_mode"),
                "blob_available": blob_health.get("blob_available"),
                "local_fallback_available": blob_health.get("local_fallback_available"),
                "fallback_reason": blob_health.get("fallback_reason"),
            },
            "session_analysis": dict(session_summary),
            "trend_rows": trend_rows,
            "trend_summary": self._build_trend_summary(trend_rows),
            "recommendations": self._recommendations(trend_rows, failure_counter),
        }

    def _load_artifacts(self, *, limit: int) -> List[Dict[str, Any]]:
        if not self.canary_dir.exists():
            return []
        loaded: List[Dict[str, Any]] = []
        for path in sorted(self.canary_dir.glob("canary_*.json"))[-limit:]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            payload["_file"] = str(path)
            if payload.get("schema_version") == "evidence_only_canary_artifact.v1":
                # Backfill session context if older artifact lacks it.
                payload.setdefault(
                    "session_context",
                    MarketSessionClassifier().classify(
                        payload.get("generated_at") or (payload.get("current_window") or {}).get("end_utc"),
                        asset_class="equity",
                    ).to_dict(),
                )
                loaded.append(payload)
        return loaded

    def _failed_gates(self, artifact: Dict[str, Any]) -> List[str]:
        return [
            str(item.get("gate", ""))
            for item in artifact.get("gate_results", [])
            if not item.get("passed")
        ]

    def _classify_failure_categories(
        self,
        artifact: Dict[str, Any],
        *,
        failed_gates: Optional[Iterable[str]] = None,
        session: str = "unknown",
    ) -> List[str]:
        """Non-exclusive multi-category classification for one artifact."""
        failed_gate_set = set(failed_gates or self._failed_gates(artifact))
        eval_metrics = artifact.get("eval_metrics", {}) or {}
        if not failed_gate_set and not artifact.get("rollback_recommended"):
            return ["no_material_failure"]
        categories: List[str] = []
        if failed_gate_set & MATURITY_GATES:
            categories.append("insufficient_evidence_maturity")
        if self._has_degraded_scorecard_contribution(artifact):
            categories.append("degraded_scorecard_runtime_quality_issue")
        if failed_gate_set & POLICY_GATES:
            categories.append("policy_gated_failure")
        if self._has_session_liquidity_driver(artifact, failed_gates=failed_gate_set, session=session):
            categories.append("market_session_liquidity_issue")
        # Proxy artifact: drawdown_delta stuck at boundary value
        dd = float(eval_metrics.get("drawdown_delta_bps", 0))
        if "max_drawdown_delta" in failed_gate_set and dd == 100.0:
            categories.append("proxy_artifact")
        # True weakness: signal gates fail without degraded runtime as driver
        signal_fails = failed_gate_set & SIGNAL_QUALITY_GATES
        if signal_fails and not bool(eval_metrics.get("runtime_degraded_driver")):
            # Exclude if already explained by proxy artifact
            if "proxy_artifact" not in categories:
                categories.append("true_canary_weakness")
        return categories or ["mixed_or_unclassified"]

    def _classify_failure_category(
        self,
        artifact: Dict[str, Any],
        *,
        failed_gates: Optional[Iterable[str]] = None,
        session: str = "unknown",
    ) -> str:
        """Dominant single-category classification (first non-exclusive category)."""
        cats = self._classify_failure_categories(
            artifact, failed_gates=failed_gates, session=session
        )
        return cats[0]

    def _has_degraded_scorecard_contribution(self, artifact: Dict[str, Any]) -> bool:
        eval_metrics = artifact.get("eval_metrics", {}) or {}
        return bool(
            eval_metrics.get("runtime_degraded_driver")
            or float(eval_metrics.get("degraded_rate", 0.0)) >= 0.25
            or int(eval_metrics.get("degraded_count", 0) or 0) > 0
        )

    def _has_session_liquidity_driver(
        self,
        artifact: Dict[str, Any],
        *,
        failed_gates: Optional[Iterable[str]] = None,
        session: str = "unknown",
    ) -> bool:
        eval_metrics = artifact.get("eval_metrics", {}) or {}
        failed_gate_set = set(failed_gates or self._failed_gates(artifact))
        if session not in LIQUIDITY_SESSIONS:
            return False
        if failed_gate_set & SESSION_SENSITIVE_GATES:
            return True
        return bool(
            float(eval_metrics.get("blocked_rate", 0.0)) > 0.0
            or float(eval_metrics.get("degraded_rate", 0.0)) > 0.0
        )

    def _classify_divergence_state(self, artifact: Dict[str, Any]) -> str:
        divergence = artifact.get("canary_vs_baseline_divergence", {}) or {}
        if not isinstance(divergence, dict) or not divergence:
            return "not_available"

        improvements = 0
        regressions = 0
        for metric_name, values in divergence.items():
            if not isinstance(values, dict):
                continue
            try:
                delta = float(values.get("delta", 0.0))
            except (TypeError, ValueError):
                continue
            if metric_name in HIGHER_IS_BETTER_METRICS:
                if delta > 0:
                    improvements += 1
                elif delta < 0:
                    regressions += 1
            elif metric_name in LOWER_IS_BETTER_METRICS:
                if delta < 0:
                    improvements += 1
                elif delta > 0:
                    regressions += 1

        if regressions:
            return "regression"
        if improvements:
            return "improvement"
        return "stable"

    def _artifact_config_fingerprints(self, artifact: Dict[str, Any]) -> List[str]:
        direct = artifact.get("config_fingerprint")
        lineage = artifact.get("_lineage", {}) or {}
        fingerprints: List[str] = []
        if direct:
            fingerprints.append(str(direct))
        lineage_direct = lineage.get("config_fingerprint")
        if lineage_direct:
            fingerprints.append(str(lineage_direct))
        for value in lineage.get("config_fingerprints", []) or []:
            if value:
                fingerprints.append(str(value))
        seen: List[str] = []
        for value in fingerprints or ["missing"]:
            if value not in seen:
                seen.append(value)
        return seen

    def _default_session_constraints(self, session: str) -> Dict[str, Any]:
        if session == "overnight":
            return {
                "limit_only": True,
                "allowed_time_in_force": ["day"],
                "requires_overnight_tradable": True,
                "requires_not_overnight_halted": True,
            }
        if session in {"pre_market", "after_hours"}:
            return {
                "extended_hours": True,
                "allowed_time_in_force": ["day", "gtc"],
            }
        if session == "regular":
            return {
                "extended_hours": False,
                "allowed_time_in_force": ["day", "gtc", "ioc", "fok", "opg", "cls"],
            }
        if session == "continuous":
            return {
                "alpaca_crypto_continuous": True,
                "allowed_time_in_force": ["gtc", "ioc"],
            }
        return {}

    def _session_interpretation(self, session: str) -> str:
        if session == "overnight":
            return "Lower-liquidity overnight session; interpret failures with Alpaca limit-only and DAY-only constraints in mind."
        if session in {"pre_market", "after_hours"}:
            return "Extended-hours session; spreads and fills can be less reliable than regular hours."
        if session == "continuous":
            return "Crypto 24/7 session; compare separately from equity sessions."
        if session == "regular":
            return "Primary equity session; failures are less likely to be session-liquidity artifacts."
        return "Session context unavailable or closed; treat evidence cautiously."

    def _dominant_counter_key(self, counter_payload: Dict[str, int] | Counter[str]) -> str:
        if not counter_payload:
            return "none"
        return Counter(counter_payload).most_common(1)[0][0]

    def _metric_trend(self, rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
        if not rows:
            return {"direction": "stable", "start": None, "end": None, "delta": 0.0}
        start = float(rows[0].get(key, 0.0) or 0.0)
        end = float(rows[-1].get(key, 0.0) or 0.0)
        delta = round(end - start, 6)
        if abs(delta) < 1e-9:
            direction = "stable"
        else:
            direction = "up" if delta > 0 else "down"
        return {
            "direction": direction,
            "start": round(start, 6),
            "end": round(end, 6),
            "delta": delta,
        }

    def _rollback_trend(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"direction": "stable", "start": None, "end": None, "changed": False}
        start = bool(rows[0].get("rollback_recommended"))
        end = bool(rows[-1].get("rollback_recommended"))
        if start == end:
            direction = "stable"
        elif start and not end:
            direction = "improving"
        else:
            direction = "worsening"
        return {
            "direction": direction,
            "start": start,
            "end": end,
            "changed": start != end,
        }

    def _gate_failure_directions(self, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int | str]]:
        if not rows:
            return {}
        split = max(1, len(rows) // 2)
        earlier = rows[:split]
        recent = rows[split:]
        earlier_counts = Counter(
            gate
            for row in earlier
            for gate in row.get("failed_gates", [])
        )
        recent_counts = Counter(
            gate
            for row in recent
            for gate in row.get("failed_gates", [])
        )
        directions: Dict[str, Dict[str, int | str]] = {}
        for gate in sorted(set(earlier_counts) | set(recent_counts)):
            if recent_counts[gate] > earlier_counts[gate]:
                direction = "worsening"
            elif recent_counts[gate] < earlier_counts[gate]:
                direction = "improving"
            else:
                direction = "stable"
            directions[gate] = {
                "earlier_count": earlier_counts[gate],
                "recent_count": recent_counts[gate],
                "direction": direction,
            }
        return directions

    def _regression_emerging(self, rows: List[Dict[str, Any]]) -> bool:
        if len(rows) < 2:
            return bool(rows and rows[-1].get("divergence_state") == "regression")
        recent = rows[-min(3, len(rows)):]
        earlier = rows[:-len(recent)]
        recent_rate = sum(1 for row in recent if row.get("divergence_state") == "regression") / max(len(recent), 1)
        earlier_rate = sum(1 for row in earlier if row.get("divergence_state") == "regression") / max(len(earlier), 1)
        return recent_rate > earlier_rate and recent_rate > 0.0

    def _build_trend_summary(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        dominant_categories = Counter(
            str(row.get("dominant_failure_category", "unknown"))
            for row in rows
            if row.get("dominant_failure_category")
        )
        return {
            "trade_count": self._metric_trend(rows, "trade_count"),
            "eval_days": self._metric_trend(rows, "eval_days"),
            "failure_rate": self._metric_trend(rows, "failure_rate"),
            "degraded_rate": self._metric_trend(rows, "degraded_rate"),
            "drawdown_delta_bps": self._metric_trend(rows, "drawdown_delta_bps"),
            "rollback_recommended": self._rollback_trend(rows),
            "dominant_failure_category": self._dominant_counter_key(dominant_categories),
            "degraded_driver_share": round(
                sum(1 for row in rows if row.get("degraded_scorecard_contribution")) / max(len(rows), 1),
                6,
            ),
            "session_liquidity_share": round(
                sum(1 for row in rows if row.get("session_liquidity_contribution")) / max(len(rows), 1),
                6,
            ),
            "regression_emerging": self._regression_emerging(rows),
        }

    def _recommendations(
        self,
        trend_rows: List[Dict[str, Any]],
        failure_counter: Counter[str],
    ) -> List[str]:
        recommendations: List[str] = []
        if failure_counter.get("min_eval_days", 0) > 0 or failure_counter.get("min_trade_count", 0) > 0:
            recommendations.append("Keep canary in stabilization until eval_days and trade_count accumulate materially.")
        if failure_counter.get("policy_check", 0) > 0:
            recommendations.append("Review weight-promotion deltas separately from canary quality; current policy gate is correctly blocking promotion.")
        if any(float(row.get("degraded_rate", 0.0)) > 0.5 for row in trend_rows):
            recommendations.append("Degraded scorecards dominate current canary failures; treat runtime quality separately from signal quality.")
        if any("proxy_artifact" in (row.get("failure_categories") or []) for row in trend_rows):
            recommendations.append("drawdown_delta_bps=100.0 is a proxy/calculation artifact (not real drawdown); gate will self-resolve as trade evidence accumulates.")
        if any(bool(row.get("session_liquidity_contribution")) for row in trend_rows):
            recommendations.append("Extended-hours failures are present; interpret overnight and extended-hours artifacts separately from regular-session evidence.")
        if self._regression_emerging(trend_rows):
            recommendations.append("Baseline regression signals are emerging in the latest canary batch; inspect whether session/liquidity effects explain the drift.")
        if not recommendations:
            recommendations.append("Continue evidence-only observation; no immediate stabilization blockers detected.")
        return recommendations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a canary stabilization report")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    builder = CanaryStabilizationReportBuilder(Path(args.repo_root).resolve())
    report = builder.build_report(limit=args.limit)
    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(out)
        return
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
