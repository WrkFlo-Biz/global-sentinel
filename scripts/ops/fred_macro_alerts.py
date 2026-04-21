#!/usr/bin/env python3
"""
Global Sentinel — FRED Macro Surprise Alerts (v2)
Enhanced with pre-release positioning alerts.

Long-running service that:
1. Monitors key economic releases via FRED API (existing)
2. Reads release calendar and sends 24h / 1h pre-release positioning alerts (NEW)
3. Tracks alerted releases to avoid duplicates

Checks every 15 minutes, sends Telegram alerts on significant moves.
Active Mon-Fri 7 AM - 5 PM ET.
"""
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
QUANTUM_FEED = REPO_ROOT / "data" / "quantum_feed"
STATE_FILE = Path("/tmp/gs_fred_state.json")
OUTPUT_FILE = QUANTUM_FEED / "fred_alerts.json"
CALENDAR_FILE = QUANTUM_FEED / "fred_release_calendar.json"
RELEASE_ALERTS_FILE = Path("/tmp/gs_release_alerts_sent.json")
ctx = ssl.create_default_context()

CHECK_INTERVAL = 900  # 15 minutes

# High-impact series to monitor (existing)
MONITORED_SERIES = {
    "DGS10": {"name": "10Y Yield", "category": "rates"},
    "DGS2": {"name": "2Y Yield", "category": "rates"},
    "T10Y2Y": {"name": "10Y-2Y Spread", "category": "rates"},
    "DFF": {"name": "Fed Funds Rate", "category": "rates"},
    "VIXCLS": {"name": "VIX", "category": "volatility"},
    "CPIAUCSL": {"name": "CPI", "category": "inflation"},
    "UNRATE": {"name": "Unemployment Rate", "category": "labor"},
    "PAYEMS": {"name": "Nonfarm Payrolls", "category": "labor"},
    "ICSA": {"name": "Initial Claims", "category": "labor"},
}

THRESHOLDS = {
    "DGS10": 0.10, "DGS2": 0.10, "T10Y2Y": 0.15, "DFF": 0.10,
    "VIXCLS": 2.0, "CPIAUCSL": 0.5, "UNRATE": 0.2, "PAYEMS": 150.0,
    "ICSA": 20000.0,
}

IMPLICATIONS = {
    "DGS10": {
        "up": "Rates up = tech/growth bearish, financials bullish",
        "down": "Rates down = tech/growth bullish, financials bearish",
    },
    "DGS2": {
        "up": "Short rates up = hawkish signal, tightening",
        "down": "Short rates down = dovish signal, easing expectations",
    },
    "T10Y2Y": {
        "up": "Curve steepening = reflation trade, cyclicals benefit",
        "down": "Curve flattening/inversion = recession risk, risk-off",
    },
    "DFF": {
        "up": "Fed hiking = tightening, bearish risk assets",
        "down": "Fed cutting = easing, bullish risk assets",
    },
    "VIXCLS": {
        "up": "Volatility spike = fear rising, hedges needed",
        "down": "Volatility dropping = complacency, risk-on",
    },
    "CPIAUCSL": {
        "up": "Inflation rising = hawkish Fed, rates pressure",
        "down": "Inflation cooling = dovish Fed, rate cut hopes",
    },
    "UNRATE": {
        "up": "Unemployment rising = recession risk, dovish Fed",
        "down": "Unemployment falling = strong labor, hawkish Fed",
    },
    "PAYEMS": {
        "up": "Payrolls strong = hawkish, rates pressure",
        "down": "Payrolls weak = recession risk, dovish Fed",
    },
    "ICSA": {
        "up": "Claims rising = labor weakness, dovish signal",
        "down": "Claims falling = labor strength, hawkish signal",
    },
}

# Positioning suggestions by release type
POSITIONING_ADVICE = {
    "CPI (Consumer Price Index)": "Reduce position size, widen stops. CPI surprises drive 10Y yields and growth/value rotation.",
    "Employment Situation (NFP)": "Reduce size, avoid new entries pre-release. NFP is highest-vol macro event. Watch for wage growth too.",
    "GDP (Gross Domestic Product)": "Moderate positioning. GDP revisions can shift rate expectations significantly.",
    "PPI (Producer Price Index)": "Watch for pipeline inflation signals. Less volatile than CPI but can move rates.",
    "Retail Sales": "Consumer spending data — retail/consumer discretionary sectors most exposed.",
    "JOLTS (Job Openings)": "Labor demand proxy. Fed watches closely for wage pressure signals.",
}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(msg: str):
    """Send to macro topic via centralized router."""
    if _send_topic:
        try:
            _send_topic(msg[:4000], topic="macro")
            return
        except Exception:
            pass
    if not TG_TOKEN:
        log("[TG] No token")
        return
    try:
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "-1003898688720")
        thread_id = os.getenv("TELEGRAM_MACRO_THREAD_ID", "2197")
        payload = json.dumps({
            "chat_id": chat_id,
            "text": msg[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "message_thread_id": int(thread_id),
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"[TG] Error: {e}")


def now_et() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


# ── Existing FRED monitoring functions ─────────────────────────────────

def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: Dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_fred_series(series_id: str) -> Tuple[Optional[float], Optional[str]]:
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&file_type=json&sort_order=desc&limit=2"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        for obs in observations:
            val = obs.get("value", ".")
            if val != ".":
                return float(val), obs.get("date", "")
        return None, None
    except Exception as e:
        log(f"FRED fetch error for {series_id}: {e}")
        return None, None


def check_all_series() -> Tuple[List[Dict], Dict]:
    state = load_state()
    alerts = []
    updated_state = dict(state)

    for series_id, meta in MONITORED_SERIES.items():
        value, date_str = fetch_fred_series(series_id)
        if value is None:
            continue

        prev_entry = state.get(series_id, {})
        prev_value = prev_entry.get("value")
        prev_date = prev_entry.get("date", "")

        updated_state[series_id] = {
            "value": value,
            "date": date_str,
            "name": meta["name"],
            "category": meta["category"],
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }

        if prev_value is not None and date_str != prev_date:
            delta = value - prev_value
            threshold = THRESHOLDS.get(series_id, 0)

            if abs(delta) >= threshold:
                direction = "up" if delta > 0 else "down"
                implication = IMPLICATIONS.get(series_id, {}).get(
                    direction, "Monitor for follow-through"
                )
                alert = {
                    "series_id": series_id,
                    "name": meta["name"],
                    "category": meta["category"],
                    "previous": prev_value,
                    "current": value,
                    "delta": round(delta, 4),
                    "threshold": threshold,
                    "direction": direction,
                    "implication": implication,
                    "date": date_str,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                alerts.append(alert)
                sign = "+" if delta > 0 else ""
                log(f"ALERT: {meta['name']} ({series_id}) moved {sign}{delta:.4f} "
                    f"to {value} (threshold: {threshold})")
        elif prev_value is None:
            log(f"Initialized {series_id} ({meta['name']}): {value} ({date_str})")

        time.sleep(0.5)

    save_state(updated_state)
    return alerts, updated_state


def send_series_alerts(alerts: List[Dict]):
    for alert in alerts:
        sign = "+" if alert["delta"] > 0 else ""
        msg = (
            f"<b>FRED ALERT</b>: {alert['name']} moved {sign}{alert['delta']:.2f} "
            f"to {alert['current']:.2f} (threshold: {alert['threshold']})\n\n"
            f"<b>Direction:</b> {alert['direction'].upper()}\n"
            f"<b>Category:</b> {alert['category']}\n"
            f"<b>Implication:</b> {alert['implication']}\n"
            f"<b>Date:</b> {alert['date']}"
        )
        send_telegram(msg)
        time.sleep(1)


def save_output(alerts: List[Dict], state: Dict):
    QUANTUM_FEED.mkdir(parents=True, exist_ok=True)
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latest_values": {},
        "recent_alerts": alerts,
    }
    for sid, info in state.items():
        output["latest_values"][sid] = {
            "name": info.get("name", sid),
            "value": info.get("value"),
            "date": info.get("date"),
            "category": info.get("category"),
        }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))


# ── NEW: Pre-Release Positioning Alerts ────────────────────────────────

def load_release_alerts_sent() -> Dict[str, str]:
    """Load tracker of which release alerts have been sent.
    Returns dict of {alert_key: timestamp_sent}
    """
    if RELEASE_ALERTS_FILE.exists():
        try:
            data = json.loads(RELEASE_ALERTS_FILE.read_text())
            # Prune entries older than 30 days
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            return {k: v for k, v in data.items() if v > cutoff}
        except Exception:
            pass
    return {}


def save_release_alerts_sent(sent: Dict[str, str]):
    RELEASE_ALERTS_FILE.write_text(json.dumps(sent, indent=2))


def load_calendar() -> Optional[Dict[str, Any]]:
    """Load the FRED release calendar from quantum_feed."""
    if not CALENDAR_FILE.exists():
        log(f"Calendar file not found: {CALENDAR_FILE}")
        return None
    try:
        return json.loads(CALENDAR_FILE.read_text())
    except Exception as e:
        log(f"Error loading calendar: {e}")
        return None


def check_pre_release_alerts():
    """Check calendar for releases that need pre-positioning alerts."""
    calendar = load_calendar()
    if not calendar:
        return

    sent = load_release_alerts_sent()
    now_utc = datetime.now(timezone.utc)

    upcoming = calendar.get("upcoming_releases", [])
    context = calendar.get("series_context", {})

    for release in upcoming:
        if release.get("impact_level") not in ("HIGH", "MEDIUM"):
            continue

        release_date_str = release.get("date", "")
        release_time_et = release.get("release_time_et", "08:30")
        release_name = release.get("release_name", "Unknown")
        release_id = release.get("release_id", 0)
        spy_impact = release.get("spy_impact_pct", 0)
        series_affected = release.get("series_affected", [])

        try:
            from zoneinfo import ZoneInfo
            et_tz = ZoneInfo("America/New_York")
            # Parse release datetime in ET
            hour, minute = map(int, release_time_et.split(":"))
            release_dt_et = datetime.strptime(release_date_str, "%Y-%m-%d").replace(
                hour=hour, minute=minute, tzinfo=et_tz
            )
            release_dt_utc = release_dt_et.astimezone(timezone.utc)
        except Exception as e:
            log(f"Error parsing release time for {release_name}: {e}")
            continue

        hours_until = (release_dt_utc - now_utc).total_seconds() / 3600

        # 24-hour alert (send between 20-28 hours before)
        alert_key_24h = f"24h_{release_id}_{release_date_str}"
        if 20 <= hours_until <= 28 and alert_key_24h not in sent:
            # Only HIGH impact gets 24h alert
            if release.get("impact_level") == "HIGH":
                _send_pre_release_alert(
                    release_name, release_time_et, release_date_str,
                    series_affected, context, spy_impact, "24h"
                )
                sent[alert_key_24h] = now_utc.isoformat()
                log(f"Sent 24h pre-release alert for {release_name} on {release_date_str}")

        # 1-hour alert (send between 45min and 75min before)
        alert_key_1h = f"1h_{release_id}_{release_date_str}"
        if 0.75 <= hours_until <= 1.25 and alert_key_1h not in sent:
            _send_pre_release_alert(
                release_name, release_time_et, release_date_str,
                series_affected, context, spy_impact, "1h"
            )
            sent[alert_key_1h] = now_utc.isoformat()
            log(f"Sent 1h pre-release alert for {release_name} on {release_date_str}")

    save_release_alerts_sent(sent)


def _send_pre_release_alert(
    release_name: str,
    release_time_et: str,
    release_date: str,
    series_affected: List[str],
    context: Dict[str, Any],
    spy_impact: float,
    window: str,
):
    """Send a pre-release positioning alert."""
    if window == "24h":
        header = f"<b>MACRO ALERT: {release_name} Tomorrow {release_time_et} AM ET</b>"
    else:
        header = f"<b>MACRO REMINDER: {release_name} in ~1 Hour ({release_time_et} AM ET)</b>"

    lines = [header]

    # Add context for affected series
    for sid in series_affected:
        ctx_data = context.get(sid)
        if ctx_data:
            lines.append(
                f"Last: {ctx_data.get('latest_value', '?')} | "
                f"Trend: {ctx_data.get('trend', '?')}"
            )

    if spy_impact:
        lines.append(f"Historical SPY impact: +/-{spy_impact}% on release day")

    # Positioning advice
    advice = POSITIONING_ADVICE.get(release_name)
    if advice:
        lines.append(f"\n<b>Suggestion:</b> {advice}")
    else:
        lines.append(f"\n<b>Suggestion:</b> Reduce position size, widen stops")

    msg = "\n".join(lines)
    send_telegram(msg)


# ── Main loop ──────────────────────────────────────────────────────────

def run():
    log("=== FRED Macro Alerts v2 (with Pre-Release Positioning) starting ===")
    if not FRED_API_KEY:
        log("ERROR: No FRED_API_KEY configured")
        return

    while True:
        try:
            et = now_et()
            weekday = et.weekday()
            hour = et.hour

            # Active Mon-Fri 7 AM - 5 PM ET (extended from 8 AM for pre-release alerts)
            if weekday >= 5 or hour < 7 or hour >= 17:
                sleep_msg = "Weekend" if weekday >= 5 else "Outside market hours"
                log(f"{sleep_msg} - sleeping 15 min")
                time.sleep(900)
                continue

            # 1. Check existing FRED series for significant moves
            log("Checking FRED series...")
            alerts, state = check_all_series()
            if alerts:
                log(f"Found {len(alerts)} alert(s)")
                send_series_alerts(alerts)
            else:
                log("No significant moves detected")
            save_output(alerts, state)

            # 2. Check pre-release positioning alerts (NEW)
            log("Checking pre-release calendar alerts...")
            check_pre_release_alerts()

            log(f"Next check in {CHECK_INTERVAL // 60} minutes")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("Shutting down")
            break
        except Exception as e:
            log(f"Error in main loop: {e}\n{traceback.format_exc()}")
            time.sleep(60)


if __name__ == "__main__":
    run()
