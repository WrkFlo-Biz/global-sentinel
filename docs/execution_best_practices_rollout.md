# Execution Best-Practices Rollout (2026-03-06)

## What Was Implemented

1. Price and sizing reliability:
- Decision price fallback chain in router:
  - `price_hints.decision_price`
  - `price_hints.last_price`
  - `execution_constraints.limit_price_fallback`
  - `snapshot.market_microstructure`
  - Alpaca latest trade/quote
- `notional_pct` can now fail closed when price is missing (`fail_if_price_missing`, default `true`).
- Per-order sizing diagnostics are embedded in `order_request._gs_sizing`.

2. Broker-constraint hardening:
- Adapter enforces limit-price tick normalization (>= $1: 2 decimals, < $1: 4 decimals).
- Adapter enforces extended-hours compatibility (only DAY/GTC limit orders).
- Router blocks non-shortable symbols for new sell/short intents when broker metadata is available.

3. Strategy/account isolation:
- Duplicate-symbol filtering moved to per-strategy account scope in dual-route execution.
- Prevents day-trade account positions from suppressing medium-long candidate routing.

4. Observability schema hardening:
- Router selected/bound records now include side/qty/type/limit/tif/decision-price/sizing-method/notional fields.
- Crisis monitor events now use schema envelope: `schema_version`, `component`, `event_type`, `payload`.
- Dashboard `/api/portfolio` adds `status`, `account_errors`, `position_count_*`, and `consistency`.

5. Options safety groundwork:
- Added `config/options_rollout.yaml` (disabled + kill switch by default).
- Added `src/execution/options_guardrails.py` and router integration for deterministic options blocking.

## Verification Gate

Run:

```bash
python3 scripts/verify/execution_quality_guardrails.py --repo-root .
```

Non-zero exit means one or more guardrail violations were detected.

## Recommended Next 48 Hours

1. Observe fallback and missing-price rates over at least 2 full sessions.
2. Tune confidence-to-size mapping only after fallback rate is consistently below 15%.
3. Keep options `enabled: false` until:
- adapter abstraction for options is complete,
- liquidity checks are populated from real chain data,
- live kill-switch drill is tested.
