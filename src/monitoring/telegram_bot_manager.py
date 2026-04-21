#!/usr/bin/env python3
"""
Global Sentinel -- Telegram Bot Manager

Manages command handler instances for both bots:
- mo2darkbot (@mo2darkbot) -> day_trade strategy
- mo2drkbot (@mo2drkbot)  -> medium_long strategy

Each bot gets its own long-polling thread.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set

from src.monitoring.telegram_command_handler import TelegramCommandHandler


def iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _add_chat_ids(target: Set[str], *raw_values: str) -> None:
    """Parse one or more env var values into a normalized chat-id set."""
    for raw in raw_values:
        if not raw:
            continue
        for token in raw.replace(",", " ").split():
            token = token.strip()
            if token:
                target.add(token)


class TelegramBotManager:
    """Starts and manages Telegram command handlers for both bots."""

    def __init__(self, repo_root: Path, dashboard_base_url: str = "http://localhost:8501"):
        self.repo_root = repo_root
        self.dashboard_url = dashboard_base_url

        # Bot tokens
        default_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.darkbot_token = os.getenv("TELEGRAM_BOT_TOKEN_DARKBOT", default_token)
        self.drkbot_token = os.getenv("TELEGRAM_BOT_TOKEN_DRKBOT", default_token)

        # Chat IDs — collect all configured IDs into a set for security
        default_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        darkbot_chat_id = os.getenv("TELEGRAM_CHAT_ID_DARKBOT", default_chat_id)
        drkbot_chat_id = os.getenv("TELEGRAM_CHAT_ID_DRKBOT", default_chat_id)
        darkbot_extra_chat_ids = os.getenv("TELEGRAM_CHAT_IDS_DARKBOT", "")
        drkbot_extra_chat_ids = os.getenv("TELEGRAM_CHAT_IDS_DRKBOT", "")

        self.darkbot_chat_ids: Set[str] = set()
        self.drkbot_chat_ids: Set[str] = set()

        _add_chat_ids(
            self.darkbot_chat_ids,
            default_chat_id,
            darkbot_chat_id,
            darkbot_extra_chat_ids,
        )
        _add_chat_ids(
            self.drkbot_chat_ids,
            default_chat_id,
            drkbot_chat_id,
            drkbot_extra_chat_ids,
        )

        # Handler instances
        self._darkbot_handler: Optional[TelegramCommandHandler] = None
        self._drkbot_handler: Optional[TelegramCommandHandler] = None

    def start(self):
        """Start command handlers for both bots."""
        if self.darkbot_token and self.darkbot_chat_ids:
            self._darkbot_handler = TelegramCommandHandler(
                bot_token=self.darkbot_token,
                allowed_chat_ids=self.darkbot_chat_ids,
                strategy="day_trade",
                repo_root=self.repo_root,
                dashboard_base_url=self.dashboard_url,
            )
            self._darkbot_handler.start()
            print(f"[{iso_now()}] mo2darkbot command handler started (day_trade)")
        else:
            print(f"[{iso_now()}] mo2darkbot skipped: missing token or chat ID")

        if self.drkbot_token and self.drkbot_chat_ids:
            # Only start drkbot if it has a different token from darkbot
            # (or if darkbot wasn't started). If same token, both bots would
            # compete for getUpdates.
            if self.drkbot_token == self.darkbot_token and self._darkbot_handler:
                print(f"[{iso_now()}] mo2drkbot skipped: same token as mo2darkbot (would conflict)")
            else:
                self._drkbot_handler = TelegramCommandHandler(
                    bot_token=self.drkbot_token,
                    allowed_chat_ids=self.drkbot_chat_ids,
                    strategy="medium_long",
                    repo_root=self.repo_root,
                    dashboard_base_url=self.dashboard_url,
                )
                self._drkbot_handler.start()
                print(f"[{iso_now()}] mo2drkbot command handler started (medium_long)")
        else:
            print(f"[{iso_now()}] mo2drkbot skipped: missing token or chat ID")

    def stop(self):
        """Stop all command handlers."""
        if self._darkbot_handler:
            self._darkbot_handler.stop()
            print(f"[{iso_now()}] mo2darkbot command handler stopped")
        if self._drkbot_handler:
            self._drkbot_handler.stop()
            print(f"[{iso_now()}] mo2drkbot command handler stopped")

    def is_alive(self) -> bool:
        """Check if at least one handler is running."""
        darkbot_alive = self._darkbot_handler is not None and self._darkbot_handler.is_alive()
        drkbot_alive = self._drkbot_handler is not None and self._drkbot_handler.is_alive()
        return darkbot_alive or drkbot_alive
