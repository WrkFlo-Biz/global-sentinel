#!/usr/bin/env python3
"""Global Sentinel P2-6 — Event Calendar Reader

Reads config/event_calendar.yaml and determines if any pre-event actions
should be triggered today. Integrates with the conditional order engine.

Called by the main sentinel loop or trade analysis engine to check
if position adjustments are needed based on upcoming events.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.event_calendar_reader")

REPO_ROOT = Path("/opt/global-sentinel")
CALENDAR_PATH = REPO_ROOT / "config" / "event_calendar.yaml"


def _load_calendar() -> Dict[str, Any]:
    """Load event calendar YAML."""
    try:
        import yaml
        if CALENDAR_PATH.exists():
            return yaml.safe_load(CALENDAR_PATH.read_text()) or {}
    except Exception as e:
        logger.error(f"Failed to load event calendar: {e}")
    return {}


def _parse_date(d: str) -> Optional[date]:
    """Parse a YYYY-MM-DD string."""
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def get_active_events(check_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """Get all events whose pre-event window includes check_date.

    Returns list of action dicts with keys:
      - event_type, event_date, action, description
      - For earnings: also includes 'symbol'
    """
    if check_date is None:
        check_date = date.today()

    cal = _load_calendar()
    active = []

    for section_key, section in cal.items():
        if not isinstance(section, dict) or "event_type" not in section:
            continue

        event_type = section["event_type"]
        pre_action = section.get("pre_event_action", {})
        days_before = int(pre_action.get("days_before", 1))

        if event_type == "earnings_report":
            # Per-ticker earnings
            for ticker_entry in section.get("tickers", []):
                symbol = ticker_entry.get("symbol", "")
                for d in ticker_entry.get("dates", []):
                    event_date = _parse_date(d)
                    if event_date is None:
                        continue
                    trigger_date = event_date - timedelta(days=days_before)
                    if check_date == trigger_date:
                        active.append({
                            "event_type": event_type,
                            "event_date": str(event_date),
                            "trigger_date": str(trigger_date),
                            "symbol": symbol,
                            "action": "flatten_ticker",
                            "description": pre_action.get("description", ""),
                        })
        else:
            # Macro events with date lists
            for d in section.get("dates", []):
                event_date = _parse_date(d)
                if event_date is None:
                    continue
                trigger_date = event_date - timedelta(days=days_before)
                if check_date == trigger_date:
                    action_detail = {}
                    if "reduce_position_pct" in pre_action:
                        action_detail["reduce_position_pct"] = pre_action["reduce_position_pct"]
                    if "increase_cash" in pre_action:
                        action_detail["increase_cash"] = True
                    if "hedge_instrument" in pre_action:
                        action_detail["hedge_instrument"] = pre_action["hedge_instrument"]
                        action_detail["hedge_allocation_pct"] = pre_action.get("hedge_allocation_pct", 5)
                    if "widen_stops_pct" in pre_action:
                        action_detail["widen_stops_pct"] = pre_action["widen_stops_pct"]

                    active.append({
                        "event_type": event_type,
                        "event_date": str(event_date),
                        "trigger_date": str(trigger_date),
                        "action": action_detail,
                        "description": pre_action.get("description", ""),
                    })

    return active


def get_upcoming_events(days_ahead: int = 7, check_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """Get all events in the next N days (for dashboard/reporting)."""
    if check_date is None:
        check_date = date.today()

    cal = _load_calendar()
    upcoming = []

    for section_key, section in cal.items():
        if not isinstance(section, dict) or "event_type" not in section:
            continue

        event_type = section["event_type"]

        if event_type == "earnings_report":
            for ticker_entry in section.get("tickers", []):
                symbol = ticker_entry.get("symbol", "")
                for d in ticker_entry.get("dates", []):
                    event_date = _parse_date(d)
                    if event_date and check_date <= event_date <= check_date + timedelta(days=days_ahead):
                        upcoming.append({
                            "event_type": event_type,
                            "event_date": str(event_date),
                            "symbol": symbol,
                            "days_away": (event_date - check_date).days,
                        })
        else:
            for d in section.get("dates", []):
                event_date = _parse_date(d)
                if event_date and check_date <= event_date <= check_date + timedelta(days=days_ahead):
                    upcoming.append({
                        "event_type": event_type,
                        "event_date": str(event_date),
                        "days_away": (event_date - check_date).days,
                    })

    upcoming.sort(key=lambda x: x["event_date"])
    return upcoming


def main():
    """Print active events for today and upcoming events."""
    logging.basicConfig(level=logging.INFO)
    today = date.today()
    print(f"\n=== Active Events for {today} ===")
    active = get_active_events(today)
    if active:
        print(json.dumps(active, indent=2))
    else:
        print("No active pre-event triggers today.")

    print(f"\n=== Upcoming Events (next 14 days) ===")
    upcoming = get_upcoming_events(days_ahead=14, check_date=today)
    if upcoming:
        print(json.dumps(upcoming, indent=2))
    else:
        print("No upcoming events in the next 14 days.")


if __name__ == "__main__":
    main()
