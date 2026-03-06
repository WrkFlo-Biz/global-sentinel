#!/usr/bin/env python3
"""
Start Telegram command handlers standalone (for testing).

Usage:
    python3 scripts/start_telegram_bots.py
    python3 scripts/start_telegram_bots.py --repo-root /opt/global-sentinel
    python3 scripts/start_telegram_bots.py --dashboard-url http://localhost:8501

Environment variables required:
    TELEGRAM_BOT_TOKEN_DARKBOT  - Bot token for @mo2darkbot
    TELEGRAM_BOT_TOKEN_DRKBOT   - Bot token for @mo2drkbot
    TELEGRAM_CHAT_ID_DARKBOT    - Chat ID for darkbot
    TELEGRAM_CHAT_ID_DRKBOT     - Chat ID for drkbot

Optional:
    TELEGRAM_BOT_TOKEN  - Default fallback token
    TELEGRAM_CHAT_ID    - Default fallback chat ID
    GS_DASHBOARD_API_KEY - API key for dashboard endpoints
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT_DEFAULT = SCRIPT_DIR.parent


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description="Start Telegram bot command handlers")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT_DEFAULT),
        help="Repository root path (default: auto-detect)",
    )
    parser.add_argument(
        "--dashboard-url",
        default="http://localhost:8501",
        help="Dashboard API base URL (default: http://localhost:8501)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()

    # Add repo root to path so imports work
    sys.path.insert(0, str(repo_root))

    # Load .env file if present
    env_file = repo_root / ".env"
    if env_file.exists():
        import os
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    from src.monitoring.telegram_bot_manager import TelegramBotManager

    print(f"[{iso_now()}] Starting Telegram bot command handlers...")
    print(f"[{iso_now()}] Repo root: {repo_root}")
    print(f"[{iso_now()}] Dashboard URL: {args.dashboard_url}")

    manager = TelegramBotManager(repo_root, dashboard_base_url=args.dashboard_url)

    # Handle shutdown
    def shutdown(signum, frame):
        print(f"\n[{iso_now()}] Shutting down...")
        manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    manager.start()

    if not manager.is_alive():
        print(f"[{iso_now()}] No bots started. Check environment variables.", file=sys.stderr)
        sys.exit(1)

    print(f"[{iso_now()}] Telegram bots running. Press Ctrl+C to stop.")

    # Keep main thread alive
    while manager.is_alive():
        time.sleep(1)

    print(f"[{iso_now()}] All bots stopped.")


if __name__ == "__main__":
    main()
