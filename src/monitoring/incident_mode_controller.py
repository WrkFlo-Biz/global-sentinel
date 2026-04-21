#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Incident Mode Controller

Purpose:
- Detect incident conditions from execution/monitoring report inputs
- Produce advisory-only incident assessment (shadow/advisory, does NOT write control files)
- Inputs: lag SLA report, stale sweeper report, exec reliability report, router summary (all optional)
- Output: incident_assessment.json with incident_detected, triggers, recommended_actions, runtime_flags

Trigger thresholds (defaults):
- lag_sla_critical: any critical lag SLA breach
- stale_rate > 0.30
- rejected_rate > 0.15
- router_error_rate > 0.10
- manual_review_rate > 0.25
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


class IncidentModeController:
    def __init__(
        self,
        stale_rate_threshold: float = 0.30,
        rejected_rate_threshold: float = 0.15,
        router_error_rate_threshold: float = 0.10,
        manual_review_rate_threshold: float = 0.25,
        repo_root: Optional[Path] = None,
    ):
        # Load from config/incident_mode_policy.yaml if available
        if repo_root:
            self._load_policy(repo_root, locals())
        else:
            self.stale_rate_threshold = stale_rate_threshold
            self.rejected_rate_threshold = rejected_rate_threshold
            self.router_error_rate_threshold = router_error_rate_threshold
            self.manual_review_rate_threshold = manual_review_rate_threshold

    def _load_policy(self, repo_root: Path, defaults: dict) -> None:
        """Load thresholds from incident_mode_policy.yaml, falling back to defaults."""
        try:
            import yaml
            policy_path = repo_root / "config" / "incident_mode_policy.yaml"
            if policy_path.exists():
                policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
                thresholds = policy.get("incident_thresholds", {})
                self.stale_rate_threshold = float(thresholds.get("stale_rate", defaults.get("stale_rate_threshold", 0.30)))
                self.rejected_rate_threshold = float(thresholds.get("rejected_rate", defaults.get("rejected_rate_threshold", 0.15)))
                self.router_error_rate_threshold = float(thresholds.get("router_error_rate", defaults.get("router_error_rate_threshold", 0.10)))
                self.manual_review_rate_threshold = float(thresholds.get("manual_review_rate", defaults.get("manual_review_rate_threshold", 0.25)))
                return
        except Exception:
            pass
        self.stale_rate_threshold = defaults.get("stale_rate_threshold", 0.30)
        self.rejected_rate_threshold = defaults.get("rejected_rate_threshold", 0.15)
        self.router_error_rate_threshold = defaults.get("router_error_rate_threshold", 0.10)
        self.manual_review_rate_threshold = defaults.get("manual_review_rate_threshold", 0.25)

    def assess(
        self,
        lag_sla_report: Optional[Dict[str, Any]] = None,
        stale_sweeper_report: Optional[Dict[str, Any]] = None,
        exec_reliability_report: Optional[Dict[str, Any]] = None,
        router_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        triggers: List[Dict[str, Any]] = []
        recommended_actions: List[str] = []
        runtime_flags: Dict[str, Any] = {}

        # Check lag SLA critical
        if lag_sla_report:
            sla_summary = lag_sla_report.get("summary") or lag_sla_report.get("sla_summary") or {}
            critical_count = int(sla_summary.get("critical_count") or sla_summary.get("critical_breach_count") or 0)
            overall_status = str(sla_summary.get("overall_sla_status") or sla_summary.get("status") or "").lower()
            if critical_count > 0 or overall_status == "critical":
                triggers.append({
                    "source": "lag_sla_report",
                    "condition": "lag_sla_critical",
                    "detail": f"critical_count={critical_count}, overall_status={overall_status}",
                })
                recommended_actions.append("Investigate reconciler lag — critical SLA breaches detected")
                runtime_flags["reconciler_lag_critical"] = True

        # Check stale rate
        if stale_sweeper_report:
            sw_summary = stale_sweeper_report.get("summary") or {}
            stale_rate = safe_float(sw_summary.get("stale_rate"), 0.0)
            if stale_rate > self.stale_rate_threshold:
                triggers.append({
                    "source": "stale_sweeper_report",
                    "condition": "stale_rate_exceeded",
                    "detail": f"stale_rate={stale_rate:.4f} > threshold={self.stale_rate_threshold}",
                    "value": stale_rate,
                    "threshold": self.stale_rate_threshold,
                })
                recommended_actions.append("Review stale open intents — stale rate exceeds threshold")
                runtime_flags["stale_rate_breach"] = True

        # Check exec reliability metrics
        if exec_reliability_report:
            kpis = exec_reliability_report.get("kpis") or {}

            rejected_rate = safe_float(kpis.get("rejected_rate"), 0.0)
            if rejected_rate > self.rejected_rate_threshold:
                triggers.append({
                    "source": "exec_reliability_report",
                    "condition": "rejected_rate_exceeded",
                    "detail": f"rejected_rate={rejected_rate:.4f} > threshold={self.rejected_rate_threshold}",
                    "value": rejected_rate,
                    "threshold": self.rejected_rate_threshold,
                })
                recommended_actions.append("Investigate broker rejection rate — may indicate connectivity or sizing issues")
                runtime_flags["rejected_rate_breach"] = True

            manual_review_rate = safe_float(kpis.get("manual_review_rate"), 0.0)
            if manual_review_rate > self.manual_review_rate_threshold:
                triggers.append({
                    "source": "exec_reliability_report",
                    "condition": "manual_review_rate_exceeded",
                    "detail": f"manual_review_rate={manual_review_rate:.4f} > threshold={self.manual_review_rate_threshold}",
                    "value": manual_review_rate,
                    "threshold": self.manual_review_rate_threshold,
                })
                recommended_actions.append("Triage manual review queue — high fraction of intents need operator attention")
                runtime_flags["manual_review_rate_breach"] = True

            # Router error rate
            router_run_count = int(kpis.get("router_run_count") or 0)
            router_error_count = int(kpis.get("router_error_count") or 0)
            if router_run_count > 0:
                router_error_rate = router_error_count / max(router_run_count, 1)
                if router_error_rate > self.router_error_rate_threshold:
                    triggers.append({
                        "source": "exec_reliability_report",
                        "condition": "router_error_rate_exceeded",
                        "detail": f"router_error_rate={router_error_rate:.4f} > threshold={self.router_error_rate_threshold}",
                        "value": router_error_rate,
                        "threshold": self.router_error_rate_threshold,
                    })
                    recommended_actions.append("Check router health — elevated error rate in shadow order routing")
                    runtime_flags["router_error_rate_breach"] = True

        # Check router summary (single-run level)
        if router_summary:
            submit_attempts = int(router_summary.get("submit_attempt_count") or 0)
            broker_rejected = int(router_summary.get("broker_rejected_count") or 0)
            if submit_attempts > 0:
                run_reject_rate = broker_rejected / submit_attempts
                if run_reject_rate > self.rejected_rate_threshold:
                    triggers.append({
                        "source": "router_summary",
                        "condition": "single_run_rejected_rate_exceeded",
                        "detail": f"run_reject_rate={run_reject_rate:.4f} > threshold={self.rejected_rate_threshold}",
                        "value": run_reject_rate,
                        "threshold": self.rejected_rate_threshold,
                    })
                    recommended_actions.append("Recent router run had elevated broker rejection rate")

        incident_detected = len(triggers) > 0

        if incident_detected:
            runtime_flags["suggested_mode"] = "ELEVATED"
            runtime_flags["incident_detected"] = True
        else:
            runtime_flags["suggested_mode"] = "NORMAL"
            runtime_flags["incident_detected"] = False

        return {
            "schema_version": "incident_assessment.v1",
            "timestamp_utc": iso_now(),
            "incident_detected": incident_detected,
            "incident_trigger_count": len(triggers),
            "incident_triggers": triggers,
            "recommended_actions": recommended_actions,
            "runtime_flags": runtime_flags,
            "thresholds_used": {
                "stale_rate_threshold": self.stale_rate_threshold,
                "rejected_rate_threshold": self.rejected_rate_threshold,
                "router_error_rate_threshold": self.router_error_rate_threshold,
                "manual_review_rate_threshold": self.manual_review_rate_threshold,
            },
            "advisory_only": True,
        }


def render_markdown(assessment: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Incident Mode Assessment")
    lines.append("")
    lines.append(f"- Generated: {assessment.get('timestamp_utc')}")
    lines.append(f"- Incident detected: **{assessment.get('incident_detected')}**")
    lines.append(f"- Trigger count: {assessment.get('incident_trigger_count')}")
    lines.append(f"- Advisory only: {assessment.get('advisory_only')}")
    lines.append("")

    if assessment.get("incident_triggers"):
        lines.append("## Triggers")
        lines.append("")
        for t in assessment["incident_triggers"]:
            lines.append(f"- **{t.get('condition')}** ({t.get('source')}): {t.get('detail')}")
        lines.append("")

    if assessment.get("recommended_actions"):
        lines.append("## Recommended Actions")
        lines.append("")
        for a in assessment["recommended_actions"]:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("## Runtime Flags")
    for k, v in (assessment.get("runtime_flags") or {}).items():
        lines.append(f"- {k}: {v}")

    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description="Incident Mode Controller (advisory only)")
    p.add_argument("--lag-sla-json", default=None, help="Path to reconciler lag SLA monitor JSON")
    p.add_argument("--stale-sweeper-json", default=None, help="Path to stale intent sweeper report JSON")
    p.add_argument("--exec-reliability-json", default=None, help="Path to execution reliability metrics JSON")
    p.add_argument("--router-summary-json", default=None, help="Path to single router run summary JSON")
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def _load_json_optional(path_str: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    args = parse_args()

    controller = IncidentModeController()
    assessment = controller.assess(
        lag_sla_report=_load_json_optional(args.lag_sla_json),
        stale_sweeper_report=_load_json_optional(args.stale_sweeper_json),
        exec_reliability_report=_load_json_optional(args.exec_reliability_json),
        router_summary=_load_json_optional(args.router_summary_json),
    )

    if args.output_json:
        p = Path(args.output_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(assessment, indent=2), encoding="utf-8")
    else:
        print(json.dumps(assessment, indent=2))

    if args.output_md:
        p = Path(args.output_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(assessment), encoding="utf-8")


if __name__ == "__main__":
    main()
