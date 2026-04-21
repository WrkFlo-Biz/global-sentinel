#!/usr/bin/env python3
"""Operator-facing checkpoint summary for the canary stabilization window.

This report is intentionally read-only. It condenses the readiness, observability,
and stabilization artifacts into one checkpoint artifact that explains whether the
system should keep observing, whether evidence quality is improving, and what the
next operator-facing questions are.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class CanaryStabilizationCheckpointBuilder:
    """Build a concise checkpoint artifact over current canary evidence."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.operational_dir = repo_root / "reports" / "operational"

    def build(self) -> Dict[str, Any]:
        readiness = _read_json(self.operational_dir / "canary_readiness_report.json")
        stabilization = _read_json(self.operational_dir / "canary_stabilization_report.json")
        observability = _read_json(self.operational_dir / "canary_observability_report.json")

        readiness_status = str(readiness.get("readiness_status", "UNKNOWN"))
        stabilization_summary = stabilization.get("summary", {}) or {}
        stabilization_trend = stabilization.get("trend_summary", {}) or {}
        observability_summary = observability.get("summary", {}) or {}
        session_breakdown = observability.get("session_breakdown", {}) or {}
        session_coverage = sorted(session_breakdown.keys()) or list(stabilization_summary.get("market_sessions", []) or [])

        checkpoint_status = self._checkpoint_status(
            readiness_status=readiness_status,
            stabilization_summary=stabilization_summary,
            stabilization_trend=stabilization_trend,
            observability_summary=observability_summary,
            session_coverage=session_coverage,
        )
        evidence_quality = self._evidence_quality(
            stabilization_summary=stabilization_summary,
            stabilization_trend=stabilization_trend,
            observability_summary=observability_summary,
        )
        primary_blockers = self._primary_blockers(
            stabilization=stabilization,
            observability=observability,
        )
        next_questions = self._next_questions(
            stabilization=stabilization,
            observability=observability,
            session_coverage=session_coverage,
        )
        failure_profile = self._failure_profile(
            stabilization=stabilization,
            observability=observability,
        )
        operator_actions = self._operator_actions(
            checkpoint_status=checkpoint_status,
            primary_blockers=primary_blockers,
        )

        return {
            "schema_version": "canary_stabilization_checkpoint.v1",
            "generated_at": _utc_now_iso(),
            "checkpoint_status": checkpoint_status,
            "current_phase": "stabilization_window",
            "readiness_status": readiness_status,
            "evidence_quality": evidence_quality,
            "failure_profile": failure_profile,
            "primary_blockers": primary_blockers,
            "next_questions": next_questions,
            "operator_actions": operator_actions,
            "snapshot": {
                "latest_trade_count": stabilization_summary.get("latest_trade_count"),
                "latest_eval_days": stabilization_summary.get("latest_eval_days"),
                "rollback_recommended_ratio": stabilization_summary.get("rollback_recommended_ratio"),
                "promotion_eligible_ratio": stabilization_summary.get("promotion_eligible_ratio"),
                "dominant_failure_category": stabilization_summary.get("dominant_failure_category"),
                "observability_failure_category": observability_summary.get("dominant_failure_category"),
                "config_fingerprint_consistent": observability_summary.get("config_fingerprint_consistent"),
                "session_coverage": session_coverage,
                "all_blob_primary": (observability.get("persistence_confirmation", {}) or {}).get("all_blob_primary"),
                "failure_profile_primary": failure_profile.get("dominant_profile"),
            },
            "trend_snapshot": {
                "trade_count": stabilization_trend.get("trade_count", {}),
                "eval_days": stabilization_trend.get("eval_days", {}),
                "rollback_recommended": stabilization_trend.get("rollback_recommended", {}),
                "failure_rate": stabilization_trend.get("failure_rate", {}),
                "degraded_driver_share": stabilization_trend.get("degraded_driver_share"),
                "session_liquidity_share": stabilization_trend.get("session_liquidity_share"),
                "regression_emerging": stabilization_trend.get("regression_emerging"),
            },
            "evidence_links": {
                "canary_readiness_report": str(self.operational_dir / "canary_readiness_report.json"),
                "canary_stabilization_report": str(self.operational_dir / "canary_stabilization_report.json"),
                "canary_observability_report": str(self.operational_dir / "canary_observability_report.json"),
            },
        }

    def _checkpoint_status(
        self,
        *,
        readiness_status: str,
        stabilization_summary: Dict[str, Any],
        stabilization_trend: Dict[str, Any],
        observability_summary: Dict[str, Any],
        session_coverage: List[str],
    ) -> str:
        if readiness_status != "GO":
            return "not_ready"
        if not bool((observability_summary.get("config_fingerprint_consistent", False))):
            return "investigate_config_drift"
        if not bool((observability_summary.get("persistence_modes", {}) or {}).get("blob_primary")):
            return "investigate_persistence"
        if str(stabilization_summary.get("dominant_failure_category", "")) == "insufficient_evidence_maturity":
            if len(session_coverage) <= 1:
                return "continue_stabilization_collect_session_coverage"
            return "continue_stabilization_collect_maturity"
        if bool(stabilization_trend.get("regression_emerging")):
            return "continue_stabilization_review_regression"
        return "continue_stabilization"

    def _evidence_quality(
        self,
        *,
        stabilization_summary: Dict[str, Any],
        stabilization_trend: Dict[str, Any],
        observability_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        latest_trade_count = int(stabilization_summary.get("latest_trade_count") or 0)
        latest_eval_days = int(stabilization_summary.get("latest_eval_days") or 0)
        degraded_driver_share = float(stabilization_trend.get("degraded_driver_share") or 0.0)
        fingerprint_consistent = bool(observability_summary.get("config_fingerprint_consistent", False))

        maturity = "early"
        if latest_eval_days >= 5 and latest_trade_count >= 50:
            maturity = "intermediate"
        if latest_eval_days >= 20 and latest_trade_count >= 100:
            maturity = "advanced"

        return {
            "maturity_level": maturity,
            "latest_trade_count": latest_trade_count,
            "latest_eval_days": latest_eval_days,
            "degraded_driver_share": degraded_driver_share,
            "config_fingerprint_consistent": fingerprint_consistent,
        }

    def _failure_profile(
        self,
        *,
        stabilization: Dict[str, Any],
        observability: Dict[str, Any],
    ) -> Dict[str, Any]:
        summary = stabilization.get("summary", {}) or {}
        artifact_count = max(int(summary.get("artifact_count") or 0), 1)
        category_multi = stabilization.get("failure_category_multi", {}) or {}
        trend = stabilization.get("trend_summary", {}) or {}
        session_breakdown = observability.get("session_breakdown", {}) or {}
        policy_overlay_count = int((observability.get("failure_category_breakdown", {}) or {}).get("policy_gated_failure", 0))

        profile_scores = {
            "evidence_immaturity": round(
                float(category_multi.get("insufficient_evidence_maturity", 0)) / artifact_count,
                6,
            ),
            "degraded_runtime_conditions": round(
                max(
                    float(category_multi.get("degraded_scorecard_runtime_quality_issue", 0)) / artifact_count,
                    float(trend.get("degraded_driver_share") or 0.0),
                ),
                6,
            ),
            "session_microstructure_distortion": round(
                max(
                    float(category_multi.get("market_session_liquidity_issue", 0)) / artifact_count,
                    float(trend.get("session_liquidity_share") or 0.0),
                ),
                6,
            ),
            "true_model_weakness": round(
                float(category_multi.get("true_canary_weakness", 0)) / artifact_count,
                6,
            ),
        }
        dominant_profile = max(profile_scores, key=profile_scores.get) if profile_scores else "unknown"
        non_zero = [name for name, value in profile_scores.items() if value > 0.0]
        if len(non_zero) > 1:
            top_value = profile_scores[dominant_profile]
            competing = sorted(profile_scores.values(), reverse=True)
            if len(competing) > 1 and abs(top_value - competing[1]) < 0.15:
                dominant_profile = "mixed"

        return {
            "dominant_profile": dominant_profile,
            "profile_scores": profile_scores,
            "policy_gate_overlay_present": policy_overlay_count > 0,
            "policy_gate_overlay_ratio": round(min(1.0, policy_overlay_count / artifact_count), 6),
            "session_breakdown_present": bool(session_breakdown),
        }

    def _primary_blockers(
        self,
        *,
        stabilization: Dict[str, Any],
        observability: Dict[str, Any],
    ) -> List[str]:
        blockers: List[str] = []
        summary = stabilization.get("summary", {}) or {}
        trend = stabilization.get("trend_summary", {}) or {}
        obs_summary = observability.get("summary", {}) or {}

        if str(summary.get("dominant_failure_category", "")) == "insufficient_evidence_maturity":
            blockers.append("evidence_maturity")
        if bool(trend.get("regression_emerging")):
            blockers.append("emerging_regression_signal")
        if float(trend.get("degraded_driver_share") or 0.0) >= 0.5:
            blockers.append("degraded_scorecard_dominance")
        if str(obs_summary.get("dominant_failure_category", "")) == "policy_gated_failure":
            blockers.append("policy_gate_still_blocking")
        if not bool(obs_summary.get("config_fingerprint_consistent", False)):
            blockers.append("config_fingerprint_inconsistency")

        if not blockers:
            blockers.append("no_material_blocker_identified")
        return blockers

    def _next_questions(
        self,
        *,
        stabilization: Dict[str, Any],
        observability: Dict[str, Any],
        session_coverage: List[str],
    ) -> List[str]:
        questions: List[str] = []
        trend = stabilization.get("trend_summary", {}) or {}
        if float((trend.get("eval_days", {}) or {}).get("end") or 0.0) <= 1.0:
            questions.append("When will eval_days move above 1 so maturity gates stop dominating?")
        if float((trend.get("trade_count", {}) or {}).get("delta") or 0.0) > 0.0:
            questions.append("Does the rising trade_count continue without increasing rollback pressure?")
        if len(session_coverage) <= 1:
            questions.append("When will canary evidence include live overnight, pre-market, regular, or after-hours coverage instead of one session bucket?")
        if bool(trend.get("regression_emerging")):
            questions.append("Are emerging regression signals caused by degraded-runtime proxies or by true canary weakness?")
        if str((observability.get("summary", {}) or {}).get("dominant_failure_category", "")) == "policy_gated_failure":
            questions.append("Are policy failures still driven mainly by low eval_count and oversized weight deltas, or is a new policy blocker appearing?")
        return questions[:4]

    def _operator_actions(
        self,
        *,
        checkpoint_status: str,
        primary_blockers: List[str],
    ) -> List[str]:
        actions: List[str] = []
        if checkpoint_status.startswith("continue_stabilization"):
            actions.append("Keep canary evidence-only and maintain the current stabilization window.")
        if "evidence_maturity" in primary_blockers:
            actions.append("Wait for more eval_days and trade_count before interpreting promotion readiness.")
        if "degraded_scorecard_dominance" in primary_blockers:
            actions.append("Review degraded-scorecard contribution separately from true signal quality.")
        if "emerging_regression_signal" in primary_blockers:
            actions.append("Inspect the latest regression-emerging rows before changing any evaluation proxy.")
        if "policy_gate_still_blocking" in primary_blockers:
            actions.append("Treat policy-gate failures as expected evidence until maturity improves; do not loosen gates.")
        return actions[:4]
