#!/usr/bin/env bash
# Global Sentinel Disk Cleanup
# Runs weekly via gs-disk-cleanup.timer (Sunday 3:00 AM UTC)
set -euo pipefail

REPO="/opt/global-sentinel"
LOG_FILE="$REPO/logs/disk_cleanup.log"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] CLEANUP: $*" | tee -a "$LOG_FILE"
}

log "=== Disk cleanup started ==="

# 1. Delete reports older than 14 days
COUNT=$(find "$REPO/reports/" -name "*.json" -mtime +14 -type f 2>/dev/null | wc -l)
find "$REPO/reports/" -name "*.json" -mtime +14 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old report files (>14 days)"

# 2. Delete old logs
COUNT=$(find "$REPO/logs/" -name "*.log" -mtime +7 -type f 2>/dev/null | wc -l)
find "$REPO/logs/" -name "*.log" -mtime +7 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old log files (>7 days)"

# 3. Delete old .jsonl data files (but preserve quantum_feed/*.json)
COUNT=$(find "$REPO/data/" -name "*.jsonl" -mtime +14 -type f 2>/dev/null | wc -l)
find "$REPO/data/" -name "*.jsonl" -mtime +14 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old .jsonl data files (>14 days)"

# 4. Clean old synthetic trade files (keep last 7 days)
COUNT=$(find "$REPO/data/synthetic_trades/" -name "*.json" -mtime +7 -type f 2>/dev/null | wc -l)
find "$REPO/data/synthetic_trades/" -name "*.json" -mtime +7 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old synthetic trade files (>7 days)"

# 5. Clean system journal logs
sudo journalctl --vacuum-size=500M 2>&1 | tail -1 | while read -r line; do log "journalctl: $line"; done

# 6. Clean pip/uv cache
rm -rf ~/.cache/pip ~/.cache/uv 2>/dev/null || true
log "Cleaned pip/uv cache"

# 7. Report disk usage
DISK_USAGE=$(df -h / | tail -1 | awk '{print $5}')
log "Disk usage after cleanup: $DISK_USAGE"

log "=== Disk cleanup complete ==="
