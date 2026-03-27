"""Merge router or reconciliation output into TradeOutcomeTelemetry schema.

Normalizes multiple input formats (trades, orders, bound_order_attempts)
into the canonical TradeOutcomeRecord schema for the evaluator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from src.research.trade_outcome_telemetry_schema import TradeOutcomeRecord, TradeOutcomeTelemetry


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


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

        rec = TradeOutcomeRecord(
            symbol=symbol,
            trade_executed=trade_executed,
            direction=direction,
            realized_return_bps=realized_return_bps,
            expected_impact_bps=safe_float(row.get("expected_impact_bps"), None) if row.get("expected_impact_bps") is not None else None,
            realized_slippage_bps=safe_float(row.get("realized_slippage_bps"), None) if row.get("realized_slippage_bps") is not None else None,
            fill_rate=safe_float(row.get("fill_rate"), None) if row.get("fill_rate") is not None else None,
            time_window=row.get("time_window"),
            incident_mode=row.get("incident_mode"),
            research_score_used=safe_float(row.get("research_score_used"), None) if row.get("research_score_used") is not None else None,
            quantum_influenced=row.get("quantum_influenced"),
            metadata={
                "status": row.get("status"),
                "router_run_id": row.get("router_run_id"),
                "package_id": row.get("package_id"),
                "intent_id": row.get("intent_id"),
            },
        )
        rows.append(rec)

    return rows


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
