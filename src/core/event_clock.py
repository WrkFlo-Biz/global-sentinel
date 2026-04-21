#!/usr/bin/env python3
"""Track event time versus processing time for incoming packets."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.core.structured_logger import get_logger


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


class EventClock:
    """Annotate packets with lag metadata and freshness decisions."""

    def __init__(self, max_lag_minutes: float = 60.0, allowed_future_skew_seconds: float = 5.0):
        self.max_lag_minutes = max_lag_minutes
        self.allowed_future_skew_seconds = allowed_future_skew_seconds
        self._logger = get_logger("event_clock")

    def annotate_packet(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        processing_time = packet.get("processing_time_utc") or now.isoformat()
        packet["processing_time_utc"] = processing_time

        event_time = packet.get("event_time_utc") or packet.get("timestamp_utc")
        event_dt = _parse_dt(event_time)
        processing_dt = _parse_dt(processing_time) or now

        if event_time is None:
            packet["_event_clock"] = {
                "event_time": None,
                "processing_time": processing_time,
                "lag_minutes": None,
                "stale": None,
                "missing_event_time": True,
            }
            return packet

        if event_dt is None:
            packet["_event_clock"] = {
                "event_time": event_time,
                "processing_time": processing_time,
                "lag_minutes": None,
                "stale": None,
                "parse_error": True,
            }
            return packet

        lag_seconds = (processing_dt - event_dt).total_seconds()
        lag_minutes = round(lag_seconds / 60.0, 3)
        stale = lag_minutes > self.max_lag_minutes
        future = lag_seconds < -self.allowed_future_skew_seconds
        packet["event_time_utc"] = event_dt.isoformat()
        packet["_event_clock"] = {
            "event_time": packet["event_time_utc"],
            "processing_time": processing_time,
            "lag_minutes": lag_minutes,
            "lag_seconds": round(lag_seconds, 3),
            "stale": stale,
            "future_timestamp": future,
            "max_lag_minutes": self.max_lag_minutes,
        }
        if stale or future:
            self._logger.warning(
                "event_clock_flagged_packet",
                packet_id=packet.get("packet_id"),
                lag_minutes=lag_minutes,
                stale=stale,
                future_timestamp=future,
            )
        return packet

    def is_stale(self, packet: Dict[str, Any]) -> bool:
        return bool((packet.get("_event_clock") or {}).get("stale", False))

    def lag_minutes(self, packet: Dict[str, Any]) -> Optional[float]:
        return (packet.get("_event_clock") or {}).get("lag_minutes")
