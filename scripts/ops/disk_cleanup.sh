#!/usr/bin/env bash
# Global Sentinel Disk Cleanup
# Runs daily via gs-disk-cleanup.timer (3:00 AM UTC)
set -euo pipefail

REPO="/opt/global-sentinel"
LOG_FILE="$REPO/logs/disk_cleanup.log"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] CLEANUP: $*" | tee -a "$LOG_FILE"
}

log "=== Disk cleanup started ==="

# 1. Delete reports older than 7 days
COUNT=$(find "$REPO/reports/" -name "*.json" -mtime +7 -type f 2>/dev/null | wc -l)
find "$REPO/reports/" -name "*.json" -mtime +7 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old report files (>7 days)"

# 2. Delete old logs (>3 days)
COUNT=$(find "$REPO/logs/" -name "*.log" -mtime +3 -type f 2>/dev/null | wc -l)
find "$REPO/logs/" -name "*.log" -mtime +3 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old log files (>3 days)"

# 3. Delete old .jsonl data files (but preserve quantum_feed/*.json)
COUNT=$(find "$REPO/data/" -name "*.jsonl" -mtime +7 -type f 2>/dev/null | wc -l)
find "$REPO/data/" -name "*.jsonl" -mtime +7 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old .jsonl data files (>7 days)"

# 4. Clean old synthetic trade files (keep last 7 days)
COUNT=$(find "$REPO/data/synthetic_trades/" -name "*.json" -mtime +7 -type f 2>/dev/null | wc -l)
find "$REPO/data/synthetic_trades/" -name "*.json" -mtime +7 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old synthetic trade files (>7 days)"

# 5. Clean bridge cache (>2 days)
COUNT=$(find "$REPO/logs/bridge_cache/" -type f -mtime +2 2>/dev/null | wc -l)
find "$REPO/logs/bridge_cache/" -type f -mtime +2 -delete 2>/dev/null || true
log "Deleted $COUNT old bridge cache files (>2 days)"

# 6. Clean .bak files (>3 days)
COUNT=$(find "$REPO" -maxdepth 3 -name "*.bak*" -mtime +3 -type f 2>/dev/null | wc -l)
find "$REPO" -maxdepth 3 -name "*.bak*" -mtime +3 -type f -delete 2>/dev/null || true
log "Deleted $COUNT old .bak files (>3 days)"

# 7. Compress large log files (>10MB, >1 day old)
COUNT=$(find "$REPO/logs/" -name "*.log" -mtime +1 -size +10M -type f 2>/dev/null | wc -l)
find "$REPO/logs/" -name "*.log" -mtime +1 -size +10M -type f -exec gzip {} \; 2>/dev/null || true
log "Compressed $COUNT large log files (>10MB, >1 day)"

# 8. Clean system journal logs
sudo journalctl --vacuum-size=300M 2>&1 | tail -1 | while read -r line; do log "journalctl: $line"; done

# 9. Clean pip/uv cache
rm -rf ~/.cache/pip ~/.cache/uv 2>/dev/null || true
log "Cleaned pip/uv cache"

# 10. Report disk usage
DISK_USAGE=$(df -h / | tail -1 | awk '{print $5}')
log "Disk usage after cleanup: $DISK_USAGE"

log "=== Disk cleanup complete ==="
