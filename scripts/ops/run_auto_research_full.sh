#!/bin/bash
# run_auto_research_full.sh — Chains both auto-research components:
#   1. auto_researcher.py (pattern discovery, alpha mining, genetic evolution)
#   2. auto_research_optimizer.py (Karpathy-style parameter self-improvement)
#
# Usage: ./scripts/ops/run_auto_research_full.sh [optimizer_iterations]
# Default: 50 iterations for optimizer

set -euo pipefail

REPO_ROOT="/opt/global-sentinel"
LOG_DIR="$REPO_ROOT/logs"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG_FILE="$LOG_DIR/auto_research_full_${TIMESTAMP}.log"
ITERATIONS="${1:-50}"

export PYTHONPATH="$REPO_ROOT"
export GLOBAL_SENTINEL_REPO_ROOT="$REPO_ROOT"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"
}

log "=== Auto-Research Full Pipeline Starting ==="
log "Optimizer iterations: $ITERATIONS"

# Step 1: Auto-Researcher (pattern discovery)
RESEARCHER_EXIT=0
if [ -f "$REPO_ROOT/src/research/auto_researcher.py" ]; then
    log "--- Step 1: Running auto_researcher.py ---"
    python3 -m src.research.auto_researcher >> "$LOG_FILE" 2>&1 || RESEARCHER_EXIT=$?
    if [ $RESEARCHER_EXIT -ne 0 ]; then
        log "WARNING: auto_researcher.py exited with code $RESEARCHER_EXIT (continuing anyway)"
    else
        log "auto_researcher.py completed successfully"
    fi
else
    log "SKIP: auto_researcher.py not found (not yet created)"
fi

# Step 2: Auto-Research Optimizer (parameter self-improvement)
log "--- Step 2: Running auto_research_optimizer.py ($ITERATIONS iterations) ---"
OPTIMIZER_EXIT=0
python3 -m src.research.auto_research_optimizer "$ITERATIONS" >> "$LOG_FILE" 2>&1 || OPTIMIZER_EXIT=$?

if [ $OPTIMIZER_EXIT -ne 0 ]; then
    log "ERROR: auto_research_optimizer.py exited with code $OPTIMIZER_EXIT"
else
    log "auto_research_optimizer.py completed successfully"
fi

# Summary
log "=== Auto-Research Full Pipeline Complete ==="
log "Researcher exit: $RESEARCHER_EXIT, Optimizer exit: $OPTIMIZER_EXIT"

# Show optimizer summary if available
SUMMARY="$REPO_ROOT/data/quantum_feed/auto_research_optimizer.json"
if [ -f "$SUMMARY" ]; then
    log "--- Optimizer Summary ---"
    cat "$SUMMARY" | tee -a "$LOG_FILE"
fi

exit $OPTIMIZER_EXIT
