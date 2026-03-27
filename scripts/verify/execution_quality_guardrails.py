#!/usr/bin/env python3
"""
Execution quality guardrail check.

Reads recent order intents and reports sizing/price-quality health.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any, Dict, List


def _load_rows(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _strategy_bucket(row: Dict[str, Any]) -> str:
    style = str(((row.get("candidate_context") or {}).get("strategy_style")) or "").lower()
    if "medium_long" in style or "swing" in style or "macro" in style:
        return "medium_long"
    return "day_trade"


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--limit", type=int, default=1500)
    p.add_argument("--max-fallback-rate", type=float, default=0.15)
    p.add_argument("--min-day-median-notional", type=float, default=3000.0)
    p.add_argument("--min-medium-median-notional", type=float, default=2000.0)
    args = p.parse_args()

    repo = Path(args.repo_root).resolve()
    rows = _load_rows(repo / "logs" / "execution" / "order_intents.jsonl", args.limit)
    draft_rows = [r for r in rows if str(r.get("status")) == "draft"]

    summary: Dict[str, Any] = {
        "rows_total": len(rows),
        "draft_rows": len(draft_rows),
        "fallback_count": 0,
        "missing_price_count": 0,
        "strategy": {"day_trade": {}, "medium_long": {}},
        "violations": [],
    }
    if not draft_rows:
        print(json.dumps(summary, indent=2))
        return 1

    per_strategy_notional: Dict[str, List[float]] = {"day_trade": [], "medium_long": []}

    for row in draft_rows:
        req = row.get("order_request") or {}
        gs = req.get("_gs_sizing") or {}
        strat = _strategy_bucket(row)
        decision_price = _safe_float(gs.get("decision_price"))
        qty = _safe_float(req.get("qty"))
        if decision_price is None:
            summary["missing_price_count"] += 1
        if gs.get("sizing_method_used") not in {"notional_pct"}:
            summary["fallback_count"] += 1
        if decision_price and qty:
            per_strategy_notional[strat].append(decision_price * qty)

    fallback_rate = summary["fallback_count"] / max(len(draft_rows), 1)
    missing_price_rate = summary["missing_price_count"] / max(len(draft_rows), 1)
    summary["fallback_rate"] = round(fallback_rate, 4)
    summary["missing_price_rate"] = round(missing_price_rate, 4)

    for strat in ("day_trade", "medium_long"):
        vals = per_strategy_notional[strat]
        if vals:
            summary["strategy"][strat] = {
                "orders": len(vals),
                "median_notional": round(median(vals), 2),
                "min_notional": round(min(vals), 2),
                "max_notional": round(max(vals), 2),
            }
        else:
            summary["strategy"][strat] = {"orders": 0}

    if fallback_rate > args.max_fallback_rate:
        summary["violations"].append(
            f"fallback_rate {fallback_rate:.2%} exceeds {args.max_fallback_rate:.2%}"
        )
    day_median = summary["strategy"]["day_trade"].get("median_notional")
    if day_median is not None and day_median < args.min_day_median_notional:
        summary["violations"].append(
            f"day_trade median_notional {day_median:.2f} below {args.min_day_median_notional:.2f}"
        )
    ml_median = summary["strategy"]["medium_long"].get("median_notional")
    if ml_median is not None and ml_median < args.min_medium_median_notional:
        summary["violations"].append(
            f"medium_long median_notional {ml_median:.2f} below {args.min_medium_median_notional:.2f}"
        )

    print(json.dumps(summary, indent=2))
    return 1 if summary["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
