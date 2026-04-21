#!/usr/bin/env python3
"""
Global Sentinel — FRED Release Calendar Bridge
Fetches upcoming economic release dates from FRED API, maps to tracked series,
outputs to quantum_feed, and sends Telegram alerts for high-impact releases.

Run daily at 7:00 AM ET (11:00 UTC) via gs-fred-calendar.timer
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
import traceback
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Setup paths ---
REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
sys.path.insert(0, str(REPO_ROOT)) if str(REPO_ROOT) not in sys.path else None

# --- Load .env ---
_env_path = REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

try:
    from src.monitoring.telegram_router import send as tg_send
except Exception:
    tg_send = None

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
QUANTUM_FEED = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QUANTUM_FEED / "fred_release_calendar.json"
ctx = ssl.create_default_context()

# ── Release ID → metadata mapping ─────────────────────────────────────
RELEASE_MAP: Dict[int, Dict[str, Any]] = {
    10: {
        "name": "CPI (Consumer Price Index)",
        "impact": "HIGH",
        "series": ["CPIAUCSL"],
        "release_time_et": "08:30",
        "spy_impact_pct": 1.2,
    },
    50: {
        "name": "Employment Situation (NFP)",
        "impact": "HIGH",
        "series": ["PAYEMS", "UNRATE"],
        "release_time_et": "08:30",
        "spy_impact_pct": 1.5,
    },
    53: {
        "name": "GDP (Gross Domestic Product)",
        "impact": "HIGH",
        "series": ["GDP"],
        "release_time_et": "08:30",
        "spy_impact_pct": 1.0,
    },
    46: {
        "name": "PPI (Producer Price Index)",
        "impact": "MEDIUM",
        "series": ["PPIACO"],
        "release_time_et": "08:30",
        "spy_impact_pct": 0.6,
    },
    21: {
        "name": "Retail Sales",
        "impact": "MEDIUM",
        "series": ["RSAFS"],
        "release_time_et": "08:30",
        "spy_impact_pct": 0.5,
    },
    13: {
        "name": "Industrial Production",
        "impact": "MEDIUM",
        "series": ["INDPRO"],
        "release_time_et": "09:15",
        "spy_impact_pct": 0.3,
    },
    19: {
        "name": "Durable Goods Orders",
        "impact": "MEDIUM",
        "series": ["DGORDER"],
        "release_time_et": "08:30",
        "spy_impact_pct": 0.4,
    },
    83: {
        "name": "JOLTS (Job Openings)",
        "impact": "MEDIUM",
        "series": ["JTSJOL"],
        "release_time_et": "10:00",
        "spy_impact_pct": 0.5,
    },
    82: {
        "name": "Consumer Sentiment (UMich)",
        "impact": "LOW",
        "series": ["UMCSENT"],
        "release_time_et": "10:00",
        "spy_impact_pct": 0.3,
    },
    194: {
        "name": "Initial Jobless Claims",
        "impact": "LOW",
        "series": ["ICSA"],
        "release_time_et": "08:30",
        "spy_impact_pct": 0.2,
    },
    86: {
        "name": "Commercial Paper",
        "impact": "LOW",
        "series": [],
        "release_time_et": "09:00",
        "spy_impact_pct": 0.1,
    },
}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(msg: str):
    """Send to macro topic via centralized router."""
    if tg_send:
        try:
            tg_send(msg[:4000], topic="macro")
            return
        except Exception as e:
            log(f"[TG router] Error: {e}")
    # Fallback direct send
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "-1003898688720")
    thread_id = os.getenv("TELEGRAM_MACRO_THREAD_ID", "2197")
    if not token:
        log("[TG] No token")
        return
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": msg[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "message_thread_id": int(thread_id),
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"[TG] Error: {e}")


def fetch_release_dates(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch upcoming release dates from FRED API."""
    url = (
        f"https://api.stlouisfed.org/fred/releases/dates"
        f"?include_release_dates_with_no_data=true"
        f"&realtime_start={start_date}"
        f"&realtime_end={end_date}"
        f"&api_key={FRED_API_KEY}"
        f"&file_type=json"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "GlobalSentinel-FREDCalendar/1.0"
        })
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            data = json.loads(resp.read())
        return data.get("release_dates", [])
    except Exception as e:
        log(f"Error fetching release dates: {e}")
        return []


def fetch_latest_value(series_id: str) -> Optional[Dict[str, Any]]:
    """Fetch latest observation for a series (for context in alerts)."""
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&file_type=json&sort_order=desc&limit=5"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "GlobalSentinel-FREDCalendar/1.0"
        })
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        # Get latest non-"." values
        values = []
        for obs in observations:
            val = obs.get("value", ".")
            if val != ".":
                values.append({"date": obs["date"], "value": float(val)})
            if len(values) >= 3:
                break
        if not values:
            return None
        # Compute trend from last 3 values
        trend = "stable"
        if len(values) >= 2:
            if values[0]["value"] > values[1]["value"]:
                trend = "rising"
            elif values[0]["value"] < values[1]["value"]:
                trend = "declining"
        return {
            "latest_value": values[0]["value"],
            "latest_date": values[0]["date"],
            "trend": trend,
            "recent_values": values[:3],
        }
    except Exception as e:
        log(f"Error fetching series {series_id}: {e}")
        return None


def build_calendar() -> Dict[str, Any]:
    """Build the full release calendar."""
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=14)

    start_str = today.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    log(f"Fetching FRED release dates: {start_str} to {end_str}")
    raw_dates = fetch_release_dates(start_str, end_str)
    log(f"Got {len(raw_dates)} raw release date entries")

    upcoming_releases: List[Dict[str, Any]] = []
    today_releases: List[Dict[str, Any]] = []
    high_impact_7d: List[Dict[str, Any]] = []
    seven_days = today + timedelta(days=7)

    seen = set()  # Deduplicate by (release_id, date)

    for entry in raw_dates:
        release_id = entry.get("release_id")
        release_date = entry.get("date", "")

        if release_id not in RELEASE_MAP:
            continue

        dedup_key = (release_id, release_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        meta = RELEASE_MAP[release_id]
        record = {
            "date": release_date,
            "release_name": meta["name"],
            "release_id": release_id,
            "impact_level": meta["impact"],
            "series_affected": meta["series"],
            "release_time_et": meta.get("release_time_et", "TBD"),
            "spy_impact_pct": meta.get("spy_impact_pct", 0),
        }

        upcoming_releases.append(record)

        # Check if today
        if release_date == start_str:
            today_releases.append(record)

        # Check if within 7 days and high impact
        try:
            rd = datetime.strptime(release_date, "%Y-%m-%d").date()
            if rd <= seven_days and meta["impact"] == "HIGH":
                high_impact_7d.append(record)
        except ValueError:
            pass

    # Sort by date
    upcoming_releases.sort(key=lambda x: x["date"])
    high_impact_7d.sort(key=lambda x: x["date"])

    # Fetch latest values for series in high-impact releases
    series_context: Dict[str, Any] = {}
    for rel in high_impact_7d:
        for sid in rel.get("series_affected", []):
            if sid not in series_context:
                ctx_data = fetch_latest_value(sid)
                if ctx_data:
                    series_context[sid] = ctx_data
                time.sleep(0.3)  # Rate limit

    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "period": {"start": start_str, "end": end_str},
        "upcoming_releases": upcoming_releases,
        "today_releases": today_releases,
        "high_impact_next_7d": high_impact_7d,
        "series_context": series_context,
        "tracked_release_ids": list(RELEASE_MAP.keys()),
    }
    return result


def send_calendar_digest(calendar: Dict[str, Any]):
    """Send Telegram digest of upcoming high-impact releases."""
    high = calendar.get("high_impact_next_7d", [])
    today = calendar.get("today_releases", [])
    context = calendar.get("series_context", {})

    if not high and not today:
        log("No high-impact releases in next 7 days")
        return

    lines = ["<b>FRED Release Calendar</b>"]
    lines.append("")

    if today:
        lines.append("<b>TODAY:</b>")
        for r in today:
            impact_emoji = {"HIGH": "!!!", "MEDIUM": "!!", "LOW": "!"}.get(r["impact_level"], "")
            lines.append(
                f"  {r['release_time_et']} ET - {r['release_name']} [{r['impact_level']}] {impact_emoji}"
            )
        lines.append("")

    if high:
        lines.append(f"<b>High-Impact Next 7 Days:</b>")
        for r in high:
            lines.append(f"  {r['date']} {r['release_time_et']} ET - {r['release_name']}")
            # Add context if available
            for sid in r.get("series_affected", []):
                ctx_data = context.get(sid)
                if ctx_data:
                    lines.append(
                        f"    Last: {ctx_data['latest_value']} | "
                        f"Trend: {ctx_data['trend']} | "
                        f"Historical SPY impact: +/-{r.get('spy_impact_pct', '?')}%"
                    )
        lines.append("")

    # Summary of all upcoming
    all_upcoming = calendar.get("upcoming_releases", [])
    med_count = sum(1 for r in all_upcoming if r["impact_level"] == "MEDIUM")
    low_count = sum(1 for r in all_upcoming if r["impact_level"] == "LOW")
    lines.append(f"Also: {med_count} medium, {low_count} low impact releases in 14-day window")

    msg = "\n".join(lines)
    send_telegram(msg)
    log("Sent calendar digest to Telegram")


def run():
    log("=== FRED Release Calendar starting ===")
    if not FRED_API_KEY:
        log("ERROR: No FRED_API_KEY configured")
        sys.exit(1)

    try:
        calendar = build_calendar()

        # Save output
        QUANTUM_FEED.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(calendar, indent=2))
        log(f"Saved calendar to {OUTPUT_FILE}")
        log(f"  {len(calendar['upcoming_releases'])} upcoming releases")
        log(f"  {len(calendar['today_releases'])} releasing today")
        log(f"  {len(calendar['high_impact_next_7d'])} high-impact in next 7 days")

        # Send Telegram digest
        send_calendar_digest(calendar)

        log("=== FRED Release Calendar complete ===")

    except Exception as e:
        log(f"Fatal error: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    run()
