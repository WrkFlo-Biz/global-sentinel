#!/usr/bin/env python3
"""Canary observability report builder for Global Sentinel.

Builds a read-only report over evidence-only canary artifacts so operators can
inspect divergence, rollback posture, persistence mode, and config identity
before or during a canary observation window.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.blob_persistence_health import BlobPersistenceHealthChecker
from src.core.market_session_classifier import MarketSessionClassifier
from src.core.promotion_policy_loader import load_promotion_policy
from src.core.rollback_telemetry import RollbackTelemetryCollector
from src.replay.decision_replay_runner import DecisionReplayRunner

logger = logging.getLogger(__name__)

POLICY_GATES = frozenset({"policy_check", "guardrail_check", "frozen_mode", "promotion_blocked"})
MATURITY_GATES = frozenset({"min_eval_days", "min_trade_count"})
SIGNAL_QUALITY_GATES = frozenset({"max_drawdown_delta", "min_win_delta", "max_failure_rate"})
SESSION_SENSITIVE_GATES = frozenset({"max_drawdown_delta", "min_win_delta", "max_failure_rate"})
LIQUIDITY_SESSIONS = frozenset({"overnight", "pre_market", "after_hours"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class CanaryObservation:
    """Normalized view of a single canary evidence artifact."""

    artifact_path: str
    timestamp_utc: str
    schema_version: str
    signal_type: str
    current_mode: str
    canary_evidence_only: bool
    promotion_allowed_if_not_canary: bool
    rollback_recommended: bool
    reason: str
    persistence_mode: str
    config_fingerprint: str
    divergence_state: str
    baseline_metrics_compared: int
    max_abs_delta_pct: float
    regressed_metrics: List[str]
    market_session: str = ""
    degraded_count: int = 0
    blocked_count: int = 0
    eval_days: int = 0
    trade_count: int = 0
    failure_rate: float = 0.0
    failed_gates: List[str] = field(default_factory=list)
    dominant_failure_category: str = ""
    degraded_scorecard_contribution: bool = False
    degraded_scorecard_flag: bool = False
    session_liquidity_contribution: bool = False
    overnight_condition_flag: bool = False
    session_constraints: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CanaryObservabilityReportBuilder:
    """Summarize canary evidence artifacts for operators and replay review."""

    def __init__(
        self,
        repo_root: Path,
        *,
        report_dirs: Optional[Sequence[Path]] = None,
    ):
        self.repo_root = repo_root
        self.report_dirs = list(
            report_dirs
            or (
                repo_root / "reports" / "research",
                repo_root / "reports" / "operational",
                repo_root / "artifacts",
            )
        )

    def build_report(
        self,
        *,
        start_utc: Optional[str] = None,
        end_utc: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Build a report over the most recent canary evidence artifacts."""
        policy = load_promotion_policy(self.repo_root / "config" / "promotion_policy.yaml")
        blob_health = BlobPersistenceHealthChecker(self.repo_root).check().to_dict()
        rollback = RollbackTelemetryCollector(self.repo_root).collect().to_dict()
        config_consistency = DecisionReplayRunner(self.repo_root).verify_config_consistency(limit=limit)

        payloads = self._load_canary_artifacts(
            start_utc=start_utc,
            end_utc=end_utc,
            limit=limit,
        )
        observations = [
            self._to_observation(
                path=path,
                payload=payload,
                max_regression_pct=policy.canary_policy.max_regression_pct,
                default_persistence_mode=str(blob_health.get("persistence_mode", "unknown")),
            )
            for path, payload in payloads
        ]

        reasons = Counter(
            observation.reason for observation in observations if observation.reason
        )
        rollback_reasons = Counter(
            observation.reason
            for observation in observations
            if observation.rollback_recommended and observation.reason
        )
        persistence_modes = Counter(
            observation.persistence_mode or "unknown" for observation in observations
        )
        divergence_states = Counter(
            observation.divergence_state for observation in observations
        )
        config_fingerprints = Counter(
            observation.config_fingerprint or "missing" for observation in observations
        )
        session_counts = Counter(
            observation.market_session or "unknown" for observation in observations
        )
        failure_categories = Counter(
            observation.dominant_failure_category or "unknown" for observation in observations
        )
        session_breakdown: Dict[str, Dict[str, Any]] = {}
        for session, session_observations in self._group_by_session(observations).items():
            session_breakdown[session] = {
                "artifact_count": len(session_observations),
                "rollback_recommended_count": sum(
                    1 for observation in session_observations if observation.rollback_recommended
                ),
                "avg_trade_count": round(
                    sum(observation.trade_count for observation in session_observations)
                    / max(len(session_observations), 1),
                    6,
                ),
                "avg_eval_days": round(
                    sum(observation.eval_days for observation in session_observations)
                    / max(len(session_observations), 1),
                    6,
                ),
                "degraded_contribution_ratio": round(
                    sum(
                        1
                        for observation in session_observations
                        if observation.degraded_scorecard_contribution
                    )
                    / max(len(session_observations), 1),
                    6,
                ),
                "liquidity_contribution_ratio": round(
                    sum(
                        1
                        for observation in session_observations
                        if observation.session_liquidity_contribution
                    )
                    / max(len(session_observations), 1),
                    6,
                ),
                "dominant_failure_category": self._dominant_counter_key(
                    Counter(
                        observation.dominant_failure_category or "unknown"
                        for observation in session_observations
                    )
                ),
                "session_constraints": session_observations[-1].session_constraints if session_observations else {},
            }
        overnight_condition_count = sum(
            1 for observation in observations if observation.overnight_condition_flag
        )
        max_abs_delta_pct = max(
            (observation.max_abs_delta_pct for observation in observations),
            default=0.0,
        )
        baseline_divergence_count = sum(
            1 for observation in observations if observation.divergence_state != "not_available"
        )
        regression_count = sum(
            1 for observation in observations if observation.divergence_state == "regression"
        )
        config_fingerprint_consistent = (
            len([value for value in config_fingerprints if value != "missing"]) <= 1
        )

        return {
            "schema_version": "canary_observability_report.v1",
            "generated_at": _utc_now_iso(),
            "period": {
                "start": start_utc,
                "end": end_utc,
                "artifacts_analyzed": len(observations),
            },
            "policy_context": {
                "schema_version": policy.schema_version,
                "canary_policy": {
                    "min_improvement_pct": policy.canary_policy.min_improvement_pct,
                    "max_regression_pct": policy.canary_policy.max_regression_pct,
                    "min_sample_size": policy.canary_policy.min_sample_size,
                    "confidence_level": policy.canary_policy.confidence_level,
                },
                "rollback_policy": {
                    "max_versions_retained": policy.rollback_policy.max_versions_retained,
                    "auto_rollback_on_drift": policy.rollback_policy.auto_rollback_on_drift,
                    "drift_threshold_for_rollback": policy.rollback_policy.drift_threshold_for_rollback,
                },
            },
            "summary": {
                "total_canary_artifacts": len(observations),
                "promotion_eligible_count": sum(
                    1 for observation in observations if observation.promotion_allowed_if_not_canary
                ),
                "rollback_recommended_count": sum(
                    1 for observation in observations if observation.rollback_recommended
                ),
                "baseline_divergence_count": baseline_divergence_count,
                "baseline_regression_count": regression_count,
                "max_abs_delta_pct": round(max_abs_delta_pct, 6),
                "persistence_modes": dict(persistence_modes),
                "config_fingerprint_consistent": config_fingerprint_consistent,
                "market_sessions": dict(session_counts),
                "dominant_failure_category": self._dominant_counter_key(failure_categories),
                "overnight_condition_count": overnight_condition_count,
            },
            "canary_pass_fail_reasons": dict(reasons),
            "rollback_trigger_reasons": dict(rollback_reasons),
            "failure_category_breakdown": dict(failure_categories),
            "baseline_divergence": {
                "states": dict(divergence_states),
                "max_abs_delta_pct": round(max_abs_delta_pct, 6),
                "regression_count": regression_count,
            },
            "config_fingerprint_state": {
                "values": dict(config_fingerprints),
                "consistent": config_fingerprint_consistent,
                "replay_consistency": config_consistency,
            },
            "persistence_confirmation": {
                "status": blob_health.get("status"),
                "persistence_mode": blob_health.get("persistence_mode"),
                "blob_available": blob_health.get("blob_available"),
                "all_blob_primary": set(persistence_modes.keys()) in (set(), {"blob_primary"}),
            },
            "session_breakdown": session_breakdown,
            "blob_health": blob_health,
            "rollback_telemetry": rollback,
            "trend_summary": self._build_trend_summary(observations),
            "trend_window_summary": self._build_trend_summary(observations),
            "observations": [observation.to_dict() for observation in observations],
        }

    def _load_canary_artifacts(
        self,
        *,
        start_utc: Optional[str],
        end_utc: Optional[str],
        limit: int,
    ) -> List[tuple[Path, Dict[str, Any]]]:
        loaded: List[tuple[Path, Dict[str, Any]]] = []
        seen: set[Path] = set()
        for root in self.report_dirs:
            if not root.exists():
                continue
            for path in root.rglob("*.json"):
                if path in seen:
                    continue
                seen.add(path)
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not self._is_canary_artifact(path, payload):
                    continue
                ts = self._artifact_timestamp(payload, path)
                if start_utc and ts and ts < start_utc:
                    continue
                if end_utc and ts and ts > end_utc:
                    continue
                loaded.append((path, payload))

        loaded.sort(
            key=lambda item: (
                self._artifact_timestamp(item[1], item[0]) or "",
                str(item[0]),
            ),
            reverse=True,
        )
        return loaded[:limit]

    def _is_canary_artifact(self, path: Path, payload: Dict[str, Any]) -> bool:
        name = path.name.lower()
        schema_version = str(payload.get("schema_version", ""))
        if name in {"canary_readiness_report.json", "canary_observability_report.json"}:
            return False
        if schema_version == "canary_comparison.v1":
            return True
        if bool(payload.get("canary_evidence_only")):
            return True
        return "canary_vs_baseline_divergence" in payload

    def _artifact_timestamp(self, payload: Dict[str, Any], path: Path) -> str:
        for key in ("timestamp", "timestamp_utc", "generated_at"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()

    def _to_observation(
        self,
        *,
        path: Path,
        payload: Dict[str, Any],
        max_regression_pct: float,
        default_persistence_mode: str,
    ) -> CanaryObservation:
        divergence_state = "not_available"
        compared = 0
        max_abs_delta_pct = 0.0
        regressed_metrics: List[str] = []

        divergence = payload.get("canary_vs_baseline_divergence", {}) or {}
        if isinstance(divergence, dict) and divergence:
            compared = len(divergence)
            for metric_name, metric_payload in divergence.items():
                try:
                    delta_pct = abs(float(metric_payload.get("delta_pct", 0.0)))
                    max_abs_delta_pct = max(max_abs_delta_pct, delta_pct)
                    raw_delta_pct = float(metric_payload.get("delta_pct", 0.0))
                    if raw_delta_pct < (-1.0 * max_regression_pct):
                        regressed_metrics.append(str(metric_name))
                except (TypeError, ValueError):
                    continue
            if regressed_metrics:
                divergence_state = "regression"
            elif any(float(item.get("delta", 0.0)) > 0 for item in divergence.values() if isinstance(item, dict)):
                divergence_state = "improvement"
            else:
                divergence_state = "stable"
        elif payload.get("schema_version") == "canary_comparison.v1":
            metrics = payload.get("metrics", {}) or {}
            compared = len(metrics)
            try:
                max_abs_delta_pct = abs(float(metrics.get("mean_score_delta", 0.0))) * 100.0
            except (TypeError, ValueError):
                max_abs_delta_pct = 0.0
            recommendation = str(payload.get("promotion_recommendation", "")).lower()
            if recommendation == "reject":
                divergence_state = "regression"
            elif recommendation == "promote":
                divergence_state = "improvement"
            else:
                divergence_state = "stable"

        persistence_mode = (
            str(payload.get("persistence_mode", ""))
            or str((payload.get("metadata", {}) or {}).get("persistence_mode", ""))
            or default_persistence_mode
        )
        config_fingerprint = str(
            payload.get("config_fingerprint")
            or (payload.get("_lineage", {}) or {}).get("config_fingerprint")
            or ""
        )
        session_context = payload.get("session_context") or MarketSessionClassifier().classify(
            payload.get("generated_at") or payload.get("timestamp_utc") or payload.get("timestamp"),
            asset_class="equity",
        ).to_dict()
        eval_metrics = payload.get("eval_metrics", {}) or {}
        failed_gates = self._failed_gates(payload)
        market_session = str((session_context or {}).get("session", ""))
        dominant_failure_category = self._classify_failure_category(
            payload,
            failed_gates=failed_gates,
            session=market_session,
        )
        degraded_scorecard_contribution = self._has_degraded_scorecard_contribution(payload)
        session_liquidity_contribution = self._has_session_liquidity_driver(
            payload,
            failed_gates=failed_gates,
            session=market_session,
        )
        overnight_condition_flag = market_session == "overnight"

        return CanaryObservation(
            artifact_path=str(path),
            timestamp_utc=self._artifact_timestamp(payload, path),
            schema_version=str(payload.get("schema_version", "")),
            signal_type=str(payload.get("signal_type", "")),
            current_mode=str(payload.get("current_mode", "")),
            canary_evidence_only=bool(payload.get("canary_evidence_only", False)),
            promotion_allowed_if_not_canary=bool(payload.get("promotion_allowed_if_not_canary", False)),
            rollback_recommended=bool(payload.get("rollback_recommended", False)),
            reason=str(payload.get("reason", "")),
            persistence_mode=persistence_mode or default_persistence_mode,
            config_fingerprint=config_fingerprint,
            divergence_state=divergence_state,
            baseline_metrics_compared=compared,
            max_abs_delta_pct=round(max_abs_delta_pct, 6),
            regressed_metrics=sorted(regressed_metrics),
            market_session=market_session,
            degraded_count=int(eval_metrics.get("degraded_count", 0) or 0),
            blocked_count=int(eval_metrics.get("blocked_count", 0) or 0),
            eval_days=int(eval_metrics.get("eval_days", 0) or 0),
            trade_count=int(eval_metrics.get("trade_count", 0) or 0),
            failure_rate=float(eval_metrics.get("failure_rate", 0.0) or 0.0),
            failed_gates=failed_gates,
            dominant_failure_category=dominant_failure_category,
            degraded_scorecard_contribution=degraded_scorecard_contribution,
            degraded_scorecard_flag=degraded_scorecard_contribution,
            session_liquidity_contribution=session_liquidity_contribution,
            overnight_condition_flag=overnight_condition_flag,
            session_constraints=dict((session_context or {}).get("constraints", {}) or {}),
        )

    def _group_by_session(self, observations: Sequence[CanaryObservation]) -> Dict[str, List[CanaryObservation]]:
        grouped: Dict[str, List[CanaryObservation]] = {}
        for observation in observations:
            session = observation.market_session or "unknown"
            grouped.setdefault(session, []).append(observation)
        return grouped

    def _failed_gates(self, payload: Dict[str, Any]) -> List[str]:
        return [
            str(item.get("gate", ""))
            for item in payload.get("gate_results", [])
            if not item.get("passed")
        ]

    def _classify_failure_category(
        self,
        payload: Dict[str, Any],
        *,
        failed_gates: Optional[Iterable[str]] = None,
        session: str = "unknown",
    ) -> str:
        failed_gate_set = set(failed_gates or self._failed_gates(payload))
        if not failed_gate_set and not payload.get("rollback_recommended"):
            return "no_material_failure"
        if failed_gate_set & POLICY_GATES:
            return "policy_gated_failure"
        if failed_gate_set & MATURITY_GATES:
            return "insufficient_evidence_maturity"
        if self._has_degraded_scorecard_contribution(payload):
            return "degraded_scorecard_runtime_quality_issue"
        if self._has_session_liquidity_driver(payload, failed_gates=failed_gate_set, session=session):
            return "market_session_liquidity_issue"
        if failed_gate_set & SIGNAL_QUALITY_GATES:
            return "true_canary_weakness"
        return "mixed_or_unclassified"

    def _has_degraded_scorecard_contribution(self, payload: Dict[str, Any]) -> bool:
        eval_metrics = payload.get("eval_metrics", {}) or {}
        return bool(
            eval_metrics.get("runtime_degraded_driver")
            or float(eval_metrics.get("degraded_rate", 0.0)) >= 0.25
            or int(eval_metrics.get("degraded_count", 0) or 0) > 0
        )

    def _has_session_liquidity_driver(
        self,
        payload: Dict[str, Any],
        *,
        failed_gates: Optional[Iterable[str]] = None,
        session: str = "unknown",
    ) -> bool:
        if session not in LIQUIDITY_SESSIONS:
            return False
        eval_metrics = payload.get("eval_metrics", {}) or {}
        failed_gate_set = set(failed_gates or self._failed_gates(payload))
        if failed_gate_set & SESSION_SENSITIVE_GATES:
            return True
        return bool(
            float(eval_metrics.get("blocked_rate", 0.0)) > 0.0
            or float(eval_metrics.get("degraded_rate", 0.0)) > 0.0
        )

    def _dominant_counter_key(self, counter_payload: Counter[str]) -> str:
        if not counter_payload:
            return "none"
        return counter_payload.most_common(1)[0][0]

    def _metric_trend(self, observations: Sequence[CanaryObservation], field_name: str) -> Dict[str, Any]:
        if not observations:
            return {"direction": "stable", "start": None, "end": None, "delta": 0.0}
        start = float(getattr(observations[-1], field_name) or 0.0)
        end = float(getattr(observations[0], field_name) or 0.0)
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

    def _rollback_trend(self, observations: Sequence[CanaryObservation]) -> Dict[str, Any]:
        if not observations:
            return {"direction": "stable", "start": None, "end": None, "changed": False}
        start = bool(observations[-1].rollback_recommended)
        end = bool(observations[0].rollback_recommended)
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

    def _build_trend_summary(self, observations: Sequence[CanaryObservation]) -> Dict[str, Any]:
        return {
            "trade_count": self._metric_trend(observations, "trade_count"),
            "eval_days": self._metric_trend(observations, "eval_days"),
            "failure_rate": self._metric_trend(observations, "failure_rate"),
            "rollback_recommended": self._rollback_trend(observations),
            "degraded_driver_share": round(
                sum(1 for observation in observations if observation.degraded_scorecard_contribution)
                / max(len(observations), 1),
                6,
            ),
            "session_liquidity_share": round(
                sum(1 for observation in observations if observation.session_liquidity_contribution)
                / max(len(observations), 1),
                6,
            ),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a canary observability report")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    builder = CanaryObservabilityReportBuilder(Path(args.repo_root).resolve())
    report = builder.build_report(
        start_utc=args.start_utc,
        end_utc=args.end_utc,
        limit=args.limit,
    )

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(out)
        return

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
