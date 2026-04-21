#!/usr/bin/env python3
"""
Experiment Tracker — logs quantum vs classical comparison results and
computes rolling statistics with paired significance tests.

All outputs carry not_for_direct_execution=true.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Graceful scipy fallback
try:
    from scipy.stats import ttest_rel, wilcoxon  # type: ignore[import-untyped]
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentTracker:
    """Track quantum experiment comparison results and compute statistics."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root)
        self.protocol_path = self.repo_root / "config" / "quantum_experiment_protocol.yaml"
        self.log_path = self.repo_root / "reports" / "research" / "experiment_log.jsonl"
        self.protocol = self._load_protocol()

    # ------------------------------------------------------------------
    # Protocol loading
    # ------------------------------------------------------------------

    def _load_protocol(self) -> Dict[str, Any]:
        if not self.protocol_path.exists():
            return {}
        if yaml is None:
            # Fallback: attempt naive parse — but really yaml should be available
            return {}
        try:
            text = self.protocol_path.read_text(encoding="utf-8")
            return yaml.safe_load(text) or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_result(self, comparison_report: dict) -> None:
        """Append a comparison report to the experiment log (JSONL)."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp_utc": _iso_now(),
            "report": comparison_report,
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
        }

        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    # ------------------------------------------------------------------
    # Reading the log
    # ------------------------------------------------------------------

    def _read_entries(self) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @staticmethod
    def _extract_deltas(entries: List[Dict[str, Any]]) -> List[float]:
        """Extract objective_value_delta from each entry's comparison."""
        deltas: List[float] = []
        for entry in entries:
            report = entry.get("report", {})
            comparison = report.get("comparison", {})
            delta = comparison.get("objective_value_delta")
            if delta is not None:
                try:
                    deltas.append(float(delta))
                except (TypeError, ValueError):
                    continue
        return deltas

    @staticmethod
    def _extract_paired_values(
        entries: List[Dict[str, Any]],
    ) -> tuple[List[float], List[float]]:
        """Extract paired (quantum, classical) objective values for significance tests."""
        quantum_vals: List[float] = []
        classical_vals: List[float] = []
        for entry in entries:
            report = entry.get("report", {})
            comparison = report.get("comparison", {})
            q = comparison.get("quantum_objective")
            c = comparison.get("classical_objective")
            if q is not None and c is not None:
                try:
                    quantum_vals.append(float(q))
                    classical_vals.append(float(c))
                except (TypeError, ValueError):
                    continue
        return quantum_vals, classical_vals

    # ------------------------------------------------------------------
    # Rolling statistics
    # ------------------------------------------------------------------

    def rolling_statistics(self, window: int = 30) -> dict:
        """Compute rolling mean/median objective deltas and p-values."""
        entries = self._read_entries()
        windowed = entries[-window:] if len(entries) > window else entries

        deltas = self._extract_deltas(windowed)
        quantum_vals, classical_vals = self._extract_paired_values(windowed)

        result: Dict[str, Any] = {
            "window": window,
            "observations": len(windowed),
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
        }

        if deltas:
            result["mean_objective_delta"] = statistics.mean(deltas)
            result["median_objective_delta"] = statistics.median(deltas)
            result["stdev_objective_delta"] = (
                statistics.stdev(deltas) if len(deltas) >= 2 else None
            )
        else:
            result["mean_objective_delta"] = None
            result["median_objective_delta"] = None
            result["stdev_objective_delta"] = None

        # Paired significance tests
        result["paired_t_test_p"] = None
        result["wilcoxon_p"] = None

        if _HAS_SCIPY and len(quantum_vals) >= 2 and len(classical_vals) >= 2:
            try:
                t_stat, t_p = ttest_rel(quantum_vals, classical_vals)
                result["paired_t_test_p"] = float(t_p) if not math.isnan(t_p) else None
            except Exception:
                pass
            try:
                w_stat, w_p = wilcoxon(
                    [q - c for q, c in zip(quantum_vals, classical_vals)]
                )
                result["wilcoxon_p"] = float(w_p) if not math.isnan(w_p) else None
            except Exception:
                pass
        elif not _HAS_SCIPY:
            result["scipy_unavailable"] = True

        return result

    # ------------------------------------------------------------------
    # Current status
    # ------------------------------------------------------------------

    def current_status(self) -> dict:
        """Return experiment progress: days elapsed, cycles, recommendation."""
        experiment_cfg = self.protocol.get("experiment", {})
        start_date_str = experiment_cfg.get("start_date", "")
        minimum_days = experiment_cfg.get("minimum_days", 60)
        minimum_cycles = experiment_cfg.get("minimum_cycles", 200)

        entries = self._read_entries()
        cycles_logged = len(entries)

        # Days elapsed
        days_elapsed: Optional[int] = None
        days_remaining: Optional[int] = None
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            now = datetime.now(timezone.utc)
            days_elapsed = max(0, (now - start_date).days)
            days_remaining = max(0, minimum_days - days_elapsed)
        except (ValueError, TypeError):
            pass

        # Gate checks
        stage_2 = experiment_cfg.get("stage_2_gate", {})
        min_days_gate = stage_2.get("min_days", minimum_days)
        min_obs_gate = stage_2.get("min_paired_observations", minimum_cycles)
        min_improvement = stage_2.get("min_mean_improvement_pct", 1.0)
        p_threshold = stage_2.get("p_value_threshold", 0.05)

        rolling = self.rolling_statistics(window=cycles_logged or 30)

        days_met = days_elapsed is not None and days_elapsed >= min_days_gate
        obs_met = cycles_logged >= min_obs_gate
        mean_delta = rolling.get("mean_objective_delta")
        improvement_met = (
            mean_delta is not None and mean_delta >= min_improvement
        )
        t_p = rolling.get("paired_t_test_p")
        w_p = rolling.get("wilcoxon_p")

        sig_cfg = experiment_cfg.get("significance", {})
        both_required = sig_cfg.get("both_required", True)

        if both_required:
            significance_met = (
                t_p is not None
                and w_p is not None
                and t_p <= p_threshold
                and w_p <= p_threshold
            )
        else:
            significance_met = (
                (t_p is not None and t_p <= p_threshold)
                or (w_p is not None and w_p <= p_threshold)
            )

        gate_passed = days_met and obs_met and improvement_met and significance_met

        if gate_passed:
            recommendation = "STAGE_2_GATE_PASSED — human review required for promotion"
        elif days_elapsed is not None and days_elapsed < min_days_gate:
            recommendation = "COLLECTING — experiment still in progress"
        elif cycles_logged < min_obs_gate:
            recommendation = "COLLECTING — insufficient observations"
        else:
            recommendation = "STAGE_2_GATE_NOT_MET — continue collecting or investigate"

        return {
            "experiment_name": experiment_cfg.get("name", "unknown"),
            "start_date": start_date_str,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "minimum_days": minimum_days,
            "cycles_logged": cycles_logged,
            "minimum_cycles": minimum_cycles,
            "gate_checks": {
                "days_met": days_met,
                "observations_met": obs_met,
                "improvement_met": improvement_met,
                "significance_met": significance_met,
                "gate_passed": gate_passed,
            },
            "rolling_statistics": rolling,
            "recommendation": recommendation,
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
        }
