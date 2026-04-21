#!/usr/bin/env python3
"""
Global Sentinel V4.8 - Manual Review Queue Report

Purpose:
- Report on unresolved manual review intents from OrderIntentRegistry
- Track backlog size, age, reason patterns, and operational risk
- Support COO/CFO/CAIO review cadence

Inputs:
- logs/execution/order_intents.jsonl (append-only registry)
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.execution.strategy_learning import infer_strategy_family


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    return rows


class ManualReviewQueueReport:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def build(self) -> Dict[str, Any]:
        rows = read_jsonl(self.repo_root / "logs" / "execution" / "order_intents.jsonl")
        latest = self._latest_by_intent(rows)

        manual = [r for r in latest if str(r.get("status")) == "manual_review"]

        now = datetime.now(timezone.utc)
        age_minutes = []
        age_buckets = defaultdict(int)
        reason_counts = defaultdict(int)
        broker_counts = defaultdict(int)
        symbol_counts = defaultdict(int)
        strategy_counts = defaultdict(int)
        strategy_family_counts = defaultdict(int)
        underlying_strategy_counts = defaultdict(int)
        learning_adjusted_count = 0

        detailed_rows = []

        for r in manual:
            strategy_context = self._strategy_context(r)
            created = parse_ts(r.get("timestamp_utc")) or parse_ts(((r.get("audit") or {}).get("created_at_utc")))
            parse_ts(((r.get("audit") or {}).get("updated_at_utc")))  # noqa: F841
            parse_ts(((r.get("reconciliation") or {}).get("last_reconciled_at_utc")))  # noqa: F841

            age_min = None
            if created:
                age_min = (now - created).total_seconds() / 60.0
                age_minutes.append(age_min)
                age_buckets[self._age_bucket(age_min)] += 1

            broker_name = ((r.get("broker_binding") or {}).get("broker_name")) or "unbound"
            broker_counts[str(broker_name)] += 1

            cand = r.get("candidate_context") or {}
            symbol_counts[str(cand.get("symbol", "UNKNOWN"))] += 1
            strategy_counts[str(strategy_context.get("strategy") or "unknown")] += 1
            if strategy_context.get("strategy_family"):
                strategy_family_counts[str(strategy_context["strategy_family"])] += 1
            if strategy_context.get("underlying_strategy"):
                underlying_strategy_counts[str(strategy_context["underlying_strategy"])] += 1
            if strategy_context.get("learning_adjusted"):
                learning_adjusted_count += 1

            # infer latest manual-review reason from audit history
            mr_reason = self._latest_manual_review_reason(r)
            if mr_reason:
                reason_counts[str(mr_reason)] += 1
            else:
                reason_counts["unknown"] += 1

            detailed_rows.append({
                "intent_id": r.get("intent_id"),
                "package_id": r.get("package_id"),
                "candidate_id": r.get("candidate_id"),
                "client_order_id": r.get("client_order_id"),
                "symbol": cand.get("symbol"),
                "strategy": strategy_context.get("strategy"),
                "strategy_style": strategy_context.get("strategy_style"),
                "strategy_family": strategy_context.get("strategy_family"),
                "underlying_strategy": strategy_context.get("underlying_strategy"),
                "learning_adjusted": strategy_context.get("learning_adjusted"),
                "learning_adjustment_detail": strategy_context.get("learning_adjustment_detail"),
                "broker_name": broker_name,
                "broker_order_id": ((r.get("broker_binding") or {}).get("broker_order_id")),
                "broker_status": ((r.get("broker_state") or {}).get("status")) if r.get("broker_state") else None,
                "manual_review_reason": mr_reason,
                "created_at_utc": r.get("timestamp_utc") or ((r.get("audit") or {}).get("created_at_utc")),
                "updated_at_utc": ((r.get("audit") or {}).get("updated_at_utc")),
                "last_reconciled_at_utc": ((r.get("reconciliation") or {}).get("last_reconciled_at_utc")),
                "age_minutes": age_min,
            })

        detailed_rows_sorted = sorted(
            detailed_rows,
            key=lambda x: (x["age_minutes"] is None, -(x["age_minutes"] or 0)),
        )

        out = {
            "schema_version": "manual_review_queue_report.v1",
            "timestamp_utc": iso_now(),
            "summary": {
                "manual_review_count": len(manual),
                "avg_age_minutes": (sum(age_minutes) / len(age_minutes)) if age_minutes else None,
                "max_age_minutes": max(age_minutes) if age_minutes else None,
                "age_bucket_counts": dict(age_buckets),
                "learning_adjusted_count": learning_adjusted_count,
            },
            "reason_counts": dict(sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)),
            "broker_counts": dict(sorted(broker_counts.items(), key=lambda kv: kv[1], reverse=True)),
            "symbol_counts_top": dict(sorted(symbol_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
            "strategy_counts_top": dict(sorted(strategy_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
            "strategy_family_counts_top": dict(sorted(strategy_family_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
            "underlying_strategy_counts_top": dict(sorted(underlying_strategy_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
            "oldest_unresolved": detailed_rows_sorted[:25],
            "operator_summary": self._operator_summary(
                len(manual),
                age_minutes,
                age_buckets,
                reason_counts,
                strategy_counts,
                strategy_family_counts,
            ),
        }
        return out

    def _latest_by_intent(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        latest = {}
        for r in rows:
            iid = r.get("intent_id")
            if iid:
                latest[iid] = r
        return list(latest.values())

    def _latest_manual_review_reason(self, intent_row: Dict[str, Any]) -> Optional[str]:
        hist = ((intent_row.get("audit") or {}).get("history") or [])
        for ev in reversed(hist):
            if ev.get("event") == "manual_review_required":
                details = ev.get("details") or {}
                return details.get("reason") or details.get("manual_review_reason")
        return None

    def _strategy_context(self, row: Dict[str, Any]) -> Dict[str, Any]:
        candidate_context = row.get("candidate_context") or {}
        metadata = candidate_context.get("metadata") or {}
        strategy_style = candidate_context.get("strategy_style") or metadata.get("strategy_style")
        strategy = (
            candidate_context.get("strategy")
            or metadata.get("strategy")
            or candidate_context.get("template_key")
            or strategy_style
            or "unknown"
        )
        strategy_family = (
            candidate_context.get("strategy_family")
            or metadata.get("strategy_family")
            or infer_strategy_family(
                {
                    "strategy": strategy,
                    "strategy_style": strategy_style,
                    "holding_period": candidate_context.get("holding_period") or metadata.get("holding_period"),
                    "time_window_name": candidate_context.get("time_window_name") or metadata.get("time_window_name"),
                },
                default_family="unknown",
            )
        )
        learning_adjusted = candidate_context.get("learning_adjusted")
        if learning_adjusted is None:
            learning_adjusted = metadata.get("learning_adjusted", False)

        return {
            "strategy": str(strategy),
            "strategy_style": strategy_style,
            "strategy_family": strategy_family,
            "underlying_strategy": (
                candidate_context.get("underlying_strategy")
                or metadata.get("underlying_strategy")
            ),
            "learning_adjusted": bool(learning_adjusted),
            "learning_adjustment_detail": (
                candidate_context.get("learning_adjustment_detail")
                or metadata.get("learning_adjustment_detail")
            ),
        }

    def _age_bucket(self, age_minutes: float) -> str:
        if age_minutes < 15:
            return "<15m"
        if age_minutes < 60:
            return "15m-1h"
        if age_minutes < 240:
            return "1h-4h"
        if age_minutes < 1440:
            return "4h-24h"
        return ">24h"

    def _operator_summary(self, count, age_minutes, age_buckets, reason_counts, strategy_counts, strategy_family_counts) -> str:
        avg_age = (sum(age_minutes) / len(age_minutes)) if age_minutes else None
        top_reason = None
        if reason_counts:
            top_reason = sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        top_strategy = None
        if strategy_counts:
            top_strategy = sorted(strategy_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        top_family = None
        if strategy_family_counts:
            top_family = sorted(strategy_family_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        return (
            f"manual review queue | count={count} | avg_age_min={avg_age} | "
            f">24h={age_buckets.get('>24h', 0)} | top_reason={top_reason} | "
            f"top_strategy={top_strategy} | top_family={top_family}"
        )


def render_markdown(rep: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Manual Review Queue Report")
    lines.append("")
    lines.append(f"- Generated: {rep.get('timestamp_utc')}")
    lines.append(f"- {rep.get('operator_summary')}")
    lines.append("")

    s = rep.get("summary", {})
    lines.append("## Summary")
    for k, v in s.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Top Strategies")
    for k, v in list((rep.get("strategy_counts_top") or {}).items())[:15]:
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Top Strategy Families")
    for k, v in list((rep.get("strategy_family_counts_top") or {}).items())[:15]:
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Top Underlying Strategies")
    for k, v in list((rep.get("underlying_strategy_counts_top") or {}).items())[:15]:
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Top Manual Review Reasons")
    for k, v in list((rep.get("reason_counts") or {}).items())[:15]:
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Oldest Unresolved (Top 10)")
    lines.append("")
    lines.append("| Intent ID | Symbol | Strategy | Family | Underlying | Learned | Broker | Broker Status | Reason | Age (min) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---:|")
    for row in (rep.get("oldest_unresolved") or [])[:10]:
        lines.append(
            f"| {row.get('intent_id')} | {row.get('symbol')} | {row.get('strategy')} | "
            f"{row.get('strategy_family')} | {row.get('underlying_strategy')} | "
            f"{'yes' if row.get('learning_adjusted') else 'no'} | {row.get('broker_name')} | "
            f"{row.get('broker_status')} | {row.get('manual_review_reason')} | {row.get('age_minutes')} |"
        )
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    rep = ManualReviewQueueReport(Path(args.repo_root).resolve()).build()

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
