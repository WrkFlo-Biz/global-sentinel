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
    def build(
        self,
        graduation_reports: List[Dict[str, Any]],
        drift_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        signals_ready = []
        signals_blocked = []
        blockers = []

        for report in graduation_reports:
            name = report.get("signal_name", "unknown")
            rec = report.get("recommendation", "hold")
            if rec == "promote":
                signals_ready.append(name)
            else:
                signals_blocked.append(name)
                failed = [
                    r["criterion"]
                    for r in report.get("criteria_results", [])
                    if not r.get("passed")
                ]
                blockers.append({"signal": name, "blockers": failed})

        return {
            "schema_version": "promotion_readiness_report.v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signals_ready_count": len(signals_ready),
            "signals_blocked_count": len(signals_blocked),
            "signals_ready": signals_ready,
            "signals_blocked": signals_blocked,
            "blockers": blockers,
            "drift_summary": {
                "max_abs_drift": (drift_report or {}).get("max_abs_drift", 0),
            },
        }
