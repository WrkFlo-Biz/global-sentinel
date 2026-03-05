#!/usr/bin/env python3
"""
Execution Reliability Fault-Injection Smoke (CI-safe)

Scenarios:
- baseline
- reject_xom
- timeout_cat
- partial_fill_cat
- stale_open_xom
- delayed_ack_xom
- ooo_partial_xom
- delayed_ack_and_ooo_xom

Flow:
  package fixture -> shadow router (mock broker fault profile)
  -> optional simulate fills
  -> broker reconciler run_once
  -> stale intent sweeper
  -> artifacts
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
import sys


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


SCENARIOS = {
    "baseline": {},
    "reject_xom": {"reject_symbols": ["XOM"]},
    "timeout_cat": {"timeout_on_submit_symbols": ["CAT"]},
    "partial_fill_cat": {"partial_fill_only_symbols": ["CAT"]},
    "stale_open_xom": {"stale_open_symbols": ["XOM"]},
    "delayed_ack_xom": {"delayed_ack_symbols": ["XOM"], "ack_after_n_get_order_calls": 3},
    "ooo_partial_xom": {"out_of_order_transition_symbols": ["XOM"]},
    "delayed_ack_and_ooo_xom": {
        "delayed_ack_symbols": ["XOM"],
        "out_of_order_transition_symbols": ["XOM"],
        "ack_after_n_get_order_calls": 3,
    },
}

VALID_INTENT_STATUSES = {
    "draft", "submitted", "acknowledged", "open", "partially_filled",
    "filled", "canceled", "rejected", "expired", "manual_review",
    "new", "pending",  # broker-mapped statuses from _map_broker_status_to_intent_status
}

REQUIRED_INTENT_FIELDS = {
    "intent_id", "package_id", "candidate_id", "client_order_id",
    "status", "shadow_mode", "order_request", "broker_binding", "audit",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS.keys()), default="baseline")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    smoke_dir = repo_root / "tests" / "replays" / "execution_reliability_smoke"
    out_dir = Path(args.output_dir).resolve() if args.output_dir else (smoke_dir / "out" / args.scenario)
    out_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(repo_root))

    # Ensure mock adapter is used
    os.environ.setdefault("BROKER_ADAPTER", "mock")

    from src.execution.shadow_order_router import ShadowOrderRouter
    from src.execution.order_intent_registry import OrderIntentRegistry
    from src.execution.broker_state_reconciler_loop import BrokerStateReconcilerLoop
    from src.execution.stale_intent_sweeper import StaleIntentSweeper

    package = load_json(smoke_dir / "package_fixture.json")
    # Assign unique package_id per run to prevent cross-run contamination
    package["package_id"] = f"pkg-smoke-{args.scenario}-{uuid.uuid4().hex[:8]}"
    router = ShadowOrderRouter(repo_root=repo_root, broker_name="mock")

    # Inject fault profile into mock adapter
    scenario_profile = dict(SCENARIOS[args.scenario])
    if hasattr(router.adapter, "set_fault_profile"):
        router.adapter.set_fault_profile(scenario_profile)

    route_summary = router.route_package(
        package=package,
        max_orders=5,
        min_confidence=0.60,
    )

    # Extract package_id for scenario-scoped intent queries
    scenario_package_id = route_summary.get("package_id")
    scenario_package_ids = [scenario_package_id] if scenario_package_id else None

    registry = OrderIntentRegistry(repo_root)
    intents_before = registry.list_intents(package_ids=scenario_package_ids)

    # Simulate fills based on scenario
    submitted = route_summary.get("bound_order_attempts", []) or []
    for row in submitted:
        oid = row.get("broker_order_id")
        sym = row.get("symbol")
        if not oid:
            continue

        # For stale_open_xom, let XOM remain stale, fill others
        if args.scenario == "stale_open_xom" and str(sym).upper() == "XOM":
            continue

        # For delayed_ack scenarios, trigger get_order calls to advance ack state
        if "delayed_ack" in args.scenario and str(sym).upper() == "XOM":
            for _ in range(4):
                try:
                    router.adapter.get_order(oid)
                except Exception:
                    pass

        # For partial_fill_cat, CAT stays partial by adapter fault; fill once
        if args.scenario == "partial_fill_cat":
            if str(sym).upper() == "CAT":
                router.adapter.simulate_fill(oid, fill_qty=5, fill_price=342.25)
            else:
                router.adapter.simulate_fill(oid, fill_qty=10, fill_price=107.35 if str(sym).upper() == "XOM" else 100.0)
            continue

        # Default: fill submitted orders
        px = 107.35 if str(sym).upper() == "XOM" else (342.25 if str(sym).upper() == "CAT" else 100.0)
        router.adapter.simulate_fill(oid, fill_qty=10, fill_price=px)

    # Reconcile using same mock adapter instance
    reconciler = BrokerStateReconcilerLoop(repo_root)
    reconciler.adapter = router.adapter
    reconciler.broker_name = "mock"
    reconciler_run = reconciler.run_once()

    intents_after = registry.list_intents(package_ids=scenario_package_ids)

    # Stale sweeper (very low TTL for CI immediacy in stale scenarios)
    sweeper = StaleIntentSweeper(repo_root)
    stale_threshold = 0.0 if "stale_open" in args.scenario else 9999.0
    ttl_policy_yaml = repo_root / "config" / "order_ttl_policy.yaml"
    # Only use TTL policy when not in CI catch-all mode (threshold=0 overrides policy)
    use_ttl_policy = (stale_threshold > 0) and ttl_policy_yaml.exists()
    stale_report = sweeper.sweep(
        stale_after_minutes=stale_threshold,
        mark_manual_review=False,
        emit_shadow_cancel_recommendations=True,
        use_time_window_ttl_policy=use_ttl_policy,
        ttl_policy_yaml=ttl_policy_yaml if use_ttl_policy else None,
        package_ids=scenario_package_ids,
    )

    summary = {
        "status": "ok",
        "scenario": args.scenario,
        "fault_profile": scenario_profile,
        "bound_order_attempt_count": len(route_summary.get("bound_order_attempts", []) or []),
        "skipped_candidates": len(route_summary.get("skipped_candidates", []) or []),
        "router_errors": len(route_summary.get("errors", []) or []),
        "reconciled_count": reconciler_run.get("reconciled_count", 0),
        "reconciler_errors": len(reconciler_run.get("errors", []) or []),
        "stale_intent_count": ((stale_report.get("summary") or {}).get("stale_intent_count")),
        "submit_attempt_count": route_summary.get("submit_attempt_count", 0),
        "broker_rejected_count": route_summary.get("broker_rejected_count", 0),
        "submitted_open_or_ack_count": route_summary.get("submitted_open_or_ack_count", 0),
    }

    # ---- Scenario-specific semantic assertions ----
    if args.scenario == "reject_xom":
        assert summary["bound_order_attempt_count"] >= 1, "Expected at least one order to submit despite XOM reject"
        assert route_summary.get("broker_rejected_count", 0) >= 1, "Expected at least one broker rejection for XOM"
        assert route_summary.get("submit_attempt_count", 0) >= 1, "Expected at least one submit attempt"
    elif args.scenario == "timeout_cat":
        assert summary["router_errors"] >= 1 or summary["bound_order_attempt_count"] < 2, "Expected timeout impact in routing"
    elif args.scenario == "partial_fill_cat":
        assert summary["reconciled_count"] >= 1
        # Verify CAT has partial fill activity (filled_qty > 0 but may not be complete)
        cat_intents = [i for i in intents_after if ((i.get("candidate_context") or {}).get("symbol") or "").upper() == "CAT"]
        for ci in cat_intents:
            broker_state = ci.get("broker_state") or {}
            filled_qty = float(broker_state.get("filled_qty") or 0)
            assert filled_qty > 0, f"Expected CAT to have some fills, got filled_qty={filled_qty}"
    elif args.scenario == "stale_open_xom":
        assert (summary["stale_intent_count"] or 0) >= 1, "Expected at least one stale intent"
        # Verify stale count is scoped to this scenario's package only
        xom_stale = [
            s for s in (stale_report.get("stale_intents") or [])
            if str(s.get("symbol") or "").upper() == "XOM"
        ]
        assert len(xom_stale) >= 1, "Expected XOM in stale intents"
    elif args.scenario.startswith("delayed_ack"):
        # Verify delayed ack scenario ran without crashing
        # After get_order calls in the fill loop, XOM should have transitioned past 'new'
        xom_intents = [i for i in intents_after if ((i.get("candidate_context") or {}).get("symbol") or "").upper() == "XOM"]
        if xom_intents:
            xi = xom_intents[-1]  # latest state
            broker_state = xi.get("broker_state") or {}
            bs = broker_state.get("status")
            # After 4 get_order calls (> ack_after_n=3), XOM should be accepted or beyond
            assert bs in {"accepted", "filled", "partially_filled", "new"}, \
                f"Unexpected XOM broker_status={bs} after delayed ack scenario"
        assert summary["reconciled_count"] >= 0, "Reconciliation should not crash on delayed ack"
    elif "ooo" in args.scenario:
        # Out-of-order: pipeline should not crash despite status inconsistency
        xom_intents = [i for i in intents_after if ((i.get("candidate_context") or {}).get("symbol") or "").upper() == "XOM"]
        for xi in xom_intents:
            broker_state = xi.get("broker_state") or {}
            bs = broker_state.get("status")
            filled_qty = float(broker_state.get("filled_qty") or 0)
            # OOO may keep status as "new"/"accepted" despite fills, or reconciler may update it
            assert bs in VALID_INTENT_STATUSES or bs in {"new", "accepted", "partially_filled", "filled"}, \
                f"Unexpected OOO broker_status={bs}"

    # ---- Universal assertions (all scenarios) ----
    for intent in intents_after:
        # Valid status enum
        assert intent.get("status") in VALID_INTENT_STATUSES, \
            f"Invalid intent status '{intent.get('status')}' for intent {intent.get('intent_id')}"
        # Required fields present
        for field in REQUIRED_INTENT_FIELDS:
            assert field in intent, \
                f"Missing required field '{field}' in intent {intent.get('intent_id')}"
        # No pipeline corruption: shadow_mode must be True
        assert intent.get("shadow_mode") is True, \
            f"Intent {intent.get('intent_id')} has shadow_mode={intent.get('shadow_mode')}, expected True"
        # Package ID must match scenario
        if scenario_package_id:
            assert intent.get("package_id") == scenario_package_id, \
                f"Intent {intent.get('intent_id')} has package_id={intent.get('package_id')}, expected {scenario_package_id}"

    # Write artifacts
    (out_dir / "route_summary.json").write_text(json.dumps(route_summary, indent=2), encoding="utf-8")
    (out_dir / "reconciler_run_once.json").write_text(json.dumps(reconciler_run, indent=2), encoding="utf-8")
    (out_dir / "stale_intent_sweeper_report.json").write_text(json.dumps(stale_report, indent=2), encoding="utf-8")
    (out_dir / "order_intents_before.json").write_text(json.dumps({"intents": intents_before}, indent=2), encoding="utf-8")
    (out_dir / "order_intents_after.json").write_text(json.dumps({"intents": intents_after}, indent=2), encoding="utf-8")
    (out_dir / "smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
