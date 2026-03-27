#!/usr/bin/env bash
# Auto Git Commit — daily snapshot at 11 PM UTC
set -euo pipefail

REPO_ROOT="${GLOBAL_SENTINEL_REPO_ROOT:-/opt/global-sentinel}"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/auto_git.log"
DATE_STR=$(date -u +"%Y-%m-%d")
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")

mkdir -p "$LOG_DIR"

log() {
    echo "[$TIMESTAMP] $1" | tee -a "$LOG_FILE"
}

cd "$REPO_ROOT"

log "=== Auto-commit starting ==="

# Stage tracked directories, excluding sensitive/generated paths
# Use pathspec exclusions; ignore warnings about .gitignore matches
git add \
    src/ \
    scripts/ \
    config/ \
    tests/ \
    -- \
    ':!.env' \
    ':!data/' \
    ':!reports/' \
    ':!logs/' \
    ':!.github/workflows/' \
    2>&1 | tee -a "$LOG_FILE" || true

# Check if anything is staged
if git diff --cached --quiet; then
    log "No changes to commit. Skipping."
    exit 0
fi

# Show what we're committing
CHANGED=$(git diff --cached --stat)
log "Staged changes:\n$CHANGED"

# Commit
git commit -m "Auto-commit: daily snapshot $DATE_STR" 2>&1 | tee -a "$LOG_FILE"

# Push
if git remote -v | grep -q origin; then
    git push origin "$(git branch --show-current)" 2>&1 | tee -a "$LOG_FILE"
    log "Push complete."
else
    log "No remote 'origin' configured. Skipping push."
fi

log "=== Auto-commit finished ==="
