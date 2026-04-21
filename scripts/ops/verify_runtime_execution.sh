#!/usr/bin/env bash
set -euo pipefail

GS_HOST="${GS_HOST:-20.124.180.8}"
GS_REMOTE_ROOT="${GS_REMOTE_ROOT:-/opt/global-sentinel}"

GS_DAYTRADE_API_KEY="${GS_DAYTRADE_API_KEY:-}"
GS_DAYTRADE_API_SECRET="${GS_DAYTRADE_API_SECRET:-}"
GS_MEDLONG_API_KEY="${GS_MEDLONG_API_KEY:-}"
GS_MEDLONG_API_SECRET="${GS_MEDLONG_API_SECRET:-}"

print_header() {
  printf '\n== %s ==\n' "$1"
}

print_header "Services"
ssh "openclaw@${GS_HOST}" "sudo systemctl is-active global-sentinel global-sentinel-dashboard global-sentinel-reconciler"

print_header "Latest Crisis Monitor Event"
ssh "openclaw@${GS_HOST}" "python3 - <<'PY'
import json
from pathlib import Path

path = Path('${GS_REMOTE_ROOT}/logs/events/crisis_monitor_events.jsonl')
if not path.exists() or path.stat().st_size == 0:
    print('missing')
    raise SystemExit(0)

row = json.loads(path.read_text().strip().splitlines()[-1])
payload = row.get('payload') or {}
print(json.dumps({
    'timestamp_utc': row.get('timestamp_utc'),
    'event_type': row.get('event_type'),
    'schema_version': row.get('schema_version'),
    'component': row.get('component'),
    'strategy_breakdown': payload.get('strategy_breakdown'),
    'orders_submitted': payload.get('orders_submitted'),
    'candidates': payload.get('candidates'),
}, indent=2))
PY"

print_header "Latest Shadow Diagnostics"
ssh "openclaw@${GS_HOST}" "python3 - <<'PY'
import json
from pathlib import Path

path = Path('${GS_REMOTE_ROOT}/logs/events/crisis_monitor_events.jsonl')
if not path.exists() or path.stat().st_size == 0:
    print('missing')
    raise SystemExit(0)

rows = []
for raw in path.read_text().strip().splitlines():
    obj = json.loads(raw)
    if str(obj.get('event_type', '')).startswith('shadow_'):
        rows.append(obj)

for row in rows[-8:]:
    payload = row.get('payload') or {}
    print(json.dumps({
        'timestamp_utc': row.get('timestamp_utc'),
        'event_type': row.get('event_type'),
        'strategy': payload.get('strategy'),
        'cycle': payload.get('cycle'),
        'idea_count': payload.get('idea_count'),
        'after_filter_count': payload.get('after_filter_count'),
        'candidate_count': payload.get('candidate_count'),
        'blocked_candidate_count': payload.get('blocked_candidate_count'),
        'blocked_reason_sample': payload.get('blocked_reason_sample'),
        'global_blocks': payload.get('global_blocks'),
        'orders_submitted': payload.get('orders_submitted'),
        'candidates': payload.get('candidates'),
    }))
PY"

print_header "Heartbeat"
ssh "openclaw@${GS_HOST}" "python3 - <<'PY'
import json
from pathlib import Path

path = Path('${GS_REMOTE_ROOT}/logs/heartbeat.json')
if not path.exists() or path.stat().st_size == 0:
    print('missing')
    raise SystemExit(0)

print(json.dumps(json.loads(path.read_text()), indent=2))
PY"

print_header "Latest Router Summary"
ssh "openclaw@${GS_HOST}" "python3 - <<'PY'
import json
from pathlib import Path

path = Path('${GS_REMOTE_ROOT}/logs/execution/shadow_order_router.jsonl')
if not path.exists() or path.stat().st_size == 0:
    print('missing')
    raise SystemExit(0)

row = json.loads(path.read_text().strip().splitlines()[-1])
payload = row.get('payload') or {}
selected = (payload.get('selected_candidates') or [])[:3]
bound = (payload.get('bound_order_attempts') or [])[:3]
print(json.dumps({
    'timestamp_utc': row.get('timestamp_utc'),
    'event_type': row.get('event_type'),
    'broker_name': row.get('broker_name'),
    'strategy_name': payload.get('strategy_name'),
    'submit_attempt_count': payload.get('submit_attempt_count'),
    'submitted_open_or_ack_count': payload.get('submitted_open_or_ack_count'),
    'broker_rejected_count': payload.get('broker_rejected_count'),
    'selected_sample': selected,
    'bound_sample': bound,
}, indent=2))
PY"

print_header "Latest Binding Sample"
ssh "openclaw@${GS_HOST}" "python3 - <<'PY'
import json
from pathlib import Path

path = Path('${GS_REMOTE_ROOT}/logs/execution/router_order_bindings.jsonl')
if not path.exists() or path.stat().st_size == 0:
    print('missing')
    raise SystemExit(0)

rows = [json.loads(x) for x in path.read_text().strip().splitlines()[-5:]]
for row in rows:
    print(json.dumps({
        'timestamp_utc': row.get('timestamp_utc'),
        'symbol': row.get('symbol'),
        'qty': row.get('qty'),
        'broker_status': row.get('broker_status'),
        'sizing_method_used': row.get('sizing_method_used'),
        'account_equity': row.get('account_equity'),
        'target_notional': row.get('target_notional'),
        'decision_price': row.get('decision_price'),
        'qty_cap': row.get('qty_cap'),
        'qty_cap_source': row.get('qty_cap_source'),
    }))
PY"

fetch_orders() {
  local label="$1"
  local key="$2"
  local secret="$3"

  if [[ -z "$key" || -z "$secret" ]]; then
    printf '%s credentials missing; skipping order query.\n' "$label"
    return 0
  fi

  curl -fsS \
    -H "APCA-API-KEY-ID: ${key}" \
    -H "APCA-API-SECRET-KEY: ${secret}" \
    "https://paper-api.alpaca.markets/v2/orders?status=all&limit=5&direction=desc" \
    | python3 - <<'PY'
import json
import sys

rows = json.load(sys.stdin)
for row in rows:
    print(json.dumps({
        'submitted_at': row.get('submitted_at'),
        'symbol': row.get('symbol'),
        'side': row.get('side'),
        'qty': row.get('qty'),
        'status': row.get('status'),
        'type': row.get('type'),
    }))
PY
}

print_header "Day Trade Recent Orders"
fetch_orders "day_trade" "$GS_DAYTRADE_API_KEY" "$GS_DAYTRADE_API_SECRET"

print_header "Medium/Long Recent Orders"
fetch_orders "medium_long" "$GS_MEDLONG_API_KEY" "$GS_MEDLONG_API_SECRET"
