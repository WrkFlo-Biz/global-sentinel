# Broker Cleanup Operating Spec

## Purpose
Classify and action all pending orders across both Alpaca accounts before each market session. This prevents order pile-up, catches position leaks, and ensures clean execution at market open.

## Classification Schema

| Bucket | Definition | Action | Priority |
|--------|-----------|--------|----------|
| pending_close | Close order queued for market open | WAIT | Low |
| stale_open | Order pending > 24h | CANCEL | High |
| duplicate | Same symbol+side+qty as another | CANCEL newer | High |
| crypto_orphan | Crypto order on equity account | CANCEL | Medium |
| partial_fill | Partially filled order | REVIEW | High |
| rejected | Broker rejected | LOG+CLEAR | Low |
| position_leak | Position with no close order | ALERT | Critical |

## Decision Rules

### Rule 1: Pending Close Identification
```
IF order.side == opposite(position.side)
AND order.qty <= position.qty
AND order.status IN ('new', 'accepted', 'pending_new')
AND order.created_at > last_market_close
THEN classify as pending_close
ACTION: WAIT — these execute normally at market open
```

### Rule 2: Stale Order Detection
```
IF order.status IN ('new', 'accepted', 'pending_new')
AND (now - order.created_at) > 24 hours
AND order NOT classified as pending_close
THEN classify as stale_open
ACTION: CANCEL via broker API
LOG: symbol, side, qty, age, original strategy
```

### Rule 3: Duplicate Detection
```
IF count(orders WHERE symbol=X AND side=Y AND qty=Z AND status='open') > 1
THEN classify newer orders as duplicate
ACTION: CANCEL the newer duplicate(s), keep oldest
LOG: all order IDs involved, creation timestamps
ALERT: if duplicates span both accounts (potential cross-account leak)
```

### Rule 4: Crypto Orphan Detection
```
IF order.symbol matches crypto pattern (e.g., *USD, *USDT, BTC*, ETH*)
AND order.account_type == 'equity'
THEN classify as crypto_orphan
ACTION: CANCEL — crypto orders belong on crypto-enabled accounts only
LOG: symbol, qty, side, account
```

### Rule 5: Partial Fill Handling
```
IF order.filled_qty > 0
AND order.filled_qty < order.qty
AND order.status IN ('partially_filled', 'new')
THEN classify as partial_fill
ACTION: REVIEW — requires human decision
OPTIONS:
  a) Cancel remainder (keep partial fill as position)
  b) Replace with market order for remainder
  c) Wait for fill (if within spread tolerance)
LOG: symbol, filled_qty, remaining_qty, fill_price, time_since_last_fill
```

### Rule 6: Rejected Order Cleanup
```
IF order.status == 'rejected'
THEN classify as rejected
ACTION: LOG reason and clear from tracking
LOG: symbol, side, qty, reject_reason, strategy_source
ALERT: if reject_reason indicates account issue (insufficient funds, pattern day trade)
```

### Rule 7: Position Leak Detection
```
IF position EXISTS in account
AND NO open/pending close order EXISTS for that position
AND position.age > 1 cycle
THEN classify as position_leak
ACTION: ALERT (Critical) — create close order immediately
LOG: symbol, qty, side, market_value, unrealized_pnl
ESCALATE: Telegram alert to operator
```

## Execution Modes

### Dry Run (default)
```bash
python3 scripts/ops/run_broker_order_audit.py --dry-run
```
- Reads all orders and positions from both accounts
- Classifies each order into buckets
- Prints summary table — takes NO action
- Output format:
```
=== Broker Order Audit (DRY RUN) ===
Account: PA3F6696XKWK (Day Trade)
  pending_close: 66 orders (WAIT)
  stale_open:     3 orders (WOULD CANCEL)
  duplicate:      1 orders (WOULD CANCEL newer)
  partial_fill:   0 orders
  rejected:       2 orders (WOULD CLEAR)
  position_leak:  0 positions

Account: PA36T8OFBNXB (Medium Long)
  pending_close: 12 orders (WAIT)
  stale_open:     0 orders
  duplicate:      0 orders
  partial_fill:   1 orders (NEEDS REVIEW)
  rejected:       0 orders
  position_leak:  0 positions
```

### Execute with Cancel
```bash
python3 scripts/ops/run_broker_order_audit.py --execute --cancel-stale
```
- Cancels all stale_open orders
- Cancels all duplicate orders (newer ones)
- Cancels all crypto_orphan orders
- Clears rejected orders from tracking
- Does NOT touch pending_close or partial_fill orders
- Logs all actions to `data/audit/broker_audit_YYYYMMDD_HHMMSS.json`

### Verify Flat
```bash
python3 scripts/ops/run_broker_order_audit.py --verify-flat
```
- Used at 9:31 AM ET to verify day-trade account is flat
- Checks: 0 positions, full buying power available
- Alerts if any positions remain

## Account-Specific Rules

### Day Trade Account (PA3F6696XKWK)
- All positions MUST be closed by 4:00 PM ET
- Any position surviving past close triggers position_leak alert
- Maximum 200 orders per day (Alpaca limit)
- Current order count: 176 (headroom: 24)

### Medium Long Account (PA36T8OFBNXB)
- Positions may be held overnight/multi-day
- Close orders classified as pending_close only if explicitly scheduled
- Position leak detection uses 7-day threshold (not 1 cycle)
- Maximum 500 orders per day (Alpaca limit)
- Current order count: 62 (headroom: 438)

## Order of Operations (Monday Pre-Market)

1. **T-60min (8:00 AM ET)**: Run dry-run audit on both accounts
2. **T-55min**: Review partial_fill orders — decide cancel/replace/wait
3. **T-50min**: Execute cancel-stale to clear stale and duplicate orders
4. **T-45min**: Re-run dry-run to verify clean state
5. **T-30min**: Verify pending_close count matches expected position count
6. **T-0 (9:30 AM ET)**: Market opens, pending closes execute
7. **T+1min (9:31 AM ET)**: Run verify-flat for day-trade account
8. **T+5min**: Check for any rejected orders from the open

## Logging and Audit Trail

All audit runs are logged to `data/audit/` with the following structure:
```json
{
  "timestamp": "2026-03-10T08:00:00Z",
  "mode": "dry_run|execute",
  "accounts": {
    "PA3F6696XKWK": {
      "orders_scanned": 176,
      "classifications": {
        "pending_close": 66,
        "stale_open": 3,
        "duplicate": 1,
        "crypto_orphan": 0,
        "partial_fill": 0,
        "rejected": 2,
        "position_leak": 0
      },
      "actions_taken": [
        {"order_id": "xxx", "action": "CANCEL", "reason": "stale_open", "symbol": "AAPL", "age_hours": 48}
      ]
    }
  }
}
```

## Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Alpaca API down | HTTP 503/timeout | Retry 3x with 5s backoff, then alert |
| Rate limited | HTTP 429 | Back off per rate_limiter.py (180 req/min) |
| Cancel rejected | Order already filled | Log fill, update position tracking |
| Position mismatch | Audit count != broker count | Re-fetch positions, reconcile, alert |
| Partial cancel | Cancel succeeds but order re-appears | Re-run audit, escalate if persistent |

## Guardrail Integration

The broker audit respects all existing guardrails:
- Kill switch (`control/kill_switch.json`) — if active, cancel ALL non-close orders
- Daily loss halt ($3,000) — if triggered, cancel all new orders, keep closes
- Max gross exposure (200%) — if breached after audit, generate close orders for excess

## Escalation Path

1. **Automated**: Log to `data/audit/`, console output
2. **Alert**: Telegram notification for position_leak and critical issues
3. **Escalate**: If 5+ stale orders found, or any position_leak, send immediate Telegram alert
4. **Manual**: Partial fills always require operator review before action
