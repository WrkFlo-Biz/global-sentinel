#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Manual Review Owner Writeback Queue

Purpose:
- Read manual_review_owner_routing.json (output of owner router)
- Append JSONL entries to logs/ops/owner_writeback_queue.jsonl
- Each line: {timestamp, intent_id, suggested_owner, priority, reason, references, writeback_status: "pending"}
- Does NOT mutate order_intent_registry (read-only queue for downstream processing)

CLI:
  python src/reports/manual_review_owner_writeback.py \
    --owner-routing-json reports/analytics/YYYYMMDD/manual_review_owner_routing.json \
    --output-jsonl logs/ops/owner_writeback_queue.jsonl \
    --output-summary-json /tmp/writeback_summary.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional  # noqa: F401


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManualReviewOwnerWriteback:
    def __init__(self, output_jsonl_path: Path):
        self.output_jsonl_path = output_jsonl_path
        self.output_jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def process(self, owner_routing: Dict[str, Any]) -> Dict[str, Any]:
        """
        Read owner routing report and append writeback queue entries.
        Returns a summary of what was queued.
        """
        routed_intents = owner_routing.get("routed_intents") or owner_routing.get("assignments") or []
        queued = []
        skipped = 0

        for entry in routed_intents:
            intent_id = entry.get("intent_id")
            if not intent_id:
                skipped += 1
                continue

            suggested_owner = (
                entry.get("suggested_owner")
                or entry.get("assigned_owner")
                or entry.get("owner")
            )
            if not suggested_owner:
                skipped += 1
                continue

            wb_entry = {
                "timestamp": iso_now(),
                "intent_id": intent_id,
                "suggested_owner": suggested_owner,
                "priority": entry.get("priority") or entry.get("severity") or "normal",
                "reason": entry.get("reason") or entry.get("routing_reason") or "owner_routing_assignment",
                "references": {
                    "package_id": entry.get("package_id"),
                    "symbol": entry.get("symbol"),
                    "client_order_id": entry.get("client_order_id"),
                    "broker_order_id": entry.get("broker_order_id"),
                    "routing_source": "manual_review_owner_router",
                },
                "writeback_status": "pending",
            }

            self._append_jsonl(wb_entry)
            queued.append(wb_entry)

        summary = {
            "schema_version": "owner_writeback_summary.v1",
            "timestamp_utc": iso_now(),
            "input_entry_count": len(routed_intents),
            "queued_count": len(queued),
            "skipped_count": skipped,
            "output_jsonl_path": str(self.output_jsonl_path),
        }
        return summary

    def _append_jsonl(self, record: Dict[str, Any]):
        with self.output_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args():
    p = argparse.ArgumentParser(description="Manual Review Owner Writeback Queue")
    p.add_argument("--owner-routing-json", required=True, help="Path to manual_review_owner_routing.json")
    p.add_argument("--output-jsonl", default="logs/ops/owner_writeback_queue.jsonl",
                    help="Path to writeback queue JSONL output")
    p.add_argument("--output-summary-json", default=None, help="Path to summary JSON output")
    return p.parse_args()


def main():
    args = parse_args()

    routing_path = Path(args.owner_routing_json)
    if not routing_path.exists():
        # Produce empty summary if input doesn't exist
        summary = {
            "schema_version": "owner_writeback_summary.v1",
            "timestamp_utc": iso_now(),
            "input_entry_count": 0,
            "queued_count": 0,
            "skipped_count": 0,
            "output_jsonl_path": args.output_jsonl,
            "note": f"Input file not found: {args.owner_routing_json}",
        }
        if args.output_summary_json:
            p = Path(args.output_summary_json)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        else:
            print(json.dumps(summary, indent=2))
        return

    owner_routing = json.loads(routing_path.read_text(encoding="utf-8"))
    writeback = ManualReviewOwnerWriteback(Path(args.output_jsonl))
    summary = writeback.process(owner_routing)

    if args.output_summary_json:
        p = Path(args.output_summary_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    else:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
