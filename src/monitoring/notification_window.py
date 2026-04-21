#!/usr/bin/env python3
"""Helpers for time-bound suppression of automated outbound bot updates."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional


def _parse_iso_datetime(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def notifications_muted_until() -> Optional[datetime]:
    """Return the outbound update mute deadline, if configured."""
    return _parse_iso_datetime(os.getenv("TELEGRAM_UPDATES_MUTED_UNTIL", ""))


def notifications_muted(now: Optional[datetime] = None) -> bool:
    """Whether automated outbound updates should currently be suppressed."""
    deadline = notifications_muted_until()
    if deadline is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc) < deadline.astimezone(timezone.utc)


def mute_reason() -> str:
    """Stable log/status reason for suppressed sends."""
    deadline = notifications_muted_until()
    if deadline is None:
        return "notifications_active"
    return f"muted_until:{deadline.isoformat()}"
