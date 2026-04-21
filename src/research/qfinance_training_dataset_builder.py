"""Build row-wise training datasets from encoded candidates + trade outcomes.

Joins encoded candidate features with realized trade telemetry
and optional research scores for downstream labeling and training.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from src.research.training.crisis_training_dataset import normalize_validation_labels


class QFinanceTrainingDatasetBuilder:
    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def build(
        self,
        *,
        encoded_candidates: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        trade_outcomes: Dict[str, Any],
        research_score: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        trade_map = {
            str(r.get("symbol")): r
            for r in (trade_outcomes.get("trades") or [])
            if r.get("symbol")
        }

        rows: List[Dict[str, Any]] = []
        for c in encoded_candidates:
            sym = str(c.get("symbol", ""))
            out = trade_map.get(sym, {})
            labels = normalize_validation_labels(
                {
                    "mfe_pct": out.get("mfe_pct"),
                    "mae_pct": out.get("mae_pct"),
                    "max_favorable_excursion_bps": out.get("max_favorable_excursion_bps"),
                    "max_adverse_excursion_bps": out.get("max_adverse_excursion_bps"),
                    "time_to_edge_minutes": out.get("time_to_edge_minutes"),
                    "fill_quality_score": out.get("fill_quality_score"),
                    "fill_slippage_bps": out.get("realized_slippage_bps"),
                    "fill_rate": out.get("fill_rate"),
                    "realized_return_bps": out.get("realized_return_bps"),
                    "realized_edge_capture_ratio": out.get("realized_edge_capture_ratio"),
                    "adverse_excursion_ratio": out.get("adverse_excursion_ratio"),
                    "post_event_drift_bps": out.get("post_event_drift_bps"),
                    "post_event_drift_score": out.get("post_event_drift_score"),
                    "post_event_drift_label": out.get("post_event_drift_label"),
                    "edge_decay_score": out.get("edge_decay_score"),
                    "edge_decay_weight": out.get("edge_decay_weight"),
                    "edge_decay_label": out.get("edge_decay_label"),
                }
            )
            fill_quality_score = labels.get("fill_quality_score")
            edge_decay_score = labels.get("edge_decay_score")
            edge_decay_weight = labels.get("edge_decay_weight")
            time_to_edge_score = labels.get("time_to_edge_score")
            fill_quality_label = out.get("fill_quality_label") or labels.get("fill_quality_label")
            execution_quality_label = out.get("execution_quality_label") or labels.get("execution_quality_label")
            max_favorable_excursion_bps = out.get("max_favorable_excursion_bps")
            if max_favorable_excursion_bps is None:
                max_favorable_excursion_bps = labels.get("max_favorable_excursion_bps")
            max_adverse_excursion_bps = out.get("max_adverse_excursion_bps")
            if max_adverse_excursion_bps is None:
                max_adverse_excursion_bps = labels.get("max_adverse_excursion_bps")
            edge_retention_score = None
            if edge_decay_score is not None:
                edge_retention_score = round(
                    max(0.0, min(1.0, 1.0 - float(edge_decay_score))),
                    4,
                )

            sample_weight = self._safe_float(out.get("sample_weight"), None)
            if sample_weight is None:
                fq = float(fill_quality_score or 0.7)
                dw = float(edge_decay_weight or 0.7)
                tes = float(time_to_edge_score or 0.5)
                executed_boost = 1.0 if out.get("trade_executed") else 0.6
                sample_weight = round(
                    max(0.15, min(1.5, ((fq * 0.4) + (dw * 0.35) + (tes * 0.25)) * executed_boost)),
                    4,
                )

            row = {
                "symbol": sym,
                "sector": c.get("sector"),
                "theme": c.get("theme"),
                "timestamp_utc": out.get("timestamp_utc") or out.get("exit_time") or out.get("entry_time") or c.get("timestamp_utc") or c.get("event_timestamp_utc"),
                "base_score": c.get("base_score"),
                "event_score": c.get("event_score"),
                "quality_score": c.get("quality_score"),
                "anomaly_score": c.get("anomaly_score"),
                "liquidity_score": c.get("liquidity_score"),
                "volatility_penalty": c.get("volatility_penalty"),
                "regime_alignment": c.get("regime_alignment"),
                "preopt_feature_score": c.get("preopt_feature_score"),
                "regime_shift_probability": regime_state.get("regime_shift_probability"),
                "macro_state": regime_state.get("macro_state"),
                "geopolitical_state": regime_state.get("geopolitical_state"),
                "trade_executed": out.get("trade_executed"),
                "direction": out.get("direction"),
                "realized_return_bps": out.get("realized_return_bps"),
                "realized_slippage_bps": out.get("realized_slippage_bps"),
                "fill_rate": out.get("fill_rate"),
                "event_novelty_score": out.get("event_novelty_score"),
                "max_favorable_excursion_bps": max_favorable_excursion_bps,
                "max_adverse_excursion_bps": max_adverse_excursion_bps,
                "time_to_edge_minutes": labels.get("time_to_edge_minutes"),
                "time_to_edge_score": time_to_edge_score,
                "time_to_edge_bucket": labels.get("time_to_edge_bucket"),
                "time_to_edge_label": labels.get("time_to_edge_label"),
                "fill_quality_score": fill_quality_score,
                "fill_quality_label": fill_quality_label,
                "execution_quality_label": execution_quality_label,
                "alpha_label": out.get("alpha_label"),
                "realized_edge_capture_ratio": labels.get("realized_edge_capture_ratio"),
                "adverse_excursion_ratio": labels.get("adverse_excursion_ratio"),
                "post_event_drift_bps": labels.get("post_event_drift_bps"),
                "post_event_drift_score": labels.get("post_event_drift_score"),
                "post_event_drift_label": labels.get("post_event_drift_label"),
                "edge_decay_score": edge_decay_score,
                "edge_decay_weight": edge_decay_weight,
                "edge_decay_label": labels.get("edge_decay_label"),
                "edge_retention_score": edge_retention_score,
                "sample_weight": sample_weight,
                "expected_edge_bps": out.get("expected_edge_bps"),
                "expected_cost_bps": out.get("expected_cost_bps"),
                "net_expected_value_bps": out.get("net_expected_value_bps"),
                "quantum_influenced": out.get("quantum_influenced"),
                "research_score_used": out.get("research_score_used"),
                "attached_research_score": (research_score or {}).get("research_score"),
                "attached_recommended_influence": (research_score or {}).get("recommended_influence"),
            }
            rows.append(row)

        return {
            "schema_version": "qfinance_training_dataset.v1",
            "row_count": len(rows),
            "rows": rows,
        }


def _self_test_label_enrichment() -> None:
    builder = QFinanceTrainingDatasetBuilder()
    ds = builder.build(
        encoded_candidates=[
            {
                "symbol": "SPY",
                "base_score": 0.5,
                "event_score": 0.2,
                "quality_score": 0.7,
                "anomaly_score": 0.1,
                "liquidity_score": 0.8,
                "volatility_penalty": 0.1,
                "regime_alignment": 0.4,
                "preopt_feature_score": 0.6,
            }
        ],
        regime_state={"regime_shift_probability": 0.4, "macro_state": "mixed", "geopolitical_state": "monitoring"},
        trade_outcomes={
            "trades": [
                {
                    "symbol": "SPY",
                    "trade_executed": True,
                    "realized_return_bps": 25.0,
                    "max_favorable_excursion_bps": 100.0,
                    "max_adverse_excursion_bps": 45.0,
                    "time_to_edge_minutes": 20.0,
                    "fill_rate": 0.96,
                    "realized_slippage_bps": 4.0,
                    "post_event_drift_score": 0.25,
                }
            ]
        },
        research_score=None,
    )
    assert ds["row_count"] == 1
    row = ds["rows"][0]
    assert row["time_to_edge_label"] == "fast_edge"
    assert row["fill_quality_label"] is not None
    assert row["execution_quality_label"] is not None
    assert row["post_event_drift_score"] == 0.25
    assert row["post_event_drift_label"] is not None


def parse_args():
    p = argparse.ArgumentParser(description="Build QFinance training dataset")
    p.add_argument("--encoded-candidates-json", required=True)
    p.add_argument("--regime-state-json", required=True)
    p.add_argument("--trade-outcomes-json", required=True)
    p.add_argument("--research-score-json", required=False)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()

    encoded_candidates = json.loads(Path(args.encoded_candidates_json).read_text(encoding="utf-8"))
    regime_state = json.loads(Path(args.regime_state_json).read_text(encoding="utf-8"))
    trade_outcomes = json.loads(Path(args.trade_outcomes_json).read_text(encoding="utf-8"))
    research_score = None
    if args.research_score_json:
        research_score = json.loads(Path(args.research_score_json).read_text(encoding="utf-8"))

    ds = QFinanceTrainingDatasetBuilder().build(
        encoded_candidates=encoded_candidates,
        regime_state=regime_state,
        trade_outcomes=trade_outcomes,
        research_score=research_score,
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ds, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
