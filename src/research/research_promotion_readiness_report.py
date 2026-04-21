#!/usr/bin/env python3
"""Weekly report summarizing all signals' promotion readiness."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ResearchPromotionReadinessReport:
    MAX_DECAY_ADJUSTED_DRIFT = 0.15
    MAX_DECAYING_EDGE_RATIO = 0.4

    def build(
        self,
        graduation_reports: List[Dict[str, Any]],
        drift_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        signals_ready = []
        signals_blocked = []
        blockers = []
        drift_report = drift_report or {}
        edge_decay_summary = drift_report.get("edge_decay_summary") or {}
        decay_adjusted_drift = float(drift_report.get("decay_adjusted_drift", drift_report.get("max_abs_drift", 0.0)) or 0.0)
        decaying_edge_ratio = float(edge_decay_summary.get("current_decaying_edge_ratio", 0.0) or 0.0)
        global_decay_blockers = []
        if decay_adjusted_drift > self.MAX_DECAY_ADJUSTED_DRIFT:
            global_decay_blockers.append("decay_adjusted_drift")
        if decaying_edge_ratio > self.MAX_DECAYING_EDGE_RATIO:
            global_decay_blockers.append("decaying_edge_ratio")

        for report in graduation_reports:
            name = report.get("signal_name", "unknown")
            rec = report.get("recommendation", "hold")
            guardrail_result = report.get("guardrail_result") or {}
            walk_forward = report.get("walk_forward_validation") or {}
            explicit_blockers = []
            if guardrail_result and not guardrail_result.get("passed", False):
                explicit_blockers.append("guardrail_check")
            if walk_forward and not walk_forward.get("passed", False):
                explicit_blockers.append("walk_forward_validation")
            explicit_blockers.extend(global_decay_blockers)

            if explicit_blockers:
                rec = "hold"
            if rec == "promote":
                signals_ready.append(name)
            else:
                signals_blocked.append(name)
                failed = [
                    r["criterion"]
                    for r in report.get("criteria_results", [])
                    if not r.get("passed")
                ]
                failed.extend(explicit_blockers)
                blockers.append({"signal": name, "blockers": failed})

        return {
            "schema_version": "promotion_readiness_report.v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signals_ready_count": len(signals_ready),
            "signals_blocked_count": len(signals_blocked),
            "signals_ready": signals_ready,
            "signals_blocked": signals_blocked,
            "blockers": blockers,
            "guardrail_blocked_count": sum(
                1 for row in blockers if "guardrail_check" in (row.get("blockers") or [])
            ),
            "walk_forward_blocked_count": sum(
                1 for row in blockers if "walk_forward_validation" in (row.get("blockers") or [])
            ),
            "drift_summary": {
                "max_abs_drift": (drift_report or {}).get("max_abs_drift", 0),
                "decay_adjusted_drift": drift_report.get("decay_adjusted_drift", drift_report.get("max_abs_drift", 0)),
                "current_decaying_edge_ratio": edge_decay_summary.get("current_decaying_edge_ratio", 0),
                "edge_decay_pressure": edge_decay_summary.get("edge_decay_pressure", 0),
            },
        }
