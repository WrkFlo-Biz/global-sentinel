#!/usr/bin/env python3
"""
Global Sentinel V4.8 - Execution Reliability Metrics

Purpose:
- Measure execution-path reliability independent of alpha quality
- Aggregate:
  - order intent registry snapshots/latest states
  - shadow order router logs
  - broker reconciler loop logs
  - paper trade reconciliation outputs (optional)
- Produce JSON + Markdown summaries for daily/weekly ops review
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def mean(xs: List[float]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


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


class ExecutionReliabilityMetrics:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def build(
        self,
        recon_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        intents_latest = self._load_latest_intents()
        intent_snapshots = read_jsonl(self.repo_root / "logs" / "execution" / "order_intent_snapshots.jsonl")
        router_logs = read_jsonl(self.repo_root / "logs" / "execution" / "shadow_order_router.jsonl")
        reconciler_logs = read_jsonl(self.repo_root / "logs" / "execution" / "broker_reconciler_loop.jsonl")

        kpis = self._compute_kpis(
            intents_latest=intents_latest,
            intent_snapshots=intent_snapshots,
            router_logs=router_logs,
            reconciler_logs=reconciler_logs,
            recon_json=recon_json,
        )

        per_window_kpis = self._compute_per_window_kpis(intents_latest)

        out = {
            "schema_version": "execution_reliability_metrics.v1",
            "timestamp_utc": iso_now(),
            "coverage": {
                "intent_latest_count": len(intents_latest),
                "intent_snapshot_count": len(intent_snapshots),
                "router_log_count": len(router_logs),
                "reconciler_log_count": len(reconciler_logs),
                "paper_reconciliation_attached": recon_json is not None,
            },
            "kpis": kpis,
            "per_window_kpis": per_window_kpis,
            "operator_summary": self._operator_summary(kpis),
        }
        return out

    def _load_latest_intents(self) -> List[Dict[str, Any]]:
        path = self.repo_root / "logs" / "execution" / "order_intents.jsonl"
        rows = read_jsonl(path)
        latest = {}
        for r in rows:
            iid = r.get("intent_id")
            if iid:
                latest[iid] = r
        return list(latest.values())

    def _compute_kpis(
        self,
        intents_latest: List[Dict[str, Any]],
        intent_snapshots: List[Dict[str, Any]],
        router_logs: List[Dict[str, Any]],
        reconciler_logs: List[Dict[str, Any]],
        recon_json: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Intent status distribution
        status_counts = defaultdict(int)
        shadow_true_count = 0
        manual_review_count = 0
        open_like_count = 0
        stale_open_like_count = 0
        stale_threshold_minutes = 30

        reconciliation_lag_mins = []
        by_broker_counts = defaultdict(int)

        now = datetime.now(timezone.utc)

        for it in intents_latest:
            status = str(it.get("status", "unknown"))
            status_counts[status] += 1
            if it.get("shadow_mode") is True:
                shadow_true_count += 1
            if status == "manual_review":
                manual_review_count += 1
            if status in {"submitted", "acknowledged", "open", "partially_filled"}:
                open_like_count += 1

                created_ts = parse_ts(it.get("timestamp_utc")) or parse_ts(((it.get("audit") or {}).get("created_at_utc")))
                if created_ts:
                    age_mins = (now - created_ts).total_seconds() / 60.0
                    if age_mins > stale_threshold_minutes:
                        stale_open_like_count += 1

            broker_name = ((it.get("broker_binding") or {}).get("broker_name"))
            if broker_name:
                by_broker_counts[str(broker_name)] += 1

            recon = it.get("reconciliation") or {}
            last_rec = parse_ts(recon.get("last_reconciled_at_utc"))
            if last_rec:
                lag = (now - last_rec).total_seconds() / 60.0
                reconciliation_lag_mins.append(lag)

        # Router logs
        router_runs = [r for r in router_logs if r.get("event_type") == "route_package_complete"]
        router_errors = []
        bound_attempt_counts = []
        skipped_candidate_counts = []

        for r in router_runs:
            payload = r.get("payload") or {}
            bound_attempt_counts.append(len(payload.get("bound_order_attempts", []) or []))
            skipped_candidate_counts.append(len(payload.get("skipped_candidates", []) or []))
            router_errors.extend(payload.get("errors", []) or [])

        router_error_count = len(router_errors)

        # Reconciler logs
        reconciler_runs = [r for r in reconciler_logs if r.get("event_type") == "run_once_summary"]
        reconciled_counts = []
        reconciler_error_counts = []

        for r in reconciler_runs:
            payload = r.get("payload") or {}
            reconciled_counts.append(safe_float(payload.get("reconciled_count"), 0))
            reconciler_error_counts.append(len(payload.get("errors", []) or []))

        # Snapshot lifecycle transitions
        transition_counts = defaultdict(int)
        last_status_by_intent = {}
        for snap in intent_snapshots:
            iid = snap.get("intent_id")
            if not iid:
                continue
            cur = str(snap.get("status", "unknown"))
            prev = last_status_by_intent.get(iid)
            if prev is not None and prev != cur:
                transition_counts[f"{prev}->{cur}"] += 1
            last_status_by_intent[iid] = cur

        # Optional paper reconciler match-confidence stats
        match_conf_counts = defaultdict(int)
        if recon_json:
            for row in recon_json.get("comparisons", []) or []:
                mc = str(row.get("match_confidence", "unknown"))
                match_conf_counts[mc] += 1

        submit_success_count = status_counts.get("open", 0) + status_counts.get("acknowledged", 0) + status_counts.get("submitted", 0) + status_counts.get("partially_filled", 0) + status_counts.get("filled", 0)
        rejected_count = status_counts.get("rejected", 0)
        total_intents = len(intents_latest)

        return {
            "intent_status_counts": dict(status_counts),
            "intent_broker_counts": dict(by_broker_counts),

            "shadow_mode_true_count": shadow_true_count,
            "manual_review_count": manual_review_count,
            "manual_review_rate": (manual_review_count / total_intents) if total_intents else None,

            "open_like_count": open_like_count,
            "stale_open_like_count": stale_open_like_count,
            "stale_open_like_rate": (stale_open_like_count / open_like_count) if open_like_count else None,
            "stale_open_threshold_minutes": stale_threshold_minutes,

            "submit_success_count_proxy": submit_success_count,
            "submit_success_rate_proxy": (submit_success_count / total_intents) if total_intents else None,
            "rejected_count": rejected_count,
            "rejected_rate": (rejected_count / total_intents) if total_intents else None,

            "avg_reconciliation_lag_minutes": mean(reconciliation_lag_mins),
            "max_reconciliation_lag_minutes": max(reconciliation_lag_mins) if reconciliation_lag_mins else None,

            "router_run_count": len(router_runs),
            "router_error_count": router_error_count,
            "avg_bound_order_attempts_per_router_run": mean(bound_attempt_counts),
            "avg_skipped_candidates_per_router_run": mean(skipped_candidate_counts),

            "reconciler_run_count": len(reconciler_runs),
            "avg_reconciled_count_per_run": mean(reconciled_counts),
            "avg_reconciler_error_count_per_run": mean(reconciler_error_counts),

            "transition_counts": dict(sorted(transition_counts.items(), key=lambda kv: kv[1], reverse=True)),
            "paper_reconciliation_match_confidence_counts": dict(match_conf_counts),
        }

    def _compute_per_window_kpis(self, intents_latest: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for it in intents_latest:
            tw = (
                ((it.get("package_context") or {}).get("time_window_name"))
                or ((it.get("order_lifecycle_policy") or {}).get("created_with_time_window_hint"))
                or "unknown"
            )
            buckets[tw].append(it)

        result = {}
        for tw_name, items in sorted(buckets.items()):
            total = len(items)
            rejected = sum(1 for i in items if i.get("status") == "rejected")
            open_like = [i for i in items if i.get("status") in {"submitted", "acknowledged", "open", "partially_filled"}]
            stale_count = 0
            for i in open_like:
                created_ts = parse_ts(i.get("timestamp_utc")) or parse_ts(((i.get("audit") or {}).get("created_at_utc")))
                if created_ts and (now - created_ts).total_seconds() / 60.0 > 30:
                    stale_count += 1

            recon_lags = []
            for i in items:
                recon = i.get("reconciliation") or {}
                last_rec = parse_ts(recon.get("last_reconciled_at_utc"))
                if last_rec:
                    recon_lags.append((now - last_rec).total_seconds() / 60.0)

            partial_fill_count = sum(1 for i in items if i.get("status") == "partially_filled")

            result[tw_name] = {
                "intent_count": total,
                "rejected_rate": (rejected / total) if total else None,
                "stale_open_like_rate": (stale_count / len(open_like)) if open_like else None,
                "avg_reconciliation_lag_minutes": mean(recon_lags),
                "partial_fill_count": partial_fill_count,
            }
        return result

    def _operator_summary(self, kpis: Dict[str, Any]) -> str:
        return (
            f"execution reliability | intents={sum((kpis.get('intent_status_counts') or {}).values())} | "
            f"rejected_rate={kpis.get('rejected_rate')} | "
            f"manual_review_rate={kpis.get('manual_review_rate')} | "
            f"stale_open_like_rate={kpis.get('stale_open_like_rate')} | "
            f"avg_reconciliation_lag_min={kpis.get('avg_reconciliation_lag_minutes')}"
        )


def render_markdown(rep: Dict[str, Any]) -> str:
    k = rep.get("kpis", {})
    lines = []
    lines.append("# Execution Reliability Metrics")
    lines.append("")
    lines.append(f"- Generated: {rep.get('timestamp_utc')}")
    lines.append(f"- {rep.get('operator_summary')}")
    lines.append("")
    lines.append("## Coverage")
    for ck, cv in (rep.get("coverage") or {}).items():
        lines.append(f"- {ck}: {cv}")
    lines.append("")
    lines.append("## Key KPIs")
    keys = [
        "submit_success_rate_proxy",
        "rejected_rate",
        "manual_review_rate",
        "stale_open_like_rate",
        "avg_reconciliation_lag_minutes",
        "router_error_count",
        "avg_reconciler_error_count_per_run",
    ]
    for key in keys:
        lines.append(f"- {key}: {k.get(key)}")
    lines.append("")
    lines.append("## Intent Status Counts")
    for sk, sv in (k.get("intent_status_counts") or {}).items():
        lines.append(f"- {sk}: {sv}")
    lines.append("")
    lines.append("## Transition Counts")
    for tk, tv in list((k.get("transition_counts") or {}).items())[:15]:
        lines.append(f"- {tk}: {tv}")
    lines.append("")
    per_window = rep.get("per_window_kpis") or {}
    if per_window:
        lines.append("## Per-Window KPIs")
        lines.append("")
        lines.append("| Time Window | Intents | Rejected Rate | Stale Rate | Avg Recon Lag (min) | Partial Fills |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for tw_name, tw_kpis in per_window.items():
            rr = tw_kpis.get("rejected_rate")
            sr = tw_kpis.get("stale_open_like_rate")
            rl = tw_kpis.get("avg_reconciliation_lag_minutes")
            lines.append(
                f"| {tw_name} | {tw_kpis.get('intent_count', 0)} | "
                f"{round(rr, 4) if rr is not None else 'N/A'} | "
                f"{round(sr, 4) if sr is not None else 'N/A'} | "
                f"{round(rl, 2) if rl is not None else 'N/A'} | "
                f"{tw_kpis.get('partial_fill_count', 0)} |"
            )
        lines.append("")

    if k.get("paper_reconciliation_match_confidence_counts"):
        lines.append("## Paper Reconciliation Match Confidence")
        for mk, mv in (k.get("paper_reconciliation_match_confidence_counts") or {}).items():
            lines.append(f"- {mk}: {mv}")
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--paper-recon-json", default=None)
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    recon_json = None
    if args.paper_recon_json:
        recon_json = json.loads(Path(args.paper_recon_json).read_text(encoding="utf-8"))

    rep = ExecutionReliabilityMetrics(Path(args.repo_root).resolve()).build(recon_json=recon_json)

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
