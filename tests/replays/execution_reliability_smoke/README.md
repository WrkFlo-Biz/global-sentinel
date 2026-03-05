# Execution Reliability Smoke (E2E)

This smoke test validates the shadow execution path:

1. Reads a package fixture
2. Routes candidates through `ShadowOrderRouter` using the **MockBrokerAdapter**
3. Creates and binds order intents in `OrderIntentRegistry`
4. Simulates fills on the mock broker
5. Runs `BrokerStateReconcilerLoop` once (using the same in-memory mock adapter)
6. Writes artifacts to `tests/replays/execution_reliability_smoke/out/`

## Run

```bash
python tests/replays/execution_reliability_smoke/run_smoke.py --repo-root .
```

## Expected outcomes

* `route_summary.json` has submitted orders
* `order_intents_latest.json` contains linked intents with client_order_id + broker_order_id
* `reconciler_run_once.json` completes with reconciled_count > 0
* Some intents move to `filled` / `partially_filled` after simulated fills
