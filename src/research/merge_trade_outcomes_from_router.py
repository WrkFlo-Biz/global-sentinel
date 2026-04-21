"""Merge router or reconciliation output into TradeOutcomeTelemetry schema.

Normalizes multiple input formats (trades, orders, bound_order_attempts)
into the canonical TradeOutcomeRecord schema for the evaluator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from src.research.training.crisis_training_dataset import normalize_validation_labels
from src.research.trade_outcome_telemetry_schema import TradeOutcomeRecord, TradeOutcomeTelemetry


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _first_value(row: Dict[str, Any], *keys: str) -> Any:
    metadata = row.get("metadata") or {}
    order_metadata = row.get("order_metadata") or {}
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
        if isinstance(metadata, dict) and metadata.get(key) is not None:
            return metadata.get(key)
        if isinstance(order_metadata, dict) and order_metadata.get(key) is not None:
            return order_metadata.get(key)
    return None


def normalize_trade_rows(router_payload: Dict[str, Any]) -> List[TradeOutcomeRecord]:
    rows: List[TradeOutcomeRecord] = []

    source_rows = (
        router_payload.get("trades")
        or router_payload.get("orders")
        or router_payload.get("bound_order_attempts")
        or []
    )

    for row in source_rows:
        symbol = str(row.get("symbol") or row.get("ticker") or "")
        if not symbol:
            continue

        direction = str(row.get("direction", "long")).lower()
        trade_executed = bool(
            row.get("trade_executed")
            or row.get("filled_qty")
            or row.get("fill_rate")
            or row.get("status") in {"filled", "partial_fill", "executed"}
        )

        realized_return_bps = safe_float(
            row.get("realized_return_bps", row.get("return_bps", row.get("pnl_bps", 0.0)))
        )
        labels = normalize_validation_labels(
            {
                "mfe_pct": _first_value(row, "mfe_pct"),
                "mae_pct": _first_value(row, "mae_pct"),
                "max_favorable_excursion_bps": _first_value(row, "max_favorable_excursion_bps", "mfe_bps"),
                "max_adverse_excursion_bps": _first_value(row, "max_adverse_excursion_bps", "mae_bps"),
                "time_to_edge_minutes": _first_value(row, "time_to_edge_minutes"),
                "fill_quality_score": _first_value(row, "fill_quality_score"),
                "fill_slippage_bps": _first_value(row, "realized_slippage_bps", "fill_slippage_bps"),
                "fill_rate": _first_value(row, "fill_rate"),
                "realized_return_bps": realized_return_bps,
                "realized_edge_capture_ratio": _first_value(row, "realized_edge_capture_ratio"),
                "adverse_excursion_ratio": _first_value(row, "adverse_excursion_ratio"),
                "post_event_drift_bps": _first_value(row, "post_event_drift_bps"),
                "post_event_drift_score": _first_value(row, "post_event_drift_score"),
                "post_event_drift_label": _first_value(row, "post_event_drift_label"),
                "edge_decay_score": _first_value(row, "edge_decay_score"),
                "edge_decay_weight": _first_value(row, "edge_decay_weight"),
                "edge_decay_label": _first_value(row, "edge_decay_label"),
            }
        )
        sample_weight = _first_value(row, "sample_weight")
        if sample_weight is None:
            fill_quality = labels.get("fill_quality_score")
            decay_weight = labels.get("edge_decay_weight")
            if fill_quality is not None or decay_weight is not None:
                sample_weight = round(
                    max(
                        0.15,
                        min(
                            1.5,
                            ((float(fill_quality or 0.7) * 0.55) + (float(decay_weight or 0.7) * 0.45)),
                        ),
                    ),
                    4,
                )

        fill_quality_label = _first_value(row, "fill_quality_label")
        if fill_quality_label is None:
            fill_quality_label = labels.get("fill_quality_label")
        execution_quality_label = _first_value(row, "execution_quality_label")
        if execution_quality_label is None:
            execution_quality_label = labels.get("execution_quality_label")
        max_favorable_excursion_bps = _first_value(row, "max_favorable_excursion_bps", "mfe_bps")
        if max_favorable_excursion_bps is None:
            max_favorable_excursion_bps = labels.get("max_favorable_excursion_bps")
        max_adverse_excursion_bps = _first_value(row, "max_adverse_excursion_bps", "mae_bps")
        if max_adverse_excursion_bps is None:
            max_adverse_excursion_bps = labels.get("max_adverse_excursion_bps")

        rec = TradeOutcomeRecord(
            symbol=symbol,
            trade_executed=trade_executed,
            direction=direction,
            realized_return_bps=realized_return_bps,
            timestamp_utc=str(_first_value(row, "timestamp_utc", "exit_time", "entry_time")) if _first_value(row, "timestamp_utc", "exit_time", "entry_time") is not None else None,
            event_novelty_score=safe_float(_first_value(row, "event_novelty_score"), None) if _first_value(row, "event_novelty_score") is not None else None,
            expected_edge_bps=safe_float(_first_value(row, "expected_edge_bps"), None) if _first_value(row, "expected_edge_bps") is not None else None,
            expected_cost_bps=safe_float(_first_value(row, "expected_cost_bps"), None) if _first_value(row, "expected_cost_bps") is not None else None,
            net_expected_value_bps=safe_float(_first_value(row, "net_expected_value_bps"), None) if _first_value(row, "net_expected_value_bps") is not None else None,
            expected_impact_bps=safe_float(_first_value(row, "expected_impact_bps"), None) if _first_value(row, "expected_impact_bps") is not None else None,
            realized_slippage_bps=safe_float(_first_value(row, "realized_slippage_bps"), None) if _first_value(row, "realized_slippage_bps") is not None else None,
            fill_rate=safe_float(_first_value(row, "fill_rate"), None) if _first_value(row, "fill_rate") is not None else None,
            max_favorable_excursion_bps=safe_float(max_favorable_excursion_bps, None) if max_favorable_excursion_bps is not None else None,
            max_adverse_excursion_bps=safe_float(max_adverse_excursion_bps, None) if max_adverse_excursion_bps is not None else None,
            time_to_edge_minutes=labels.get("time_to_edge_minutes"),
            time_to_edge_score=labels.get("time_to_edge_score"),
            time_to_edge_bucket=labels.get("time_to_edge_bucket"),
            time_to_edge_label=labels.get("time_to_edge_label"),
            fill_quality_score=labels.get("fill_quality_score"),
            fill_quality_label=str(fill_quality_label) if fill_quality_label is not None else None,
            execution_quality_label=str(execution_quality_label) if execution_quality_label is not None else None,
            alpha_label=str(_first_value(row, "alpha_label")) if _first_value(row, "alpha_label") is not None else None,
            realized_edge_capture_ratio=labels.get("realized_edge_capture_ratio"),
            adverse_excursion_ratio=labels.get("adverse_excursion_ratio"),
            post_event_drift_bps=labels.get("post_event_drift_bps"),
            post_event_drift_score=labels.get("post_event_drift_score"),
            post_event_drift_label=labels.get("post_event_drift_label"),
            edge_decay_score=labels.get("edge_decay_score"),
            edge_decay_weight=labels.get("edge_decay_weight"),
            edge_decay_label=labels.get("edge_decay_label"),
            sample_weight=safe_float(sample_weight, None) if sample_weight is not None else None,
            time_window=row.get("time_window") or _first_value(row, "time_window"),
            incident_mode=_first_value(row, "incident_mode"),
            research_score_used=safe_float(_first_value(row, "research_score_used"), None) if _first_value(row, "research_score_used") is not None else None,
            quantum_influenced=_first_value(row, "quantum_influenced"),
            metadata={
                "status": row.get("status"),
                "router_run_id": row.get("router_run_id"),
                "package_id": row.get("package_id"),
                "intent_id": row.get("intent_id"),
                "timestamp_utc": _first_value(row, "timestamp_utc", "exit_time", "entry_time"),
                "strategy": _first_value(row, "strategy"),
                "strategy_family": _first_value(row, "strategy_family"),
                "strategy_style": _first_value(row, "strategy_style"),
                "underlying_strategy": _first_value(row, "underlying_strategy"),
                "event_novelty_score": _first_value(row, "event_novelty_score"),
                "post_event_drift_bps": labels.get("post_event_drift_bps"),
                "post_event_drift_score": labels.get("post_event_drift_score"),
                "post_event_drift_label": labels.get("post_event_drift_label"),
            },
        )
        rows.append(rec)

    return rows


def _self_test_schema_propagation() -> None:
    payload = {
        "trades": [
            {
                "symbol": "SPY",
                "direction": "long",
                "trade_executed": True,
                "realized_return_bps": 42.0,
                "max_favorable_excursion_bps": 88.0,
                "max_adverse_excursion_bps": 31.0,
                "time_to_edge_minutes": 12.0,
                "fill_rate": 0.97,
                "realized_slippage_bps": 4.0,
                "post_event_drift_score": 0.3,
            }
        ]
    }
    rows = normalize_trade_rows(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.max_favorable_excursion_bps == 88.0
    assert row.max_adverse_excursion_bps == 31.0
    assert row.time_to_edge_label == "immediate_edge"
    assert row.fill_quality_label in {
        "excellent_fill_quality",
        "strong_fill_quality",
        "adequate_fill_quality",
        "weak_fill_quality",
    }
    assert row.execution_quality_label is not None
    assert row.post_event_drift_score == 0.3
    assert row.post_event_drift_label is not None


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--router-json", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--package-id", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()

    payload = load_json(Path(args.router_json))
    trades = normalize_trade_rows(payload)

    telemetry = TradeOutcomeTelemetry(
        schema_version="trade_outcome_telemetry.v1",
        request_id=args.request_id,
        package_id=args.package_id,
        trades=trades,
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(telemetry.to_dict(), indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
