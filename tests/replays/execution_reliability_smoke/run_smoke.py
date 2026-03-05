#!/usr/bin/env python3
"""
Execution Reliability Smoke (E2E)
- ShadowOrderRouter (mock broker)
- OrderIntentRegistry linkage
- Simulated fills
- BrokerStateReconcilerLoop run_once with shared adapter instance
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import sys


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    smoke_dir = repo_root / "tests" / "replays" / "execution_reliability_smoke"
    out_dir = smoke_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(repo_root))

    # Ensure mock adapter is used for smoke (avoids credential errors from real adapters)
    import os
    os.environ.setdefault("BROKER_ADAPTER", "mock")

    from src.execution.shadow_order_router import ShadowOrderRouter
    from src.execution.order_intent_registry import OrderIntentRegistry
    from src.execution.broker_state_reconciler_loop import BrokerStateReconcilerLoop

    # 1) Load fixture and route via mock broker
    package = load_json(smoke_dir / "package_fixture.json")
    router = ShadowOrderRouter(repo_root=repo_root, broker_name="mock")
    route_summary = router.route_package(
        package=package,
        max_orders=5,
        min_confidence=0.65
    )

    # 2) Inspect intents after routing
    registry = OrderIntentRegistry(repo_root)
    latest_intents = registry.list_intents()

    # 3) Simulate fills on the SAME mock broker instance held by router
    #    Fill one fully, one partially
    submitted = route_summary.get("bound_order_attempts", [])
    if len(submitted) >= 1:
        oid1 = submitted[0].get("broker_order_id")
        if oid1:
            router.adapter.simulate_fill(oid1, fill_qty=10, fill_price=107.35)  # qty will clamp to order qty

    if len(submitted) >= 2:
        oid2 = submitted[1].get("broker_order_id")
        if oid2:
            router.adapter.simulate_fill(oid2, fill_qty=1, fill_price=342.25)

    # 4) Reconcile using shared adapter instance
    #    The reconciler normally creates its own adapter, but we override it with router.adapter
    reconciler = BrokerStateReconcilerLoop(repo_root)
    reconciler.adapter = router.adapter
    reconciler.broker_name = "mock"
    reconciler_run = reconciler.run_once()

    # 5) Snapshot intents again after reconciliation
    latest_intents_after = registry.list_intents()

    # 6) Write outputs
    (out_dir / "route_summary.json").write_text(json.dumps(route_summary, indent=2), encoding="utf-8")
    (out_dir / "order_intents_latest.json").write_text(json.dumps({"intents": latest_intents}, indent=2), encoding="utf-8")
    (out_dir / "reconciler_run_once.json").write_text(json.dumps(reconciler_run, indent=2), encoding="utf-8")
    (out_dir / "order_intents_latest_after_reconcile.json").write_text(json.dumps({"intents": latest_intents_after}, indent=2), encoding="utf-8")

    # smoke assertions (lightweight)
    assert len(route_summary.get("bound_order_attempts", [])) >= 1, "Expected at least 1 bound order attempt"
    assert reconciler_run.get("reconciled_count", 0) >= 1, "Expected reconciler to process at least one intent"

    summary = {
        "status": "ok",
        "bound_order_attempt_count": len(route_summary.get("bound_order_attempts", [])),
        "skipped_candidates": len(route_summary.get("skipped_candidates", [])),
        "reconciled_count": reconciler_run.get("reconciled_count", 0),
        "error_count": len(reconciler_run.get("errors", [])),
        "out_dir": str(out_dir),
    }
    (out_dir / "smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
