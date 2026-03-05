#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Reconciler Lag SLA Monitor

Purpose:
- Monitor reconciliation lag across latest order intents
- Detect SLA breaches (avg/max/per-intent lag)
- Provide escalation recommendations (COO/CAIO/CFO)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


class ReconcilerLagSLAMonitor:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def _latest_intents(self) -> List[Dict[str, Any]]:
        rows = read_jsonl(self.repo_root / "logs" / "execution" / "order_intents.jsonl")
        latest = {}
        for r in rows:
            iid = r.get("intent_id")
            if iid:
                latest[iid] = r
        return list(latest.values())

    def evaluate(
        self,
        target_statuses: Optional[List[str]] = None,
        avg_lag_sla_minutes: float = 2.0,
        max_lag_sla_minutes: float = 10.0,
        per_intent_lag_warn_minutes: float = 5.0,
    ) -> Dict[str, Any]:
        target_statuses = target_statuses or ["submitted", "acknowledged", "open", "partially_filled", "manual_review"]
        intents = [r for r in self._latest_intents() if str(r.get("status")) in set(target_statuses)]

        now = datetime.now(timezone.utc)

        lag_rows = []
        lags = []
        breaches_warn = []
        breaches_critical = []

        for r in intents:
            recon = r.get("reconciliation") or {}
            last_recon_ts = parse_ts(recon.get("last_reconciled_at_utc"))
            created_ts = parse_ts(r.get("timestamp_utc")) or parse_ts(((r.get("audit") or {}).get("created_at_utc")))
            updated_ts = parse_ts(((r.get("audit") or {}).get("updated_at_utc")))

            ref_ts = last_recon_ts or updated_ts or created_ts
            lag_minutes = None
            if ref_ts:
                lag_minutes = (now - ref_ts).total_seconds() / 60.0
                lags.append(lag_minutes)

            row = {
                "intent_id": r.get("intent_id"),
                "status": r.get("status"),
                "symbol": ((r.get("candidate_context") or {}).get("symbol")),
                "strategy_style": ((r.get("candidate_context") or {}).get("strategy_style")),
                "broker_name": ((r.get("broker_binding") or {}).get("broker_name")),
                "broker_order_id": ((r.get("broker_binding") or {}).get("broker_order_id")),
                "broker_status": ((r.get("broker_state") or {}).get("status")) if r.get("broker_state") else None,
                "last_reconciled_at_utc": recon.get("last_reconciled_at_utc"),
                "lag_minutes": lag_minutes,
                "reconciler_status": recon.get("reconciler_status"),
            }
            lag_rows.append(row)

            if lag_minutes is not None and lag_minutes >= per_intent_lag_warn_minutes:
                breaches_warn.append(row)
            if lag_minutes is not None and lag_minutes >= max_lag_sla_minutes:
                breaches_critical.append(row)

        avg_lag = (sum(lags) / len(lags)) if lags else None
        max_lag = max(lags) if lags else None

        sla_breaches = {
            "avg_lag_breach": (avg_lag is not None and avg_lag > avg_lag_sla_minutes),
            "max_lag_breach": (max_lag is not None and max_lag > max_lag_sla_minutes),
            "per_intent_warn_count": len(breaches_warn),
            "per_intent_critical_count": len(breaches_critical),
        }

        severity = "ok"
        if sla_breaches["max_lag_breach"] or len(breaches_critical) > 0:
            severity = "critical"
        elif sla_breaches["avg_lag_breach"] or len(breaches_warn) > 0:
            severity = "warning"

        recommendations = []
        if severity == "warning":
            recommendations.append("Increase reconciler polling cadence or reduce adapter request batch size.")
        if severity == "critical":
            recommendations.append("Escalate to COO immediately: reconciliation lag SLA breach. Pause new shadow routing in affected windows until lag normalizes.")
            recommendations.append("Review adapter health, broker API latency/rate-limit behavior, and reconciler exception logs.")
        if len(breaches_critical) > 0:
            recommendations.append("Prioritize manual review of intents with lag above critical threshold and open/partially_filled broker states.")

        out = {
            "schema_version": "reconciler_lag_sla_monitor.v1",
            "timestamp_utc": iso_now(),
            "config": {
                "target_statuses": target_statuses,
                "avg_lag_sla_minutes": avg_lag_sla_minutes,
                "max_lag_sla_minutes": max_lag_sla_minutes,
                "per_intent_lag_warn_minutes": per_intent_lag_warn_minutes,
            },
            "summary": {
                "intent_count": len(intents),
                "avg_lag_minutes": avg_lag,
                "max_lag_minutes": max_lag,
                "severity": severity,
                **sla_breaches,
            },
            "top_lagging_intents": sorted(
                [r for r in lag_rows if r.get("lag_minutes") is not None],
                key=lambda x: -(x["lag_minutes"] or 0)
            )[:25],
            "recommendations": recommendations,
            "operator_summary": (
                f"reconciler lag SLA | severity={severity} avg={avg_lag} max={max_lag} "
                f"warn={len(breaches_warn)} critical={len(breaches_critical)}"
            ),
        }
        return out


def render_markdown(rep: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Reconciler Lag SLA Monitor")
    lines.append("")
    lines.append(f"- Generated: {rep.get('timestamp_utc')}")
    lines.append(f"- {rep.get('operator_summary')}")
    lines.append("")
    lines.append("## Summary")
    for k, v in (rep.get("summary") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Recommendations")
    for r in (rep.get("recommendations") or []):
        lines.append(f"- {r}")
    if not rep.get("recommendations"):
        lines.append("- None")
    lines.append("")
    lines.append("## Top Lagging Intents")
    lines.append("")
    lines.append("| Intent ID | Symbol | Status | Broker | Broker Status | Lag (min) |")
    lines.append("|---|---|---|---|---|---:|")
    for row in (rep.get("top_lagging_intents") or [])[:15]:
        lines.append(
            f"| {row.get('intent_id')} | {row.get('symbol')} | {row.get('status')} | "
            f"{row.get('broker_name')} | {row.get('broker_status')} | {row.get('lag_minutes')} |"
        )
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--avg-lag-sla-minutes", type=float, default=2.0)
    p.add_argument("--max-lag-sla-minutes", type=float, default=10.0)
    p.add_argument("--per-intent-lag-warn-minutes", type=float, default=5.0)
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    rep = ReconcilerLagSLAMonitor(Path(args.repo_root).resolve()).evaluate(
        avg_lag_sla_minutes=args.avg_lag_sla_minutes,
        max_lag_sla_minutes=args.max_lag_sla_minutes,
        per_intent_lag_warn_minutes=args.per_intent_lag_warn_minutes,
    )

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
