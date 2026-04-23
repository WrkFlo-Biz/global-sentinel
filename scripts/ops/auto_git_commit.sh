#!/usr/bin/env bash
# Deprecated compatibility stub.
#
# Unattended repo mutation has been removed from supported Global Sentinel
# runtime paths. This file remains only to absorb any leftover external cron or
# automation entrypoints without staging, committing, or pushing changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="${GLOBAL_SENTINEL_REPO_ROOT:-$DEFAULT_REPO_ROOT}"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/auto_git.log"
TIMESTAMP="$(date -u +"%Y-%m-%d %H:%M:%S UTC")"

mkdir -p "$LOG_DIR"

MESSAGE="auto_git_commit.sh is disabled; unattended git commit/push is no longer supported in runtime."

printf '[%s] %s\n' "$TIMESTAMP" "$MESSAGE" | tee -a "$LOG_FILE" >&2
exit 0
