#!/usr/bin/env python3
"""Evaluate whether classical or quantum recommendations aligned with actual trade outcomes.

Compares recommended symbols/directions against realized trade telemetry
to determine which optimizer produced better real-world results.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def top_symbols(result: Dict[str, Any], limit: int = 10) -> List[str]:
    rows = result.get("ranked_solutions") or []
    return [str(row["symbol"]) for row in rows[:limit] if row.get("symbol")]


def compute_overlap_score(recommended: List[str], realized: List[str]) -> float:
    if not recommended:
        return 0.0
    return len(set(recommended) & set(realized)) / max(len(recommended), 1)


def compute_directional_score(recommended_rows: List[Dict[str, Any]], outcome_rows: List[Dict[str, Any]]) -> float:
    """Score directional alignment between recommendations and realized returns."""
    outcome_map = {str(r.get("symbol")): r for r in outcome_rows}
    scored = 0
    total = 0

    for row in recommended_rows:
        sym = str(row.get("symbol"))
        direction = str(row.get("direction", "long")).lower()
        realized = safe_float((outcome_map.get(sym) or {}).get("realized_return_bps"), 0.0)

        if sym not in outcome_map:
            continue

        total += 1
        if direction == "long" and realized > 0:
            scored += 1
        elif direction == "short" and realized < 0:
            scored += 1

    return scored / max(total, 1)


def evaluate(
    *,
    classical_result: Dict[str, Any],
    quantum_result: Dict[str, Any],
    trade_outcomes: Dict[str, Any],
) -> Dict[str, Any]:
    outcomes = trade_outcomes.get("trades") or []

    classical_syms = top_symbols(classical_result)
    quantum_syms = top_symbols(quantum_result)
    realized_syms = [str(r.get("symbol")) for r in outcomes if r.get("trade_executed")]

    classical_overlap = compute_overlap_score(classical_syms, realized_syms)
    quantum_overlap = compute_overlap_score(quantum_syms, realized_syms)

    classical_directional = compute_directional_score(classical_result.get("ranked_solutions") or [], outcomes)
    quantum_directional = compute_directional_score(quantum_result.get("ranked_solutions") or [], outcomes)

    pnl_map = {str(r.get("symbol")): safe_float(r.get("realized_return_bps"), 0.0) for r in outcomes}
    classical_realized_sum = sum(pnl_map.get(sym, 0.0) for sym in classical_syms)
    quantum_realized_sum = sum(pnl_map.get(sym, 0.0) for sym in quantum_syms)

    winner = "tie"
    if quantum_realized_sum > classical_realized_sum:
        winner = "quantum"
    elif classical_realized_sum > quantum_realized_sum:
        winner = "classical"

    return {
        "request_id": classical_result.get("request_id") or quantum_result.get("request_id"),
        "package_id": classical_result.get("package_id") or quantum_result.get("package_id"),
        "classical_overlap_score": classical_overlap,
        "quantum_overlap_score": quantum_overlap,
        "classical_directional_score": classical_directional,
        "quantum_directional_score": quantum_directional,
        "classical_realized_return_bps_sum": classical_realized_sum,
        "quantum_realized_return_bps_sum": quantum_realized_sum,
        "winner": winner,
        "note": "Extend with slippage-adjusted P&L, fill quality, impact error, and holding-period aware scoring.",
    }


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate trade outcomes against optimizer recommendations")
    p.add_argument("--classical-json", required=True)
    p.add_argument("--quantum-json", required=True)
    p.add_argument("--trade-outcomes-json", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    result = evaluate(
        classical_result=load_json(Path(args.classical_json)),
        quantum_result=load_json(Path(args.quantum_json)),
        trade_outcomes=load_json(Path(args.trade_outcomes_json)),
    )
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
