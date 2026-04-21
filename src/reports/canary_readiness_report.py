#!/usr/bin/env python3
"""Formal canary-readiness review report for Global Sentinel."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.blob_persistence_health import BlobPersistenceHealthChecker
from src.core.rollback_telemetry import RollbackTelemetryCollector
from src.replay.decision_replay_runner import DecisionReplayRunner
from src.reports.decision_audit_report import DecisionAuditReportBuilder


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
class CriterionResult:
    category: str
    criterion: str
    passed: bool
    value: Any
    threshold: Any
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "criterion": self.criterion,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


class CanaryReadinessReportBuilder:
    """Build a conservative GO/NO-GO report for evidence-only canary readiness."""

    def __init__(
        self,
        repo_root: Path,
        *,
        replay_grade_ratio_threshold: float = 0.95,
        max_blob_fallback_events: int = 1,
        max_alerts_per_scorecard: float = 0.25,
    ):
        self.repo_root = repo_root
        self.replay_grade_ratio_threshold = replay_grade_ratio_threshold
        self.max_blob_fallback_events = max_blob_fallback_events
        self.max_alerts_per_scorecard = max_alerts_per_scorecard

    def build(self, limit: int = 200) -> Dict[str, Any]:
        epoch = self._current_schema_epoch(limit=limit)
        replay_runner = DecisionReplayRunner(self.repo_root)
        replay_report = replay_runner.replay_range(
            start_utc=epoch.get("start_utc"),
            limit=limit,
        )
        config_consistency = replay_runner.verify_config_consistency(limit=limit)
        audit_report = DecisionAuditReportBuilder(self.repo_root).build_report(
            start_utc=epoch.get("start_utc"),
            limit=limit,
        )
        blob_health = BlobPersistenceHealthChecker(self.repo_root).check().to_dict()
        rollback = RollbackTelemetryCollector(self.repo_root).collect().to_dict()
        alert_summary = self._alert_summary(
            scorecard_count=int(replay_report.get("total_scorecards", 0)),
            start_utc=epoch.get("start_utc"),
        )

        criteria = [
            self._criterion_replay_runner_trusted(replay_report),
            self._criterion_scorecards_replay_grade(replay_report),
            self._criterion_blob_primary_stable(blob_health),
            self._criterion_fallback_visible_and_rare(blob_health, alert_summary),
            self._criterion_audit_reports_useful(audit_report, replay_report),
            self._criterion_rollback_path_proven(rollback),
            self._criterion_alert_quality(alert_summary),
            self._criterion_config_fingerprint_consistent(config_consistency),
        ]

        blockers = [
            {
                "category": result.category,
                "criterion": result.criterion,
                "reason": result.reason,
            }
            for result in criteria
            if not result.passed
        ]
        blocker_counts = Counter(item["category"] for item in blockers)
        readiness_status = "GO" if not blockers else "NO_GO"
        top_blocker = blockers[0] if blockers else None

        return {
            "schema_version": "canary_readiness_report.v1",
            "generated_at": _utc_now_iso(),
            "readiness_status": readiness_status,
            "top_blocker": top_blocker,
            "blockers": blockers,
            "blockers_by_category": dict(blocker_counts),
            "criteria_results": [item.to_dict() for item in criteria],
            "summary": {
                "replay_confidence": self._confidence_label(criteria[0].passed and criteria[1].passed),
                "persistence_confidence": self._confidence_label(criteria[2].passed and criteria[3].passed),
                "rollback_confidence": self._confidence_label(criteria[5].passed),
                "alert_quality_assessment": self._confidence_label(criteria[6].passed),
                "audit_confidence": self._confidence_label(criteria[4].passed),
            },
            "evidence_artifacts": {
                "scorecards_dir": str(self.repo_root / "logs" / "scorecards"),
                "events_dir": str(self.repo_root / "logs" / "events"),
                "operational_reports_dir": str(self.repo_root / "reports" / "operational"),
            },
            "supporting_evidence": {
                "current_schema_epoch": epoch,
                "replay_report": replay_report,
                "config_consistency": config_consistency,
                "audit_report": audit_report,
                "blob_health": blob_health,
                "rollback_telemetry": rollback,
                "alert_summary": alert_summary,
            },
        }

    def _criterion_replay_runner_trusted(self, replay_report: Dict[str, Any]) -> CriterionResult:
        total = int(replay_report.get("total_scorecards", 0))
        passed = total > 0 and "error" not in replay_report
        return CriterionResult(
            category="replay",
            criterion="replay_runner_trusted",
            passed=passed,
            value=total,
            threshold="> 0 scorecards and no replay errors",
            reason="replay runner has scorecards to evaluate" if passed else "no scorecards available for replay review",
            evidence={"total_scorecards": total},
        )

    def _criterion_scorecards_replay_grade(self, replay_report: Dict[str, Any]) -> CriterionResult:
        ratio = float(replay_report.get("replay_grade_ratio", 0.0))
        passed = ratio >= self.replay_grade_ratio_threshold
        return CriterionResult(
            category="replay",
            criterion="scorecards_replay_grade",
            passed=passed,
            value=ratio,
            threshold=self.replay_grade_ratio_threshold,
            reason="replay-grade ratio meets threshold" if passed else "replay-grade ratio below threshold",
            evidence={
                "replay_grade_count": replay_report.get("replay_grade_count", 0),
                "total_scorecards": replay_report.get("total_scorecards", 0),
            },
        )

    def _criterion_blob_primary_stable(self, blob_health: Dict[str, Any]) -> CriterionResult:
        passed = (
            blob_health.get("status") == "healthy"
            and blob_health.get("persistence_mode") == "blob_primary"
        )
        return CriterionResult(
            category="persistence",
            criterion="blob_primary_stable",
            passed=passed,
            value={
                "status": blob_health.get("status"),
                "mode": blob_health.get("persistence_mode"),
            },
            threshold={"status": "healthy", "mode": "blob_primary"},
            reason="Blob primary persistence is healthy" if passed else "Blob primary persistence is not healthy",
            evidence={
                "fallback_reason": blob_health.get("fallback_reason"),
                "blob_available": blob_health.get("blob_available"),
            },
        )

    def _criterion_fallback_visible_and_rare(
        self,
        blob_health: Dict[str, Any],
        alert_summary: Dict[str, Any],
    ) -> CriterionResult:
        fallback_count = int(alert_summary.get("blob_fallback_count", 0))
        visible = bool(alert_summary.get("blob_fallback_visible")) or blob_health.get("persistence_mode") != "blob_primary"
        passed = visible and fallback_count <= self.max_blob_fallback_events
        if blob_health.get("persistence_mode") == "blob_primary" and fallback_count == 0:
            # Healthy primary persistence with no fallback events is acceptable and visible.
            passed = True
            visible = True
        return CriterionResult(
            category="persistence",
            criterion="fallback_visible_and_rare",
            passed=passed,
            value={"fallback_count": fallback_count, "visible": visible},
            threshold={"max_blob_fallback_events": self.max_blob_fallback_events},
            reason="fallback events are visible and rare" if passed else "fallback visibility/rate is not acceptable",
            evidence={"recent_blob_fallback_events": alert_summary.get("recent_blob_fallback_events", [])},
        )

    def _criterion_audit_reports_useful(
        self,
        audit_report: Dict[str, Any],
        replay_report: Dict[str, Any],
    ) -> CriterionResult:
        scorecards = int(audit_report.get("period", {}).get("scorecards_analyzed", 0))
        has_sections = all(
            key in audit_report
            for key in (
                "blocked_escalations",
                "freshness_degradations",
                "config_drift_events",
                "quorum_blocks",
            )
        )
        passed = has_sections and scorecards == int(replay_report.get("total_scorecards", 0))
        return CriterionResult(
            category="audit",
            criterion="audit_reports_useful_and_complete",
            passed=passed,
            value={"scorecards_analyzed": scorecards, "has_sections": has_sections},
            threshold="all audit sections present and aligned with replay scope",
            reason="audit report contains complete decision sections" if passed else "audit report is incomplete or misaligned",
            evidence={"summary": audit_report.get("summary", {})},
        )

    def _criterion_rollback_path_proven(self, rollback: Dict[str, Any]) -> CriterionResult:
        passed = bool(rollback.get("rollback_path_proven"))
        return CriterionResult(
            category="rollback",
            criterion="rollback_path_proven",
            passed=passed,
            value={
                "rollback_path_present": rollback.get("rollback_path_present"),
                "rollback_path_proven": rollback.get("rollback_path_proven"),
            },
            threshold={"rollback_path_present": True, "rollback_path_proven": True},
            reason="rollback path is evidenced by recorded rollback events" if passed else "rollback path has not been proven by evidence",
            evidence={
                "recent_encoder_rollbacks": rollback.get("recent_encoder_rollbacks", [])[:3],
                "recent_learning_state_rollbacks": rollback.get("recent_learning_state_rollbacks", [])[:3],
            },
        )

    def _criterion_alert_quality(self, alert_summary: Dict[str, Any]) -> CriterionResult:
        max_repeated_alert_type = int(alert_summary.get("max_repeated_alert_type", 0))
        reason_rich = bool(alert_summary.get("reason_rich"))
        passed = reason_rich and max_repeated_alert_type <= 1
        return CriterionResult(
            category="alerting",
            criterion="alerting_low_noise_reason_rich",
            passed=passed,
            value={
                "max_repeated_alert_type": max_repeated_alert_type,
                "reason_rich": reason_rich,
            },
            threshold={
                "max_repeated_alert_type": 1,
                "reason_rich": True,
            },
            reason="alerts are low-noise and reason-rich" if passed else "alerts are noisy or missing actionable reasons",
            evidence={"recent_alerts": alert_summary.get("recent_alerts", [])[:5]},
        )

    def _criterion_config_fingerprint_consistent(self, config_consistency: Dict[str, Any]) -> CriterionResult:
        passed = bool(config_consistency.get("config_stable"))
        return CriterionResult(
            category="config",
            criterion="config_fingerprint_consistent",
            passed=passed,
            value={
                "config_stable": config_consistency.get("config_stable"),
                "unique_fingerprints": config_consistency.get("unique_fingerprints"),
            },
            threshold={"config_stable": True},
            reason="config fingerprint is stable across replay scope" if passed else "config fingerprint drift detected",
            evidence={"drift_events": config_consistency.get("drift_events", [])[:5]},
        )

    def _alert_summary(self, scorecard_count: int, start_utc: Optional[str] = None) -> Dict[str, Any]:
        events_dir = self.repo_root / "logs" / "events"
        recent_alerts: List[Dict[str, Any]] = []
        blob_fallback_events: List[Dict[str, Any]] = []
        threshold_dt = _parse_iso(start_utc)
        if events_dir.exists():
            for path in sorted(events_dir.glob("alert_*.json"), reverse=True)[:50]:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                payload_dt = _parse_iso(payload.get("timestamp"))
                if threshold_dt is not None and payload_dt is not None and payload_dt < threshold_dt:
                    continue
                recent_alerts.append(payload)
                if (
                    payload.get("alert_type") == "blob_fallback"
                    and (payload.get("details", {}) or {}).get("reason") != "test"
                ):
                    blob_fallback_events.append(payload)

        reason_rich = all(
            bool(item.get("message")) and bool(item.get("details") is not None)
            for item in recent_alerts[:10]
        ) if recent_alerts else True

        alert_type_counts = Counter(str(item.get("alert_type", "unknown")) for item in recent_alerts)
        return {
            "recent_alerts": recent_alerts,
            "blob_fallback_count": len(blob_fallback_events),
            "recent_blob_fallback_events": blob_fallback_events,
            "blob_fallback_visible": len(blob_fallback_events) > 0,
            "alerts_per_scorecard": round(len(recent_alerts) / scorecard_count, 4) if scorecard_count > 0 else float(len(recent_alerts)),
            "max_repeated_alert_type": max(alert_type_counts.values(), default=0),
            "alert_type_counts": dict(alert_type_counts),
            "reason_rich": reason_rich,
        }

    @staticmethod
    def _confidence_label(passed: bool) -> str:
        return "high" if passed else "low"

    def _current_schema_epoch(self, limit: int = 200) -> Dict[str, Any]:
        scorecards_dir = self.repo_root / "logs" / "scorecards"
        if not scorecards_dir.exists():
            return {"schema_version": "", "start_utc": None, "scorecard_count": 0}

        records: List[Dict[str, Any]] = []
        for path in sorted(scorecards_dir.glob("scorecard_*.json"))[-limit:]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            records.append({
                "schema_version": str(payload.get("schema_version", "")),
                "timestamp_utc": payload.get("timestamp_utc"),
            })

        if not records:
            return {"schema_version": "", "start_utc": None, "scorecard_count": 0}

        latest_schema = records[-1]["schema_version"]
        matching = [item for item in records if item["schema_version"] == latest_schema]
        matching_ts = [
            ts.isoformat()
            for ts in (_parse_iso(item.get("timestamp_utc")) for item in matching)
            if ts is not None
        ]
        return {
            "schema_version": latest_schema,
            "start_utc": min(matching_ts) if matching_ts else None,
            "scorecard_count": len(matching),
        }
