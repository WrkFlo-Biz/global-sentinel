#!/usr/bin/env python3
"""Generate a compact daily summary of policy engine evaluations."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def build_policy_audit_report(entries: Iterable[Dict[str, Any]], near_miss_ratio: float = 0.10) -> Dict[str, Any]:
    entries = list(entries)
    allowed_counts = Counter()
    blocked_counts = Counter()
    near_misses: List[Dict[str, Any]] = []

    for entry in entries:
        eval_type = str(entry.get("eval_type") or entry.get("message") or "unknown")
        decision = entry.get("decision") or entry
        allowed = bool(decision.get("allowed"))
        if allowed:
            allowed_counts[eval_type] += 1
        else:
            blocked_counts[eval_type] += 1

        trace = decision.get("trace") or {}
        if "notional" in trace and "portfolio_equity" in trace:
            equity = float(trace.get("portfolio_equity") or 0.0)
            if equity > 0:
                limit = 0.12 * equity
                notional = float(trace.get("notional") or 0.0)
                if allowed and limit > 0 and notional >= limit * (1.0 - near_miss_ratio):
                    near_misses.append({"eval_type": eval_type, "trace_id": decision.get("trace_id"), "kind": "notional_limit", "notional": notional, "limit": limit})

        for check in decision.get("checks_failed", []):
            if "quantum_influence" in str(check) and allowed:
                near_misses.append({"eval_type": eval_type, "trace_id": decision.get("trace_id"), "kind": "quantum_influence", "check": check})

    return {
        "schema_version": "policy_audit_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_count": len(entries),
        "allowed_by_category": dict(allowed_counts),
        "blocked_by_category": dict(blocked_counts),
        "near_miss_count": len(near_misses),
        "near_misses": near_misses[:100],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a policy audit report from JSONL evaluations")
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--near-miss-ratio", type=float, default=0.10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    entries = _load_jsonl(Path(args.input_jsonl))
    report = build_policy_audit_report(entries, near_miss_ratio=args.near_miss_ratio)
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
