#!/usr/bin/env python3
"""
Global Sentinel V5.0 - Stale Intent Sweeper

Purpose:
- Identify stale open/shadow intents from OrderIntentRegistry
- Produce actionable report for ops (COO/CFO/CAIO)
- Optionally mark stale intents as manual_review
- Optionally emit shadow cancel recommendations (no direct broker cancels)
- Time-window-aware TTL policy support (per-intent TTL resolution)

Default target statuses:
  submitted, acknowledged, open, partially_filled
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


class StaleIntentSweeper:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        from src.execution.order_intent_registry import OrderIntentRegistry
        self.registry = OrderIntentRegistry(repo_root)
        self.ttl_policy_engine = None

    def _intent_matches_filters(
        self,
        intent_row: Dict[str, Any],
        router_run_id_filter: Optional[str] = None,
        intent_id_prefix_filter: Optional[str] = None,
    ) -> bool:
        if router_run_id_filter:
            extra = intent_row.get("extra_context") or {}
            rrid = extra.get("router_run_id")
            if rrid is None:
                rrid = ((intent_row.get("audit") or {}).get("router_run_id"))
            if str(rrid) != str(router_run_id_filter):
                return False
        if intent_id_prefix_filter:
            iid = str(intent_row.get("intent_id") or "")
            if not iid.startswith(str(intent_id_prefix_filter)):
                return False
        return True

    def _load_ttl_policy_engine(self, ttl_policy_yaml: Optional[Path]):
        if ttl_policy_yaml is None:
            return None
        if not ttl_policy_yaml.exists():
            return None
        from src.execution.time_window_ttl_policy import TimeWindowTTLPolicyEngine
        return TimeWindowTTLPolicyEngine.from_yaml_file(ttl_policy_yaml)

    def _effective_ttl_for_intent(
        self,
        intent_row: Dict[str, Any],
        default_ttl_minutes: float,
        use_time_window_ttl_policy: bool,
    ):
        # If default_ttl_minutes is 0 or negative, use it as-is (CI override mode)
        if default_ttl_minutes <= 0:
            return float(default_ttl_minutes), {
                "resolved_ttl_minutes": float(default_ttl_minutes),
                "time_window_name": None,
                "reasons": [{"layer": "cli_override_zero_ttl", "ttl_minutes": float(default_ttl_minutes)}],
            }

        # Prefer intent-stored TTL policy (resolved at creation time)
        stored_policy = intent_row.get("order_lifecycle_policy") or {}
        if stored_policy.get("resolved_ttl_minutes") is not None:
            ttl = float(stored_policy["resolved_ttl_minutes"])
            expl = stored_policy.get("ttl_explanation") or {
                "resolved_ttl_minutes": ttl,
                "reasons": [{"layer": "intent_stored_policy", "ttl_minutes": ttl}],
            }
            return ttl, expl

        # Fall back to policy engine recomputation
        if use_time_window_ttl_policy and self.ttl_policy_engine is not None:
            ttl, explanation = self.ttl_policy_engine.resolve_ttl_minutes(intent_row)
            return float(ttl), explanation
        return float(default_ttl_minutes), {
            "resolved_ttl_minutes": float(default_ttl_minutes),
            "time_window_name": None,
            "strategy_style": ((intent_row.get("candidate_context") or {}).get("strategy_style")),
            "symbol": ((intent_row.get("candidate_context") or {}).get("symbol")),
            "reasons": [{"layer": "static_cli_default", "ttl_minutes": float(default_ttl_minutes)}],
        }

    def sweep(
        self,
        stale_after_minutes: float = 30.0,
        target_statuses: Optional[List[str]] = None,
        mark_manual_review: bool = False,
        emit_shadow_cancel_recommendations: bool = True,
        max_rows: int = 200,
        use_time_window_ttl_policy: bool = False,
        ttl_policy_yaml: Optional[Path] = None,
        package_ids: Optional[List[str]] = None,
        router_run_id_filter: Optional[str] = None,
        intent_id_prefix_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_statuses = target_statuses or ["submitted", "acknowledged", "open", "partially_filled"]
        rows = self.registry.list_intents(statuses=target_statuses, package_ids=package_ids)

        # Apply additional filters (router_run_id, intent_id_prefix)
        if router_run_id_filter or intent_id_prefix_filter:
            rows = [
                r for r in rows
                if self._intent_matches_filters(
                    r,
                    router_run_id_filter=router_run_id_filter,
                    intent_id_prefix_filter=intent_id_prefix_filter,
                )
            ]

        # Initialize TTL policy engine if requested
        self.ttl_policy_engine = self._load_ttl_policy_engine(ttl_policy_yaml)

        now = datetime.now(timezone.utc)
        stale_rows = []
        cancel_recos = []

        for r in rows:
            created = parse_ts(r.get("timestamp_utc")) or parse_ts(((r.get("audit") or {}).get("created_at_utc")))
            updated = parse_ts(((r.get("audit") or {}).get("updated_at_utc")))

            ref_ts = updated or created
            if ref_ts is None:
                age_min = None
            else:
                age_min = (now - ref_ts).total_seconds() / 60.0

            # Per-intent TTL resolution
            effective_ttl_minutes, ttl_expl = self._effective_ttl_for_intent(
                r,
                default_ttl_minutes=stale_after_minutes,
                use_time_window_ttl_policy=use_time_window_ttl_policy,
            )

            if age_min is None or age_min < effective_ttl_minutes:
                continue

            cand = r.get("candidate_context") or {}
            broker_binding = r.get("broker_binding") or {}
            broker_state = r.get("broker_state") or {}

            item = {
                "intent_id": r.get("intent_id"),
                "package_id": r.get("package_id"),
                "candidate_id": r.get("candidate_id"),
                "client_order_id": r.get("client_order_id"),
                "status": r.get("status"),
                "shadow_mode": r.get("shadow_mode"),
                "symbol": cand.get("symbol"),
                "strategy_style": cand.get("strategy_style"),
                "broker_name": broker_binding.get("broker_name"),
                "broker_order_id": broker_binding.get("broker_order_id"),
                "broker_status": broker_state.get("status"),
                "created_at_utc": r.get("timestamp_utc") or ((r.get("audit") or {}).get("created_at_utc")),
                "updated_at_utc": ((r.get("audit") or {}).get("updated_at_utc")),
                "last_reconciled_at_utc": ((r.get("reconciliation") or {}).get("last_reconciled_at_utc")),
                "age_minutes": age_min,
                "resolved_ttl_minutes": effective_ttl_minutes,
                "ttl_explanation": ttl_expl,
            }
            stale_rows.append(item)

            if emit_shadow_cancel_recommendations:
                cancel_recos.append({
                    "intent_id": r.get("intent_id"),
                    "client_order_id": r.get("client_order_id"),
                    "broker_order_id": broker_binding.get("broker_order_id"),
                    "symbol": cand.get("symbol"),
                    "recommendation": "shadow_cancel_review",
                    "reason": f"stale_open_intent>{effective_ttl_minutes}m",
                    "age_minutes": age_min,
                    "resolved_ttl_minutes": effective_ttl_minutes,
                    "time_window_name": ttl_expl.get("time_window_name"),
                })

            if mark_manual_review:
                try:
                    self.registry.mark_manual_review(
                        r["intent_id"],
                        reason="stale_open_intent",
                        details={
                            "stale_after_minutes": stale_after_minutes,
                            "observed_age_minutes": age_min,
                            "broker_status": broker_state.get("status"),
                            "resolved_ttl_minutes": effective_ttl_minutes,
                            "ttl_explanation": ttl_expl,
                        },
                    )
                except Exception:
                    pass

        stale_rows_sorted = sorted(stale_rows, key=lambda x: -(x["age_minutes"] or 0))[:max_rows]

        # Time window bucket counts
        ttl_windows = {}
        for row in stale_rows:
            tw = ((row.get("ttl_explanation") or {}).get("time_window_name")) or "unknown"
            ttl_windows[tw] = ttl_windows.get(tw, 0) + 1

        out = {
            "schema_version": "stale_intent_sweeper_report.v1",
            "timestamp_utc": iso_now(),
            "config": {
                "stale_after_minutes": stale_after_minutes,
                "target_statuses": target_statuses,
                "mark_manual_review": mark_manual_review,
                "emit_shadow_cancel_recommendations": emit_shadow_cancel_recommendations,
                "use_time_window_ttl_policy": use_time_window_ttl_policy,
                "ttl_policy_yaml": str(ttl_policy_yaml) if ttl_policy_yaml else None,
                "package_ids": package_ids,
                "router_run_id_filter": router_run_id_filter,
                "intent_id_prefix_filter": intent_id_prefix_filter,
            },
            "summary": {
                "target_intent_count": len(rows),
                "stale_intent_count": len(stale_rows),
                "stale_rate": (len(stale_rows) / len(rows)) if rows else None,
                "shadow_cancel_recommendation_count": len(cancel_recos),
                "time_window_bucket_counts": ttl_windows,
            },
            "stale_intents": stale_rows_sorted,
            "shadow_cancel_recommendations": sorted(cancel_recos, key=lambda x: -(x["age_minutes"] or 0))[:max_rows],
            "operator_summary": (
                f"stale sweeper | target={len(rows)} stale={len(stale_rows)} "
                f"rate={(len(stale_rows)/len(rows)) if rows else None} threshold_min={stale_after_minutes} "
                f"ttl_policy={'on' if use_time_window_ttl_policy else 'off'}"
                f" intent_prefix_filter={intent_id_prefix_filter}"
            ),
        }
        return out


def render_markdown(rep: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Stale Intent Sweeper Report")
    lines.append("")
    lines.append(f"- Generated: {rep.get('timestamp_utc')}")
    lines.append(f"- {rep.get('operator_summary')}")
    lines.append("")
    lines.append("## Summary")
    for k, v in (rep.get("summary") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Top Stale Intents")
    lines.append("")
    lines.append("| Intent ID | Symbol | Status | Time Window | TTL (min) | Age (min) |")
    lines.append("|---|---|---|---|---:|---:|")
    for row in (rep.get("stale_intents") or [])[:15]:
        tw = ((row.get("ttl_explanation") or {}).get("time_window_name")) or ""
        lines.append(
            f"| {row.get('intent_id')} | {row.get('symbol')} | {row.get('status')} | "
            f"{tw} | {round(row.get('resolved_ttl_minutes') or 0, 2)} | {round(row.get('age_minutes') or 0, 2)} |"
        )
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--stale-after-minutes", type=float, default=30.0)
    p.add_argument("--mark-manual-review", action="store_true")
    p.add_argument("--no-shadow-cancel-recos", action="store_true")
    p.add_argument("--use-time-window-ttl-policy", action="store_true")
    p.add_argument("--ttl-policy-yaml", default="config/order_ttl_policy.yaml")
    p.add_argument("--package-id-filter", nargs="*", default=None, help="Filter intents by package ID(s)")
    p.add_argument("--router-run-id-filter", default=None, help="Filter intents by router run ID")
    p.add_argument("--intent-id-prefix-filter", default=None, help="Filter intents by intent_id prefix (ad hoc drills)")
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    import sys
    sys.path.insert(0, str(repo_root))

    ttl_policy_path = Path(args.ttl_policy_yaml).resolve() if args.ttl_policy_yaml else None

    rep = StaleIntentSweeper(repo_root).sweep(
        stale_after_minutes=args.stale_after_minutes,
        mark_manual_review=args.mark_manual_review,
        emit_shadow_cancel_recommendations=(not args.no_shadow_cancel_recos),
        use_time_window_ttl_policy=args.use_time_window_ttl_policy,
        ttl_policy_yaml=ttl_policy_path,
        package_ids=args.package_id_filter,
        router_run_id_filter=args.router_run_id_filter,
        intent_id_prefix_filter=args.intent_id_prefix_filter,
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
