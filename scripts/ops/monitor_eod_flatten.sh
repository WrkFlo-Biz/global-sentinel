#!/usr/bin/env bash
# One-time monitor for EOD flatten verification
# Runs from 15:40 to 16:20 ET, logging position counts every minute

# Source credentials from .env
set -a
# shellcheck source=/dev/null
source /opt/global-sentinel/.env
set +a

API_KEY="${ALPACA_API_KEY_DAYTRADE}"
API_SECRET="${ALPACA_SECRET_KEY_DAYTRADE}"

LOG="/opt/global-sentinel/logs/eod_flatten_monitor_$(date +%Y%m%d).log"
mkdir -p "$(dirname "$LOG")"
echo "EOD Flatten Monitor Started: $(date)" >> "$LOG"

for ((attempt=0; attempt<40; attempt++)); do
    TIMESTAMP=$(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z')

    # Count day trade positions
    DT_COUNT=$(curl -s -H "APCA-API-KEY-ID: ${API_KEY}" -H "APCA-API-SECRET-KEY: ${API_SECRET}" "https://paper-api.alpaca.markets/v2/positions" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "ERR")

    # Check position manager logs
    FLATTEN_LOG=$(sudo journalctl -u global-sentinel --since "1 min ago" --no-pager 2>/dev/null | grep -i "flatten\|eod\|close_all\|closing" | tail -3)

    echo "[$TIMESTAMP] Day-trade positions: $DT_COUNT | Flatten logs: $FLATTEN_LOG" >> "$LOG"
    sleep 60
done

echo "EOD Flatten Monitor Ended: $(date)" >> "$LOG"

# Final position count
FINAL_COUNT=$(curl -s -H "APCA-API-KEY-ID: ${API_KEY}" -H "APCA-API-SECRET-KEY: ${API_SECRET}" "https://paper-api.alpaca.markets/v2/positions" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
echo "FINAL: $FINAL_COUNT positions remaining" >> "$LOG"
