#!/usr/bin/env python3
"""
Build CI / Replay Dashboard Summary (Markdown)

Aggregates available artifacts into a single markdown file.
Safe to run even if some files are missing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional


def try_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-md", default="reports/ci/ci_dashboard_summary.md")
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_path = Path(args.output_md)
    if not out_path.is_absolute():
        out_path = repo_root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    weekly = try_load_json(repo_root / "reports" / "weekly" / "scorecard_latest.json")
    exec_rel = try_load_json(repo_root / "reports" / "weekly" / "execution_reliability_metrics.json")
    mr = try_load_json(repo_root / "reports" / "weekly" / "manual_review_queue_report.json")
    stale = try_load_json(repo_root / "reports" / "weekly" / "stale_intent_sweeper_report.json")
    lag_sla = try_load_json(repo_root / "reports" / "weekly" / "reconciler_lag_sla_monitor.json")

    smoke_root = repo_root / "tests" / "replays" / "execution_reliability_smoke" / "out"
    smoke_summaries = []
    if smoke_root.exists():
        for scen_dir in sorted(smoke_root.iterdir()):
            if not scen_dir.is_dir():
                continue
            s = try_load_json(scen_dir / "smoke_summary.json")
            if s:
                smoke_summaries.append(s)

    lines = []
    lines.append("# CI / Replay Dashboard Summary")
    lines.append("")

    if weekly:
        lines.append("## Weekly Scorecard")
        lines.append(f"- {weekly.get('operator_summary')}")
        lines.append("")

    lines.append("## Execution Smoke Matrix (Latest Local Artifacts)")
    lines.append("")
    lines.append("| Scenario | Package ID | Status | Bound Attempts | Broker Rejects | Router Errors | Reconciled | Reconciler Errors | Stale Intents |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
    if smoke_summaries:
        for s in smoke_summaries:
            bound_attempt_count = s.get("bound_order_attempt_count")
            lines.append(
                f"| {s.get('scenario')} | {s.get('scenario_package_id')} | {s.get('status')} | "
                f"{bound_attempt_count} | {s.get('broker_rejected_count')} | "
                f"{s.get('router_errors')} | {s.get('reconciled_count')} | {s.get('reconciler_errors')} | {s.get('stale_intent_count')} |"
            )
    else:
        lines.append("| (none) | - | - | - | - | - | - | - | - |")
    lines.append("")

    if exec_rel:
        k = exec_rel.get("kpis") or {}
        lines.append("## Execution Reliability Snapshot")
        for metric in [
            "submit_success_rate_proxy", "rejected_rate", "manual_review_rate",
            "stale_open_like_rate", "avg_reconciliation_lag_minutes", "max_reconciliation_lag_minutes"
        ]:
            lines.append(f"- {metric}: {k.get(metric)}")
        lines.append("")

    if lag_sla:
        ls = lag_sla.get("summary") or {}
        lines.append("## Reconciler Lag SLA")
        for metric in ["severity", "avg_lag_minutes", "max_lag_minutes", "avg_lag_breach", "max_lag_breach", "per_intent_warn_count", "per_intent_critical_count"]:
            lines.append(f"- {metric}: {ls.get(metric)}")
        recs = lag_sla.get("recommendations") or []
        if recs:
            lines.append("- recommendations:")
            for r in recs[:6]:
                lines.append(f"  - {r}")
        lines.append("")

    if mr:
        ms = mr.get("summary") or {}
        lines.append("## Manual Review Queue")
        for metric in ["manual_review_count", "avg_age_minutes", "max_age_minutes"]:
            lines.append(f"- {metric}: {ms.get(metric)}")
        lines.append(f"- age_bucket_counts: {ms.get('age_bucket_counts')}")
        lines.append("")

    if stale:
        ss = stale.get("summary") or {}
        lines.append("## Stale Intent Sweeper")
        for metric in ["stale_intent_count", "stale_rate", "shadow_cancel_recommendation_count"]:
            lines.append(f"- {metric}: {ss.get(metric)}")
        lines.append(f"- time_window_bucket_counts: {ss.get('time_window_bucket_counts')}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
