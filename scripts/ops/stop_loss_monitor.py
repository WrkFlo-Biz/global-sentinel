#!/usr/bin/env python3
"""
Global Sentinel — Portfolio Monitor (MONITOR-ONLY MODE)

- Checks every 30s during market hours
- Logs equity, positions, and cash — NO automatic selling
- All auto-sell triggers (drawdown, equity floor, EOD close) REMOVED per operator request 2026-04-16
- Sends Telegram alerts for large drawdowns but does NOT execute trades

Original rules removed:
  - 30% drawdown stop-loss: REMOVED
  - $100 equity floor: REMOVED
  - 3:55 PM EOD auto-close: REMOVED
"""
import json, time, ssl
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
import sys

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path("/opt/global-sentinel")

env = {}
with open(REPO_ROOT / ".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"  ")

TG_TOKEN   = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT    = env.get("TELEGRAM_CHAT_ID", "")
ALP_KEY    = env.get("ALPACA_API_KEY_LIVE", "")
ALP_SECRET = env.get("ALPACA_SECRET_KEY_LIVE", "")
h = {"APCA-API-KEY-ID": ALP_KEY, "APCA-API-SECRET-KEY": ALP_SECRET, "Content-Type": "application/json"}
BASE = "https://api.alpaca.markets"

CHECK_INTERVAL_S = 30
ctx = ssl.create_default_context()

def _notifications_muted():
    raw = env.get("TELEGRAM_UPDATES_MUTED_UNTIL", "")
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < deadline
    except Exception:
        return False

def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="trading")
            return
        except Exception:
            pass
    if _notifications_muted(): return
    if not TG_TOKEN or not TG_CHAT: return
    try:
        _pd = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}
        if str(TG_CHAT).startswith("-100"):
            _dt = env.get("TELEGRAM_DEFAULT_THREAD_ID")
            if _dt:
                _pd["message_thread_id"] = int(_dt)
        payload = json.dumps(_pd).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                                     data=payload, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f"  [TG] {e}")

def aget(path):
    req = urllib.request.Request(f"{BASE}{path}", headers=h)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r: return json.loads(r.read())

def fetch_account():
    try: return aget("/v2/account")
    except Exception as e: print(f"  [ACCT] {e}"); return {}

def fetch_positions():
    try: return aget("/v2/positions")
    except Exception as e: print(f"  [POS] {e}"); return []

print("=" * 55)
print("  PORTFOLIO MONITOR — OBSERVE ONLY (no auto-selling)")
print("  All auto-exit rules removed per operator 2026-04-16")
print("=" * 55)

equity_hwm = None

while True:
    try:
        now_et = datetime.now(timezone.utc) - timedelta(hours=4)
        hour, minute = now_et.hour, now_et.minute

        # Only monitor during market hours (9:30-16:00 ET)
        market_open = (hour > 9 or (hour == 9 and minute >= 30)) and hour < 16
        if not market_open:
            time.sleep(CHECK_INTERVAL_S)
            continue

        acct    = fetch_account()
        equity  = float(acct.get("equity", 0))
        cash    = float(acct.get("cash", 0))
        long_mv = float(acct.get("long_market_value", 0))
        now_str = now_et.strftime("%H:%M ET")
        print(f"  [{now_str}] equity=${equity:.2f}  positions=${long_mv:.2f}  cash=${cash:.2f}")

        # Track HWM for informational logging only — NO action taken
        if equity > 0:
            if equity_hwm is None or equity > equity_hwm:
                equity_hwm = equity

        time.sleep(CHECK_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        break
    except Exception as e:
        print(f"  [ERROR] {e}")
        time.sleep(30)
