#!/usr/bin/env python3
"""Market-session classification helpers for Global Sentinel.

This module provides explicit session classification for US equities and crypto
with Alpaca 24/5 overnight semantics reflected in the returned constraints.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dt_time, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def _parse_timestamp(value: Optional[Any]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class SessionClassification:
    """Normalized market session context."""

    timestamp_utc: str
    timestamp_et: str
    asset_class: str
    market: str
    session: str
    session_label: str
    intraday_phase: str
    session_bucket: str
    is_market_open: bool
    is_extended_hours: bool
    is_overnight: bool
    constraints: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketSessionClassifier:
    """Classify timestamps into trading sessions with Alpaca-aware constraints."""

    def __init__(self, timezone_name: str = "America/New_York"):
        self.et = ZoneInfo(timezone_name)

    def classify(
        self,
        timestamp_utc: Optional[Any] = None,
        *,
        asset_class: str = "equity",
        market: str = "alpaca_us",
    ) -> SessionClassification:
        dt_utc = _parse_timestamp(timestamp_utc)
        dt_et = dt_utc.astimezone(self.et)
        asset_class_norm = str(asset_class or "equity").lower()

        if asset_class_norm == "crypto":
            return SessionClassification(
                timestamp_utc=dt_utc.isoformat(),
                timestamp_et=dt_et.isoformat(),
                asset_class="crypto",
                market=market,
                session="continuous",
                session_label="crypto_24_7",
                intraday_phase="continuous",
                session_bucket="continuous",
                is_market_open=True,
                is_extended_hours=False,
                is_overnight=False,
                constraints={
                    "alpaca_crypto_continuous": True,
                    "limit_only": False,
                    "allowed_time_in_force": ["gtc", "ioc"],
                },
            )

        session = self._classify_equity_session(dt_et)
        intraday_phase = self._equity_intraday_phase(dt_et, session)
        session_bucket = intraday_phase if session == "regular" else session
        constraints = self._equity_constraints(
            session,
            intraday_phase=intraday_phase,
            session_bucket=session_bucket,
        )
        return SessionClassification(
            timestamp_utc=dt_utc.isoformat(),
            timestamp_et=dt_et.isoformat(),
            asset_class="equity",
            market=market,
            session=session,
            session_label=session,
            intraday_phase=intraday_phase,
            session_bucket=session_bucket,
            is_market_open=session != "closed",
            is_extended_hours=session in {"overnight", "pre_market", "after_hours"},
            is_overnight=session == "overnight",
            constraints=constraints,
        )

    def _classify_equity_session(self, dt_et: datetime) -> str:
        weekday = dt_et.weekday()  # Monday=0 ... Sunday=6
        current = dt_et.time()

        # Saturday always closed.
        if weekday == 5:
            return "closed"

        # Sunday only opens at 20:00 ET for overnight session.
        if weekday == 6:
            return "overnight" if current >= dt_time(20, 0) else "closed"

        # Friday closes at 20:00 ET and remains closed after that.
        if weekday == 4 and current >= dt_time(20, 0):
            return "closed"

        # Monday-Friday before 04:00 ET is overnight, sourced from previous evening.
        if current < dt_time(4, 0):
            return "overnight"
        if current < dt_time(9, 30):
            return "pre_market"
        if current < dt_time(16, 0):
            return "regular"
        if current < dt_time(20, 0):
            return "after_hours"
        return "overnight"

    def _equity_intraday_phase(self, dt_et: datetime, session: str) -> str:
        if session != "regular":
            return session

        current = dt_et.time()
        if current < dt_time(10, 30):
            return "opening"
        if current < dt_time(14, 30):
            return "midday"
        if current < dt_time(16, 0):
            return "power_hour"
        return "regular"

    def _equity_constraints(
        self,
        session: str,
        *,
        intraday_phase: str,
        session_bucket: str,
    ) -> Dict[str, Any]:
        if session == "overnight":
            return {
                "alpaca_24_5": True,
                "limit_only": True,
                "allowed_time_in_force": ["day"],
                "requires_overnight_tradable": True,
                "requires_not_overnight_halted": True,
                "session_phase": "overnight",
                "session_bucket": session_bucket,
                "liquidity_profile": "thin",
            }
        if session in {"pre_market", "after_hours"}:
            return {
                "alpaca_24_5": False,
                "extended_hours": True,
                "limit_only": False,
                "allowed_time_in_force": ["day", "gtc"],
                "session_phase": intraday_phase,
                "session_bucket": session_bucket,
                "liquidity_profile": "thin",
            }
        if session == "regular":
            liquidity_profile = {
                "opening": "opening_whipsaw",
                "midday": "midday_lull",
                "power_hour": "power_hour",
                "regular": "regular",
            }.get(intraday_phase, "regular")
            return {
                "alpaca_24_5": False,
                "extended_hours": False,
                "limit_only": False,
                "allowed_time_in_force": ["day", "gtc", "ioc", "fok", "opg", "cls"],
                "session_phase": intraday_phase,
                "session_bucket": session_bucket,
                "liquidity_profile": liquidity_profile,
            }
        return {
            "alpaca_24_5": False,
            "extended_hours": False,
            "limit_only": False,
            "allowed_time_in_force": [],
            "session_phase": intraday_phase,
            "session_bucket": session_bucket,
            "liquidity_profile": "closed",
        }
