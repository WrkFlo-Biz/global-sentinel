#!/usr/bin/env python3
"""Sliding-window packet deduplication by packet identifier."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional


def _parse_dt(value: Optional[str]) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)


class PacketDedupIndex:
    """Maintain a time-bounded set of packet identifiers."""

    def __init__(self, window_hours: float = 24.0, max_entries: int = 100000):
        self.window_hours = window_hours
        self.max_entries = max_entries
        self._index: "OrderedDict[str, datetime]" = OrderedDict()

    def _evict_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.window_hours)
        expired = [packet_id for packet_id, seen_at in self._index.items() if seen_at < cutoff]
        for packet_id in expired:
            self._index.pop(packet_id, None)
        while len(self._index) > self.max_entries:
            self._index.popitem(last=False)

    def is_duplicate(self, packet_id: str) -> bool:
        self._evict_expired()
        return packet_id in self._index

    def record(self, packet_id: str, timestamp: Optional[str] = None) -> bool:
        self._evict_expired()
        if packet_id in self._index:
            return False
        self._index[packet_id] = _parse_dt(timestamp)
        self._evict_expired()
        return True

    @property
    def size(self) -> int:
        self._evict_expired()
        return len(self._index)

    @property
    def stats(self) -> dict:
        return {"index_size": self.size, "window_hours": self.window_hours, "max_entries": self.max_entries}
