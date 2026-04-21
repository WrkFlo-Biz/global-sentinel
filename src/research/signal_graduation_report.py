#!/usr/bin/env python3
"""Evaluate signals against promotion criteria and generate graduation report."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SignalGraduationReport:
    """Evaluates each signal against promotion registry criteria."""

    def __init__(self, registry_path: Optional[Path] = None):
        self._registry = {}
        if registry_path and registry_path.exists():
            try:
                import yaml
                self._registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
            except Exception:
                pass

    def evaluate_signal(
        self,
        signal_name: str,
        eval_metrics: Dict[str, Any],
        criteria: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if criteria is None:
            criteria = (
                self._registry.get("signals", {})
                .get(signal_name, {})
                .get("promotion_criteria", {})
            )

        results = []
        all_pass = True

        checks = [
            ("min_eval_days", "eval_days", ">="),
            ("min_trade_count", "trade_count", ">="),
            ("max_drawdown_delta_bps", "drawdown_delta_bps", "<="),
            ("min_slippage_adjusted_win_delta_bps", "slippage_adjusted_win_delta_bps", ">="),
            ("max_failure_rate", "failure_rate", "<="),
        ]

        for crit_key, metric_key, op in checks:
            threshold = criteria.get(crit_key)
            if threshold is None:
                continue
            value = eval_metrics.get(metric_key, 0)
            if op == ">=":
                passed = value >= threshold
            else:
                passed = value <= threshold
            if not passed:
                all_pass = False
            results.append({
                "criterion": crit_key,
                "threshold": threshold,
                "value": value,
                "operator": op,
                "passed": passed,
            })

        # Boolean checks
        for flag in ["reproducibility_required", "no_safety_regressions"]:
            if criteria.get(flag):
                val = eval_metrics.get(flag.replace("_required", ""), True)
                passed = bool(val)
                if not passed:
                    all_pass = False
                results.append({"criterion": flag, "threshold": True, "value": val, "passed": passed})

        recommendation = "promote" if all_pass else "hold"
        current_stage = (
            self._registry.get("signals", {})
            .get(signal_name, {})
            .get("current_stage", "unknown")
        )

        return {
            "schema_version": "signal_graduation_report.v1",
            "signal_name": signal_name,
            "current_stage": current_stage,
            "recommendation": recommendation,
            "criteria_results": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
