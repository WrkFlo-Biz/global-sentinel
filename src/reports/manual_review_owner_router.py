#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Manual Review Owner Router

Purpose:
- Route manual review queue items to suggested owners (COO/CFO/CAIO/CIO)
- Provide operational ownership recommendations based on reason / broker state / strategy context
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple  # noqa: F401


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class ManualReviewOwnerRouter:
    def __init__(self):
        pass

    def route(self, manual_review_report: Dict[str, Any], lag_sla_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        items = manual_review_report.get("oldest_unresolved") or []
        lag_severity = ((lag_sla_report or {}).get("summary") or {}).get("severity")

        routed = []
        owner_counts: Dict[str, int] = {"COO": 0, "CFO": 0, "CAIO": 0, "CIO": 0, "UNASSIGNED": 0}

        for row in items:
            owner, reason = self._assign_owner(row, lag_severity=lag_severity)
            owner_counts[owner] = owner_counts.get(owner, 0) + 1
            routed.append({
                **row,
                "suggested_owner": owner,
                "owner_routing_reason": reason,
                "sla_priority": self._priority_for_row(row, lag_severity=lag_severity),
            })

        # Sort by priority then age
        priority_rank = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
        routed_sorted = sorted(
            routed,
            key=lambda r: (priority_rank.get(r.get("sla_priority"), 9), -(r.get("age_minutes") or 0))
        )

        out = {
            "schema_version": "manual_review_owner_router.v1",
            "summary": {
                "manual_review_items_considered": len(items),
                "owner_counts": owner_counts,
                "lag_sla_severity": lag_severity,
            },
            "routed_items": routed_sorted,
        }
        return out

    def _assign_owner(self, row: Dict[str, Any], lag_severity: Optional[str]) -> Tuple[str, str]:
        reason = str(row.get("manual_review_reason") or "unknown")
        broker_status = str(row.get("broker_status") or "")
        age_min = row.get("age_minutes") or 0

        # Ops-first issues
        if "reconciliation" in reason or "stale_open" in reason or broker_status in {"new", "accepted", "pending"}:
            return "COO", "ops/reconciliation/staleness"

        # Risk/reject/execution risk issues
        if "reject" in reason or "risk" in reason or "limit" in reason or "exposure" in reason:
            return "CFO", "risk/rejection/limits"

        # Model/policy/logic tuning issues
        if "threshold" in reason or "policy" in reason or "mismatch" in reason:
            return "CAIO", "policy/model/logic"

        # Strategy/escalation
        if lag_severity == "critical" and age_min > 60:
            return "CIO", "critical lag + aging item strategy oversight"

        # default
        return "COO", "default operational triage"

    def _priority_for_row(self, row: Dict[str, Any], lag_severity: Optional[str]) -> str:
        age = row.get("age_minutes") or 0
        broker_status = str(row.get("broker_status") or "")
        reason = str(row.get("manual_review_reason") or "")

        if lag_severity == "critical" and age > 30:
            return "P1"
        if "stale_open" in reason and age > 15:
            return "P1"
        if broker_status in {"partially_filled", "filled"} and age > 10:
            return "P1"
        if age > 240:
            return "P2"
        if age > 60:
            return "P3"
        return "P4"


def render_markdown(rep: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Manual Review Owner Routing")
    lines.append("")
    s = rep.get("summary") or {}
    lines.append(f"- manual_review_items_considered: {s.get('manual_review_items_considered')}")
    lines.append(f"- lag_sla_severity: {s.get('lag_sla_severity')}")
    lines.append(f"- owner_counts: {s.get('owner_counts')}")
    lines.append("")
    lines.append("| Priority | Owner | Intent ID | Symbol | Reason | Age (min) | Routing Reason |")
    lines.append("|---|---|---|---|---|---:|---|")
    for r in (rep.get("routed_items") or [])[:20]:
        lines.append(
            f"| {r.get('sla_priority')} | {r.get('suggested_owner')} | {r.get('intent_id')} | "
            f"{r.get('symbol')} | {r.get('manual_review_reason')} | {r.get('age_minutes')} | {r.get('owner_routing_reason')} |"
        )
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--manual-review-report-json", required=True)
    p.add_argument("--lag-sla-report-json", default=None)
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    mr = load_json(Path(args.manual_review_report_json))
    lag = load_json(Path(args.lag_sla_report_json)) if args.lag_sla_report_json else None

    rep = ManualReviewOwnerRouter().route(mr, lag_sla_report=lag)

    if args.output_json:
        p = Path(args.output_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    else:
        print(json.dumps(rep, indent=2))

    if args.output_md:
        p = Path(args.output_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(rep), encoding="utf-8")


if __name__ == "__main__":
    main()
