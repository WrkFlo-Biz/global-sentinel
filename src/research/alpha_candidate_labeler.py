"""Label training dataset rows for supervised learning.

Assigns alpha labels (strong_positive/positive/neutral/negative/strong_negative)
and execution quality labels based on realized returns, slippage, and fill rate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from src.research.training.crisis_training_dataset import normalize_validation_labels


class AlphaCandidateLabeler:
    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @classmethod
    def _value_or_pct(cls, row: Dict[str, Any], value_key: str, pct_key: str) -> float | None:
        value = cls._safe_float(row.get(value_key), None)
        if value is not None:
            return value
        pct_value = cls._safe_float(row.get(pct_key), None)
        if pct_value is None:
            return None
        return pct_value * 100.0

    @staticmethod
    def _time_to_edge_label(time_to_edge_bucket: str | None) -> str:
        mapping = {
            "immediate": "immediate_edge",
            "fast": "fast_edge",
            "moderate": "moderate_edge",
            "slow": "slow_edge",
        }
        return mapping.get(str(time_to_edge_bucket or ""), "unknown")

    @staticmethod
    def _degrade_label(label: str, steps: int) -> str:
        ladder = [
            "strong_negative",
            "negative",
            "neutral",
            "positive",
            "strong_positive",
        ]
        if label not in ladder or steps <= 0:
            return label
        idx = ladder.index(label)
        return ladder[max(0, min(len(ladder) - 1, idx - steps))]

    def label_rows(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        out_rows: List[Dict[str, Any]] = []

        for row in dataset.get("rows", []):
            realized = self._safe_float(row.get("realized_return_bps"), None)
            slippage = row.get("realized_slippage_bps")
            fill_rate = row.get("fill_rate")
            labels = normalize_validation_labels(
                {
                    "mfe_pct": row.get("mfe_pct"),
                    "mae_pct": row.get("mae_pct"),
                    "max_favorable_excursion_bps": row.get("max_favorable_excursion_bps"),
                    "max_adverse_excursion_bps": row.get("max_adverse_excursion_bps"),
                    "time_to_edge_minutes": row.get("time_to_edge_minutes"),
                    "fill_quality_score": row.get("fill_quality_score"),
                    "fill_slippage_bps": row.get("realized_slippage_bps"),
                    "fill_rate": row.get("fill_rate"),
                    "realized_return_bps": realized,
                    "realized_edge_capture_ratio": row.get("realized_edge_capture_ratio"),
                    "adverse_excursion_ratio": row.get("adverse_excursion_ratio"),
                    "post_event_drift_bps": row.get("post_event_drift_bps"),
                    "post_event_drift_score": row.get("post_event_drift_score"),
                    "post_event_drift_label": row.get("post_event_drift_label"),
                    "edge_decay_score": row.get("edge_decay_score"),
                    "edge_decay_weight": row.get("edge_decay_weight"),
                    "edge_decay_label": row.get("edge_decay_label"),
                }
            )
            mfe_bps = self._value_or_pct(row, "max_favorable_excursion_bps", "mfe_pct")
            if mfe_bps is None and labels.get("mfe_pct") is not None:
                mfe_bps = float(labels["mfe_pct"]) * 100.0
            mae_bps = self._value_or_pct(row, "max_adverse_excursion_bps", "mae_pct")
            if mae_bps is None and labels.get("mae_pct") is not None:
                mae_bps = abs(float(labels["mae_pct"])) * 100.0
            time_to_edge_minutes = labels.get("time_to_edge_minutes")
            fill_quality_label = row.get("fill_quality_label")
            execution_quality_label = row.get("execution_quality_label")
            fill_quality_score = labels.get("fill_quality_score")
            edge_decay_score = labels.get("edge_decay_score")
            edge_decay_weight = labels.get("edge_decay_weight")
            post_event_drift_bps = labels.get("post_event_drift_bps")
            post_event_drift_score = labels.get("post_event_drift_score")
            post_event_drift_label = labels.get("post_event_drift_label")
            edge_capture_ratio = labels.get("realized_edge_capture_ratio")

            label = "neutral"
            if realized is not None:
                if realized >= 75:
                    label = "strong_positive"
                elif realized >= 15:
                    label = "positive"
                elif realized <= -75:
                    label = "strong_negative"
                elif realized <= -15:
                    label = "negative"
            elif mfe_bps is not None and edge_decay_score is not None:
                if mfe_bps >= 75 and edge_decay_score < 0.35:
                    label = "positive"
                elif mfe_bps >= 150 and edge_decay_score < 0.25:
                    label = "strong_positive"

            quality = str(execution_quality_label or "unknown")
            if fill_rate is not None:
                fr = float(fill_rate)
                if fr >= 0.95:
                    quality = "high_fill_quality"
                elif fr >= 0.70:
                    quality = "medium_fill_quality"
                else:
                    quality = "low_fill_quality"

            if slippage is not None:
                sl = float(slippage)
                if sl >= 20:
                    quality = "poor_execution_quality"
                elif sl <= 5 and quality != "unknown":
                    quality = "good_execution_quality"

            fill_quality = str(fill_quality_label or "unknown")
            if fill_rate is not None:
                fr = float(fill_rate)
                if fr >= 0.98:
                    fill_quality = "excellent_fill_quality"
                elif fr >= 0.85:
                    fill_quality = "strong_fill_quality"
                elif fr >= 0.70:
                    fill_quality = "adequate_fill_quality"
                else:
                    fill_quality = "weak_fill_quality"
            if slippage is not None and float(slippage) >= 20:
                fill_quality = "slippage_heavy_fill_quality"

            if fill_quality_score is not None:
                if fill_quality_score >= 0.9:
                    fill_quality = "excellent_fill_quality"
                elif fill_quality_score >= 0.75:
                    fill_quality = "strong_fill_quality"
                elif fill_quality_score >= 0.55:
                    fill_quality = "adequate_fill_quality"
                else:
                    fill_quality = "weak_fill_quality"
            if fill_quality == "unknown":
                fill_quality = str(labels.get("fill_quality_label") or "unknown")
            if quality == "unknown":
                quality = str(labels.get("execution_quality_label") or "unknown")

            if edge_decay_score is not None:
                if edge_decay_score >= 0.8:
                    label = self._degrade_label(label, 2)
                    quality = "decayed_execution_quality"
                elif edge_decay_score >= 0.55:
                    label = self._degrade_label(label, 1)

            if edge_capture_ratio is not None and edge_capture_ratio < 0.25 and label in {"positive", "strong_positive"}:
                label = self._degrade_label(label, 1)
            if fill_quality_score is not None and fill_quality_score < 0.4 and label in {"positive", "strong_positive"}:
                label = self._degrade_label(label, 1)

            edge_retention_score = None
            if edge_decay_score is not None:
                edge_retention_score = round(max(0.0, min(1.0, 1.0 - edge_decay_score)), 4)

            sample_weight = self._safe_float(row.get("sample_weight"), None)
            if sample_weight is None:
                fq = float(fill_quality_score or 0.7)
                dw = float(edge_decay_weight or 0.7)
                tes = float(labels.get("time_to_edge_score") or 0.5)
                sample_weight = round(max(0.15, min(1.5, (fq * 0.35) + (dw * 0.4) + (tes * 0.25))), 4)

            new_row = dict(row)
            new_row["alpha_label"] = label
            new_row["execution_quality_label"] = quality
            new_row["fill_quality_label"] = fill_quality
            new_row["max_favorable_excursion_bps"] = mfe_bps
            new_row["max_adverse_excursion_bps"] = mae_bps
            new_row["time_to_edge_minutes"] = time_to_edge_minutes
            new_row["time_to_edge_score"] = labels.get("time_to_edge_score")
            new_row["time_to_edge_bucket"] = labels.get("time_to_edge_bucket")
            new_row["time_to_edge_label"] = self._time_to_edge_label(labels.get("time_to_edge_bucket"))
            new_row["fill_quality_score"] = fill_quality_score
            new_row["realized_edge_capture_ratio"] = edge_capture_ratio
            new_row["adverse_excursion_ratio"] = labels.get("adverse_excursion_ratio")
            new_row["post_event_drift_bps"] = post_event_drift_bps
            new_row["post_event_drift_score"] = post_event_drift_score
            new_row["post_event_drift_label"] = post_event_drift_label
            new_row["edge_decay_score"] = edge_decay_score
            new_row["edge_decay_weight"] = edge_decay_weight
            new_row["edge_decay_label"] = labels.get("edge_decay_label")
            new_row["edge_retention_score"] = edge_retention_score
            new_row["sample_weight"] = sample_weight
            out_rows.append(new_row)

        return {
            "schema_version": "alpha_candidate_labels.v1",
            "row_count": len(out_rows),
            "rows": out_rows,
        }


def _self_test_label_enrichment() -> None:
    payload = {
        "rows": [
            {
                "symbol": "SPY",
                "realized_return_bps": 55.0,
                "max_favorable_excursion_bps": 120.0,
                "max_adverse_excursion_bps": 35.0,
                "time_to_edge_minutes": 18.0,
                "realized_slippage_bps": 4.0,
                "fill_rate": 0.95,
                "post_event_drift_score": 0.4,
            }
        ]
    }
    out = AlphaCandidateLabeler().label_rows(payload)
    assert out["row_count"] == 1
    row = out["rows"][0]
    assert row["time_to_edge_label"] == "fast_edge"
    assert row["fill_quality_label"] in {
        "excellent_fill_quality",
        "strong_fill_quality",
        "adequate_fill_quality",
        "weak_fill_quality",
    }
    assert row["execution_quality_label"] != "unknown"
    assert row["post_event_drift_score"] == 0.4
    assert row["post_event_drift_label"] is not None


def parse_args():
    p = argparse.ArgumentParser(description="Label alpha candidates")
    p.add_argument("--dataset-json", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    ds = json.loads(Path(args.dataset_json).read_text(encoding="utf-8"))
    labeled = AlphaCandidateLabeler().label_rows(ds)

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(labeled, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
