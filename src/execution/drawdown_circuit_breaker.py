#!/usr/bin/env python3
"""
Global Sentinel — Drawdown Circuit Breaker Enhancement

Adds daily P&L tracking to the existing circuit breaker:
- If paper account daily P&L hits -$200 OR -2% of equity: trips breaker
- Creates /tmp/gs_circuit_breaker_active sentinel file
- Auto-resets at 9:25 AM ET next day
- Sends Telegram alert when tripped
- Logs events to logs/circuit_breaker.jsonl

This module is imported by paper_trade_mirror.py to gate order placement.
"""
import json, os, ssl, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone
import sys

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
BREAKER_FILE = Path("/tmp/gs_circuit_breaker_active")
LOG_FILE = REPO_ROOT / "logs" / "circuit_breaker.jsonl"

# Thresholds
MAX_DAILY_LOSS_DOLLARS = 200.0
MAX_DAILY_LOSS_PCT = 0.02  # 2%

# Load .env lazily
_env = None
def _get_env():
    global _env
    if _env is not None:
        return _env
    _env = {}
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _env[k.strip()] = v.strip()
    return _env

ctx = ssl.create_default_context()

def _now_et():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def _log_event(event_type, details=None):
    """Append event to circuit_breaker.jsonl."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
    }
    if details:
        entry.update(details)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _send_telegram(msg):
    """Send alert via Telegram."""
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="trading")
            return
        except Exception:
            pass
    env = _get_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "7091381625")
    if not token:
        return
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML", "message_thread_id": int(env.get("TELEGRAM_TRADING_THREAD_ID", "74")),
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception:
        pass


def is_breaker_active():
    """Check if the drawdown circuit breaker is currently active."""
    if not BREAKER_FILE.exists():
        return False

    # Check if we should auto-reset (9:25 AM ET next day)
    try:
        et = _now_et()
        # Read when it was tripped
        trip_data = json.loads(BREAKER_FILE.read_text())
        trip_date = trip_data.get("trip_date", "")

        today_str = et.strftime("%Y-%m-%d")
        current_minutes = et.hour * 60 + et.minute

        # Auto-reset: if it's a new day AND past 9:25 AM ET
        if trip_date != today_str and current_minutes >= (9 * 60 + 25):
            reset_breaker("auto_reset_next_day")
            return False
    except Exception:
        pass

    return True


def trip_breaker(daily_pnl, equity, reason=""):
    """Trip the circuit breaker."""
    et = _now_et()
    trip_data = {
        "trip_date": et.strftime("%Y-%m-%d"),
        "trip_time": et.isoformat(),
        "daily_pnl": daily_pnl,
        "equity": equity,
        "pnl_pct": round(daily_pnl / equity * 100, 2) if equity > 0 else 0,
        "reason": reason,
    }
    BREAKER_FILE.write_text(json.dumps(trip_data, indent=2))

    _log_event("breaker_tripped", trip_data)

    msg = (
        f"<b>CIRCUIT BREAKER</b>: Daily loss limit hit "
        f"(${daily_pnl:.2f}). Trading halted until tomorrow.\n\n"
        f"Equity: ${equity:.0f}\n"
        f"P&L %: {trip_data['pnl_pct']:.2f}%\n"
        f"Reason: {reason}\n"
        f"Auto-reset: 9:25 AM ET tomorrow"
    )
    _send_telegram(msg)
    print(f"[CIRCUIT BREAKER] TRIPPED: daily P&L ${daily_pnl:.2f}, equity ${equity:.0f}", flush=True)


def reset_breaker(reason="manual"):
    """Reset the circuit breaker."""
    if BREAKER_FILE.exists():
        BREAKER_FILE.unlink()
    _log_event("breaker_reset", {"reason": reason})
    print(f"[CIRCUIT BREAKER] RESET: {reason}", flush=True)


def check_daily_pnl(base_url, api_key, api_secret, account_label="paper"):
    """
    Check account daily P&L and trip breaker if threshold exceeded.
    Returns (is_tripped, daily_pnl, equity).
    """
    # First check auto-reset
    if is_breaker_active():
        return True, 0, 0

    try:
        # Get account info
        url = f"{base_url}/v2/account"
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", api_key)
        req.add_header("APCA-API-SECRET-KEY", api_secret)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            acct = json.loads(resp.read())

        equity = float(acct.get("equity", 0))
        last_equity = float(acct.get("last_equity", 0))

        if last_equity <= 0:
            return False, 0, equity

        daily_pnl = equity - last_equity
        pnl_pct = daily_pnl / last_equity

        # Check thresholds
        if daily_pnl <= -MAX_DAILY_LOSS_DOLLARS:
            trip_breaker(daily_pnl, equity,
                         f"{account_label} daily loss exceeded ${MAX_DAILY_LOSS_DOLLARS}")
            return True, daily_pnl, equity

        if pnl_pct <= -MAX_DAILY_LOSS_PCT:
            trip_breaker(daily_pnl, equity,
                         f"{account_label} daily loss exceeded {MAX_DAILY_LOSS_PCT*100:.0f}%")
            return True, daily_pnl, equity

        return False, daily_pnl, equity

    except Exception as e:
        print(f"[CIRCUIT BREAKER] Error checking P&L: {e}", flush=True)
        _log_event("check_error", {"error": str(e)})
        return False, 0, 0


def gate_order(account_label="paper"):
    """
    Gate function to call before placing any order.
    Returns True if order should proceed, False if breaker is active.
    """
    if is_breaker_active():
        print(f"[CIRCUIT BREAKER] Circuit breaker active -- halting new trades ({account_label})", flush=True)
        _log_event("order_blocked", {"account": account_label})
        return False
    return True
