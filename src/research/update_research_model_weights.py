"""Bounded weight updates from labeled training rows.

Simple online update rule: positively labeled rows with high feature
values nudge that feature weight up; negatively labeled rows nudge down.
All updates are bounded by max_abs_weight_step guardrail.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.research.qfinance_online_learning_state import load_state, save_state
from src.research.research_guardrail_checker import ResearchGuardrailChecker


FEATURE_KEYS = [
    "base_score",
    "event_score",
    "quality_score",
    "anomaly_score",
    "liquidity_score",
    "regime_alignment",
    "volatility_penalty",
    "fill_quality_score",
    "edge_retention_score",
    "time_to_edge_score",
]


LABEL_TO_TARGET = {
    "strong_positive": 1.0,
    "positive": 0.5,
    "neutral": 0.0,
    "negative": -0.5,
    "strong_negative": -1.0,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


class ResearchModelWeightUpdater:
    @staticmethod
    def _clip(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _drift_thresholds(self, guardrails: Dict[str, Any]) -> Dict[str, float]:
        drift_cfg = guardrails.get("drift_guardrails") or {}
        trigger = self._clip(
            safe_float(drift_cfg.get("concept_drift_trigger_score"), 0.58),
            0.05,
            0.95,
        )
        critical = self._clip(
            safe_float(drift_cfg.get("concept_drift_critical_score"), 0.75),
            trigger,
            0.99,
        )
        return {
            "concept_drift_trigger_score": trigger,
            "concept_drift_critical_score": critical,
            "max_decaying_edge_ratio": self._clip(
                safe_float(drift_cfg.get("max_decaying_edge_ratio"), 0.45),
                0.05,
                0.99,
            ),
            "max_average_edge_decay_score": self._clip(
                safe_float(drift_cfg.get("max_average_edge_decay_score"), 0.55),
                0.05,
                0.99,
            ),
            "min_average_fill_quality_score": self._clip(
                safe_float(drift_cfg.get("min_average_fill_quality_score"), 0.50),
                0.01,
                0.99,
            ),
            "min_average_time_to_edge_score": self._clip(
                safe_float(drift_cfg.get("min_average_time_to_edge_score"), 0.45),
                0.01,
                0.99,
            ),
            "max_step_scale_under_drift": self._clip(
                safe_float(drift_cfg.get("max_step_scale_under_drift"), 0.5),
                0.1,
                1.0,
            ),
            "max_drift_down_weight_step": self._clip(
                safe_float(drift_cfg.get("max_drift_down_weight_step"), 0.03),
                0.0,
                0.10,
            ),
        }

    def _build_drift_monitor(
        self,
        *,
        avg_edge_decay_score: float,
        decaying_edge_ratio: float,
        avg_fill_quality_score: float,
        avg_time_to_edge_score: float,
        thresholds: Dict[str, float],
    ) -> Dict[str, Any]:
        signal_rows = [
            {
                "name": "avg_edge_decay_score",
                "value": round(avg_edge_decay_score, 4),
                "threshold": thresholds["max_average_edge_decay_score"],
                "comparison": "<=",
                "breached": avg_edge_decay_score >= thresholds["max_average_edge_decay_score"],
            },
            {
                "name": "decaying_edge_ratio",
                "value": round(decaying_edge_ratio, 4),
                "threshold": thresholds["max_decaying_edge_ratio"],
                "comparison": "<=",
                "breached": decaying_edge_ratio >= thresholds["max_decaying_edge_ratio"],
            },
            {
                "name": "avg_fill_quality_score",
                "value": round(avg_fill_quality_score, 4),
                "threshold": thresholds["min_average_fill_quality_score"],
                "comparison": ">=",
                "breached": avg_fill_quality_score <= thresholds["min_average_fill_quality_score"],
            },
            {
                "name": "avg_time_to_edge_score",
                "value": round(avg_time_to_edge_score, 4),
                "threshold": thresholds["min_average_time_to_edge_score"],
                "comparison": ">=",
                "breached": avg_time_to_edge_score <= thresholds["min_average_time_to_edge_score"],
            },
        ]

        breached = [row["name"] for row in signal_rows if row["breached"]]
        drift_score = (
            (avg_edge_decay_score * 0.40)
            + (decaying_edge_ratio * 0.30)
            + ((1.0 - self._clip(avg_fill_quality_score, 0.0, 1.0)) * 0.20)
            + ((1.0 - self._clip(avg_time_to_edge_score, 0.0, 1.0)) * 0.10)
        )
        drift_score = self._clip(drift_score, 0.0, 1.0)
        triggered = (
            drift_score >= thresholds["concept_drift_trigger_score"]
            or len(breached) >= 2
        )
        critical = drift_score >= thresholds["concept_drift_critical_score"]
        if critical:
            severity = "critical"
        elif triggered:
            severity = "elevated"
        elif breached:
            severity = "watch"
        else:
            severity = "normal"

        if triggered:
            down_weighting_multiplier = self._clip(
                1.0 - min(0.65, drift_score * 0.8),
                0.35,
                1.0,
            )
            step_scale = min(
                thresholds["max_step_scale_under_drift"],
                down_weighting_multiplier,
            )
        else:
            down_weighting_multiplier = 1.0
            step_scale = 1.0

        return {
            "schema_version": "concept_drift_monitor.v1",
            "triggered": triggered,
            "critical": critical,
            "severity": severity,
            "concept_drift_score": round(drift_score, 4),
            "thresholds": thresholds,
            "signals": signal_rows,
            "breached_signals": breached,
            "down_weighting_multiplier": round(down_weighting_multiplier, 4),
            "step_scale_under_drift": round(step_scale, 4),
        }

    def _validate_labeled_dataset(self, labeled_dataset: Dict[str, Any]) -> Dict[str, Any]:
        checker = ResearchGuardrailChecker()
        guardrail_result = checker.check_training_dataset(labeled_dataset).to_dict()
        walk_forward = labeled_dataset.get("walk_forward_validation") or {}
        if not isinstance(walk_forward, dict):
            walk_forward = {}

        folds_run = _safe_int(walk_forward.get("folds_run", walk_forward.get("fold_count", 0)), 0)
        validation = {
            "passed": bool(guardrail_result.get("passed")) and bool(walk_forward.get("passed", False)) and folds_run >= 2,
            "guardrail_result": guardrail_result,
            "walk_forward_validation": {
                **walk_forward,
                "folds_run": folds_run,
                "passed": bool(walk_forward.get("passed", False)),
            },
        }
        return validation

    def update(
        self,
        *,
        state: Dict[str, Any],
        labeled_dataset: Dict[str, Any],
        learning_rate: float = 0.01,
    ) -> Dict[str, Any]:
        validation = self._validate_labeled_dataset(labeled_dataset)
        if not validation["passed"]:
            raise ValueError("labeled_dataset_failed_guardrails_or_walk_forward_validation")

        weights = dict((state.get("weights") or {}))
        guardrails = state.get("guardrails") or {}
        max_step = float(guardrails.get("max_abs_weight_step", 0.05))
        min_w = float(guardrails.get("min_weight", -1.0))
        max_w = float(guardrails.get("max_weight", 1.0))

        rows = labeled_dataset.get("rows") or []
        n = 0

        gradients = {k: 0.0 for k in FEATURE_KEYS}
        total_row_weight = 0.0
        decaying_edge_rows = 0
        fill_quality_values = []
        edge_decay_values = []
        time_to_edge_values = []
        for row in rows:
            label = str(row.get("alpha_label", "neutral"))
            target = LABEL_TO_TARGET.get(label, 0.0)
            fill_quality = safe_float(row.get("fill_quality_score"), 0.7)
            edge_decay_score = safe_float(row.get("edge_decay_score"), 0.5)
            edge_decay_weight = safe_float(
                row.get("edge_decay_weight"),
                max(0.2, min(1.0, 1.0 - (edge_decay_score * 0.7))),
            )
            time_to_edge_score = safe_float(row.get("time_to_edge_score"), 0.5)
            sample_weight = safe_float(row.get("sample_weight"), 1.0)
            row_weight = max(
                0.05,
                min(
                    2.0,
                    sample_weight
                    * edge_decay_weight
                    * (0.55 + (fill_quality * 0.25) + (time_to_edge_score * 0.20)),
                ),
            )
            if edge_decay_score >= 0.55:
                decaying_edge_rows += 1
            fill_quality_values.append(fill_quality)
            edge_decay_values.append(edge_decay_score)
            time_to_edge_values.append(time_to_edge_score)

            for k in FEATURE_KEYS:
                gradients[k] += target * safe_float(row.get(k), 0.0) * row_weight
            n += 1
            total_row_weight += row_weight

        normalization = total_row_weight if total_row_weight > 0 else float(n)
        if normalization > 0:
            for k in FEATURE_KEYS:
                gradients[k] /= normalization

        avg_fill_quality_score = (
            (sum(fill_quality_values) / len(fill_quality_values))
            if fill_quality_values
            else 0.7
        )
        avg_edge_decay_score = (
            (sum(edge_decay_values) / len(edge_decay_values))
            if edge_decay_values
            else 0.5
        )
        avg_time_to_edge_score = (
            (sum(time_to_edge_values) / len(time_to_edge_values))
            if time_to_edge_values
            else 0.5
        )
        decaying_edge_ratio = (decaying_edge_rows / n) if n else 0.0
        drift_thresholds = self._drift_thresholds(guardrails)
        drift_monitor = self._build_drift_monitor(
            avg_edge_decay_score=avg_edge_decay_score,
            decaying_edge_ratio=decaying_edge_ratio,
            avg_fill_quality_score=avg_fill_quality_score,
            avg_time_to_edge_score=avg_time_to_edge_score,
            thresholds=drift_thresholds,
        )
        drift_score = safe_float(drift_monitor.get("concept_drift_score"), 0.0)
        max_extra_step = min(
            max_step * 0.6,
            drift_thresholds["max_drift_down_weight_step"],
        )
        max_total_step = max_step + max_extra_step

        for k in FEATURE_KEYS:
            old = safe_float(weights.get(k), 0.0)
            step = max(-max_step, min(max_step, learning_rate * gradients[k]))
            if drift_monitor["triggered"]:
                step *= float(drift_monitor["step_scale_under_drift"])
            new = old + step
            if drift_monitor["triggered"]:
                extra = min(abs(new), max_extra_step * max(0.0, min(drift_score, 1.0)))
                if new > 0:
                    new -= extra
                elif new < 0:
                    new += extra
            delta = new - old
            delta = self._clip(delta, -max_total_step, max_total_step)
            new = old + delta
            weights[k] = max(min_w, min(max_w, new))

        new_state = dict(state)
        new_state["weights"] = weights
        new_state["drift_monitor"] = {
            **drift_monitor,
            "updated_at": utc_now_iso(),
        }
        new_state["update_stats"] = dict(state.get("update_stats") or {})
        new_state["update_stats"]["updates_applied"] = int(new_state["update_stats"].get("updates_applied", 0)) + 1
        new_state["update_stats"]["last_update_ts"] = utc_now_iso()
        new_state["update_stats"]["last_training_rows"] = n
        new_state["update_stats"]["last_total_row_weight"] = round(total_row_weight, 4)
        new_state["update_stats"]["last_average_fill_quality_score"] = round(avg_fill_quality_score, 4)
        new_state["update_stats"]["last_average_edge_decay_score"] = round(avg_edge_decay_score, 4)
        new_state["update_stats"]["last_average_time_to_edge_score"] = round(avg_time_to_edge_score, 4)
        new_state["update_stats"]["last_decaying_edge_ratio"] = round(decaying_edge_ratio, 4)
        new_state["update_stats"]["last_concept_drift_score"] = drift_monitor["concept_drift_score"]
        new_state["update_stats"]["last_drift_triggered"] = bool(drift_monitor["triggered"])
        new_state["update_stats"]["last_drift_severity"] = drift_monitor["severity"]
        new_state["update_stats"]["last_drift_breaches"] = list(drift_monitor["breached_signals"])
        new_state["update_stats"]["last_drift_signals"] = list(drift_monitor["signals"])
        new_state["update_stats"]["last_drift_thresholds"] = dict(drift_monitor["thresholds"])
        new_state["update_stats"]["last_down_weighting_multiplier"] = drift_monitor["down_weighting_multiplier"]
        new_state["update_stats"]["last_step_scale_under_drift"] = drift_monitor["step_scale_under_drift"]
        new_state["update_stats"]["last_auto_down_weighting_applied"] = bool(drift_monitor["triggered"])
        new_state["update_stats"]["last_max_total_weight_step"] = round(max_total_step, 4)
        new_state["update_stats"]["last_max_drift_down_weight_step"] = round(max_extra_step, 4)
        new_state["update_stats"]["last_validation"] = validation
        return new_state


def parse_args():
    p = argparse.ArgumentParser(description="Update research model weights")
    p.add_argument("--state-json", required=True)
    p.add_argument("--labeled-dataset-json", required=True)
    p.add_argument("--output-json", required=True)
    p.add_argument("--learning-rate", type=float, default=0.01)
    return p.parse_args()


def main():
    args = parse_args()
    state = load_state(Path(args.state_json))
    labeled_dataset = json.loads(Path(args.labeled_dataset_json).read_text(encoding="utf-8"))

    updater = ResearchModelWeightUpdater()
    new_state = updater.update(
        state=state,
        labeled_dataset=labeled_dataset,
        learning_rate=args.learning_rate,
    )

    out = Path(args.output_json)
    save_state(out, new_state)
    print(out)


if __name__ == "__main__":
    main()
