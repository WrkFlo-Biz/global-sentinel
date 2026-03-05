#!/bin/bash
# Start Global Sentinel Dashboard
# API serves both the REST endpoints and the static frontend
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GS_REPO_ROOT="${GS_REPO_ROOT:-$REPO_ROOT}"

# Load env if present
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    . "$REPO_ROOT/.env"
    set +a
fi

echo "Starting Global Sentinel Dashboard API..."
echo "  Repo root: $GS_REPO_ROOT"
echo "  Serving on: http://0.0.0.0:8501"

cd "$REPO_ROOT"
exec python3 -m uvicorn dashboard.api.server:app --host 0.0.0.0 --port 8501
