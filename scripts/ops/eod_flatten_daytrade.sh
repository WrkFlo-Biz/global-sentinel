#!/bin/bash
# EOD Flatten: Close ALL day trade positions
# Run daily at 15:45 ET via gs-eod-flatten.timer
set -a
source /opt/global-sentinel/.env
set +a

API_KEY="${ALPACA_API_KEY_DAYTRADE:-$ALPACA_API_KEY}"
API_SECRET="${ALPACA_SECRET_KEY_DAYTRADE:-$ALPACA_SECRET_KEY}"
BASE_URL="${ALPACA_PAPER_BASE_URL:-https://paper-api.alpaca.markets}"
LOG="/opt/global-sentinel/logs/execution/eod_flatten.log"
FLAG="/opt/global-sentinel/control/eod_flatten_active"

mkdir -p "$(dirname $LOG)" "$(dirname $FLAG)"

TS=$(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z')

# Check if today is a weekday
DOW=$(date +%u)
if [ "$DOW" -gt 5 ]; then
    echo "[$TS] Weekend - skipping EOD flatten" >> "$LOG"
    exit 0
fi

# Count positions
POS_COUNT=$(curl -sf -H "APCA-API-KEY-ID: $API_KEY" -H "APCA-API-SECRET-KEY: $API_SECRET" "$BASE_URL/v2/positions" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
echo "[$TS] Day trade positions: $POS_COUNT" >> "$LOG"

if [ "$POS_COUNT" = "0" ]; then
    echo "[$TS] No positions to flatten" >> "$LOG"
    exit 0
fi

# Log per-position P/L before closing
PNL_JSONL="/opt/global-sentinel/logs/execution/eod_flatten_pnl.jsonl"
TOTAL_PNL=$(python3 -c "
import json, sys, urllib.request
url = '${BASE_URL}/v2/positions'
req = urllib.request.Request(url, headers={
    'APCA-API-KEY-ID': '${API_KEY}',
    'APCA-API-SECRET-KEY': '${API_SECRET}',
})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        positions = json.loads(resp.read())
except Exception as e:
    print('ERR', file=sys.stderr); sys.exit(0)
ts = '$(TZ=America/New_York date --iso-8601=seconds)'
total_pnl = 0.0
with open('${PNL_JSONL}', 'a') as f:
    for p in positions:
        rec = {
            'timestamp': ts, 'symbol': p.get('symbol',''), 'qty': p.get('qty','0'),
            'side': p.get('side',''), 'avg_entry_price': p.get('avg_entry_price','0'),
            'current_price': p.get('current_price','0'), 'unrealized_pl': p.get('unrealized_pl','0'),
            'unrealized_plpc': p.get('unrealized_plpc','0'), 'market_value': p.get('market_value','0'),
        }
        f.write(json.dumps(rec) + '\n')
        total_pnl += float(p.get('unrealized_pl', 0))
print(f'{total_pnl:.2f}')
" 2>/dev/null || echo "ERR")
echo "[$TS] Pre-close total unrealized P/L: \$$TOTAL_PNL ($POS_COUNT positions)" >> "$LOG"

# Cancel pending orders
curl -sf -X DELETE -H "APCA-API-KEY-ID: $API_KEY" -H "APCA-API-SECRET-KEY: $API_SECRET" "$BASE_URL/v2/orders" > /dev/null 2>&1
echo "[$TS] Cancelled pending orders" >> "$LOG"
sleep 2

# Close ALL positions
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE -H "APCA-API-KEY-ID: $API_KEY" -H "APCA-API-SECRET-KEY: $API_SECRET" "$BASE_URL/v2/positions")
echo "[$TS] Close all positions: HTTP $HTTP_CODE" >> "$LOG"

# Write control flag
TZ=America/New_York date --iso-8601=seconds > "$FLAG"

# Verify after delay
sleep 5
REMAIN=$(curl -sf -H "APCA-API-KEY-ID: $API_KEY" -H "APCA-API-SECRET-KEY: $API_SECRET" "$BASE_URL/v2/positions" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo "ERR")
echo "[$TS] Remaining after flatten: $REMAIN" >> "$LOG"

# Retry if needed
if [ "$REMAIN" != "0" ] && [ "$REMAIN" != "ERR" ]; then
    echo "[$TS] Retrying close..." >> "$LOG"
    sleep 3
    curl -sf -X DELETE -H "APCA-API-KEY-ID: $API_KEY" -H "APCA-API-SECRET-KEY: $API_SECRET" "$BASE_URL/v2/positions" > /dev/null 2>&1
fi

# Telegram notification
python3 -c "
import sys;sys.path.insert(0,'/opt/global-sentinel')
try:
    from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier
    TelegramTopicNotifier(topic='v6_digest').send_message('EOD FLATTEN (Day Trade)\nClosed: $POS_COUNT positions\nTotal P/L: \$$TOTAL_PNL\nRemaining: $REMAIN')
except: pass
" 2>/dev/null

echo "[$TS] EOD flatten done" >> "$LOG"
