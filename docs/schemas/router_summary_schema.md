## Canonical Router Summary Fields (vNext)

- scenario_package_id: string
- router_run_id: string
- bound_order_attempt_count: int
- bound_order_attempts: array[object]  # attempt-level records (intent_id, symbol, side, qty, ts, etc.)
- broker_rejected_count: int           # broker-level rejects (reject reason codes counted)
- filled_count: int
- partial_fill_count: int
- canceled_count: int
- stale_detected_count: int
- router_error_count: int
