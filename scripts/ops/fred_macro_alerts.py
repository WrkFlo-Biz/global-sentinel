#!/usr/bin/env python3
"""
Global Sentinel — FRED Macro Surprise Alerts
Long-running service that monitors key economic releases via FRED API.
Checks every 15 minutes, sends Telegram alerts on significant moves.
Active Mon-Fri 8 AM - 5 PM ET only.
"""
import json, os, sys, ssl, time, urllib.request, urllib.error, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QUANTUM_FEED = REPO_ROOT / "data" / "quantum_feed"
STATE_FILE = Path("/tmp/gs_fred_state.json")
OUTPUT_FILE = QUANTUM_FEED / "fred_alerts.json"

# Load .env
env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())

FRED_API_KEY = env.get("FRED_API_KEY", "")
TG_TOKEN = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = "7091381625"
ctx = ssl.create_default_context()

CHECK_INTERVAL = 900  # 15 minutes

# High-impact series to monitor
MONITORED_SERIES = {
    # Rates
    "DGS10": {"name": "10Y Yield", "category": "rates"},
    "DGS2": {"name": "2Y Yield", "category": "rates"},
    "T10Y2Y": {"name": "10Y-2Y Spread", "category": "rates"},
    "DFF": {"name": "Fed Funds Rate", "category": "rates"},
    "VIXCLS": {"name": "VIX", "category": "volatility"},
    # Macro
    "CPIAUCSL": {"name": "CPI", "category": "inflation"},
    "UNRATE": {"name": "Unemployment Rate", "category": "labor"},
    "PAYEMS": {"name": "Nonfarm Payrolls", "category": "labor"},
    "ICSA": {"name": "Initial Claims", "category": "labor"},
}

# Thresholds from macro_policy_intel.yaml
THRESHOLDS = {
    "DGS10": 0.10,
    "DGS2": 0.10,
    "T10Y2Y": 0.15,
    "DFF": 0.10,
    "VIXCLS": 2.0,
    "CPIAUCSL": 0.5,
    "UNRATE": 0.2,
    "PAYEMS": 150.0,
    "ICSA": 20000.0,
}

# Trading implications
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


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="macro")
            return
        except Exception:
            pass
    if not TG_TOKEN:
        log("[TG] No token")
        return
    try:
        payload = json.dumps({
            "chat_id": TG_CHAT,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True, "message_thread_id": 74,
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


def now_et():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def load_state():
    """Load last known values from state file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_fred_series(series_id):
    """Fetch latest observation from FRED API."""
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
        if not observations:
            return None, None
        # Latest non-"." value
        for obs in observations:
            val = obs.get("value", ".")
            if val != ".":
                return float(val), obs.get("date", "")
        return None, None
    except Exception as e:
        log(f"FRED fetch error for {series_id}: {e}")
        return None, None


def check_all_series():
    """Check all monitored series for significant moves."""
    state = load_state()
    alerts = []
    updated_state = dict(state)

    for series_id, meta in MONITORED_SERIES.items():
        value, date = fetch_fred_series(series_id)
        if value is None:
            continue

        prev_entry = state.get(series_id, {})
        prev_value = prev_entry.get("value")
        prev_date = prev_entry.get("date", "")

        # Update state
        updated_state[series_id] = {
            "value": value,
            "date": date,
            "name": meta["name"],
            "category": meta["category"],
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }

        # Check for significant move (only if we have a previous value and date changed)
        if prev_value is not None and date != prev_date:
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
                    "date": date,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                alerts.append(alert)

                sign = "+" if delta > 0 else ""
                log(f"ALERT: {meta['name']} ({series_id}) moved {sign}{delta:.4f} "
                    f"to {value} (threshold: {threshold})")
        elif prev_value is None:
            log(f"Initialized {series_id} ({meta['name']}): {value} ({date})")

        # Rate limit FRED API
        time.sleep(0.5)

    save_state(updated_state)
    return alerts, updated_state


def send_alerts(alerts):
    """Send Telegram alerts for significant moves."""
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
        time.sleep(1)  # Don't spam Telegram


def save_output(alerts, state):
    """Save latest state and alerts to quantum feed."""
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


def run():
    log("=== FRED Macro Alerts Service starting ===")
    if not FRED_API_KEY:
        log("ERROR: No FRED_API_KEY configured")
        return

    while True:
        try:
            et = now_et()
            weekday = et.weekday()
            hour = et.hour

            # Only active Mon-Fri 8 AM - 5 PM ET
            if weekday >= 5 or hour < 8 or hour >= 17:
                sleep_msg = "Weekend" if weekday >= 5 else "Outside market hours"
                log(f"{sleep_msg} — sleeping 15 min")
                time.sleep(900)
                continue

            log("Checking FRED series...")
            alerts, state = check_all_series()

            if alerts:
                log(f"Found {len(alerts)} alert(s)")
                send_alerts(alerts)
            else:
                log("No significant moves detected")

            save_output(alerts, state)

            log(f"Next check in {CHECK_INTERVAL // 60} minutes")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("Shutting down")
            break
        except Exception as e:
            log(f"Error in main loop: {e}\n{traceback.format_exc()}")
            time.sleep(60)  # Back off on error


if __name__ == "__main__":
    run()
