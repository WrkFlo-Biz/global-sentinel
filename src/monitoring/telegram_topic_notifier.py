#!/usr/bin/env python3
"""Telegram notifier that can route updates into a forum topic/thread."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.monitoring.notification_window import mute_reason, notifications_muted


@dataclass(frozen=True)
class TelegramSendResult:
    """Result of a Telegram send attempt."""

    ok: bool
    reason: str
    payload: Dict[str, Any]


class TelegramTopicNotifier:
    """Send Telegram updates, optionally to a specific topic thread."""

    # Named topic channels for routing
    TOPIC_ENV_MAP = {
        "canary": "TELEGRAM_CANARY_THREAD_ID",
        "research": "TELEGRAM_RESEARCH_THREAD_ID",
        "advisories": "TELEGRAM_ADVISORIES_THREAD_ID",
        "v6_digest": "TELEGRAM_V6_DIGEST_THREAD_ID",
    }

    def __init__(
        self,
        *,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        message_thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        disable_notification: Optional[bool] = None,
        topic: Optional[str] = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_ROLE_UPDATES_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
        # Resolve topic name to thread ID
        if topic and topic in self.TOPIC_ENV_MAP:
            self.message_thread_id = os.getenv(self.TOPIC_ENV_MAP[topic], "")
        else:
            self.message_thread_id = message_thread_id or os.getenv("TELEGRAM_ROLE_UPDATES_THREAD_ID", "")
        self.reply_to_message_id = reply_to_message_id or os.getenv("TELEGRAM_ROLE_UPDATES_REPLY_TO_MESSAGE_ID", "")
        env_disable = os.getenv("TELEGRAM_ROLE_UPDATES_DISABLE_NOTIFICATION", "")
        if disable_notification is not None:
            self.disable_notification = bool(disable_notification)
        else:
            self.disable_notification = str(env_disable).strip().lower() in {"1", "true", "yes", "on"}

    def send_message(self, text: str, *, require_topic_target: bool = False) -> TelegramSendResult:
        if notifications_muted():
            return TelegramSendResult(False, mute_reason(), {})
        if not self.bot_token or not self.chat_id:
            return TelegramSendResult(False, "missing_token_or_chat_id", {})
        if require_topic_target and not self.message_thread_id and not self.reply_to_message_id:
            return TelegramSendResult(False, "missing_topic_target", {})

        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "disable_notification": self.disable_notification,
        }
        if self.message_thread_id:
            payload["message_thread_id"] = int(self.message_thread_id)
        elif self.reply_to_message_id:
            payload["reply_to_message_id"] = int(self.reply_to_message_id)

        request = urllib.request.Request(
            url="https://api.telegram.org/bot%s/sendMessage" % self.bot_token,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            return TelegramSendResult(False, "url_error:%s" % exc, payload)
        except Exception as exc:  # pragma: no cover - defensive path
            return TelegramSendResult(False, "send_failed:%s" % exc, payload)

        return TelegramSendResult(bool(body.get("ok")), "sent" if body.get("ok") else "telegram_error", payload)
