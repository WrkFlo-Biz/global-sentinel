#!/usr/bin/env python3
"""
Global Sentinel — Portfolio Stop-Loss + EOD Monitor

- Checks every 30s during market hours
- Exits ALL positions if position market value <= $110
- Exits ALL positions at 3:55 PM ET regardless of price
- Cancels open orders before closing positions
- Pauses trading until operator approves resume
"""
import json, time, ssl
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

REPO_ROOT = Path("/opt/global-sentinel")
PAUSE_FILE = Path("/tmp/gs_trading_paused")
RESUME_FILE = Path("/tmp/gs_trading_resume")

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

STOP_LOSS_THRESHOLD = 110.00
CHECK_INTERVAL_S    = 30
ctx = ssl.create_default_context()

def _notifications_muted() -> bool:
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

def adelete(path):
    req = urllib.request.Request(f"{BASE}{path}", headers=h, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r: return r.read().decode()
    except Exception as e: return str(e)

def apost(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r: return json.loads(r.read())

def cancel_all_orders():
    try:
        orders = aget("/v2/orders?status=open")
        for o in orders:
            adelete(f"/v2/orders/{o['id']}")
        return len(orders)
    except Exception as e:
        print(f"  [CANCEL] {e}")
        return 0

def close_all_positions():
    cancelled = cancel_all_orders()
    if cancelled: time.sleep(2)
    resp = adelete("/v2/positions")
    time.sleep(2)
    return resp, cancelled

def fetch_account():
    try: return aget("/v2/account")
    except Exception as e: print(f"  [ACCT] {e}"); return {}

def fetch_positions():
    try: return aget("/v2/positions")
    except Exception as e: print(f"  [POS] {e}"); return []

print("=" * 55)
print("  STOP-LOSS + EOD MONITOR STARTED")
print(f"  Stop-loss: ${STOP_LOSS_THRESHOLD:.2f} | EOD exit: 3:55 PM ET")
print("=" * 55)

exit_executed = False
eod_executed  = False
paused_since  = None

while True:
    try:
        now_et = datetime.now(timezone.utc) - timedelta(hours=4)
        hour, minute = now_et.hour, now_et.minute

        # Reset EOD flag each new trading day
        if hour < 9:
            eod_executed = False

        # Handle resume
        if exit_executed or PAUSE_FILE.exists():
            if RESUME_FILE.exists():
                RESUME_FILE.unlink()
                if PAUSE_FILE.exists(): PAUSE_FILE.unlink()
                exit_executed = False
                eod_executed  = False
                paused_since  = None
                send_telegram("\u2705 <b>TRADING RESUMED</b>\n\nOperator approved. Monitoring resumed.")
                print("  [RESUME] Trading resumed")
            else:
                now_ts = time.time()
                if paused_since and (now_ts - paused_since) > 3600:
                    acct = fetch_account()
                    equity = float(acct.get("equity", 0))
                    send_telegram(f"\u23f8\ufe0f <b>TRADING STILL PAUSED</b>\n\nEquity: ${equity:.2f}\n\nTo resume: <code>touch {RESUME_FILE}</code>")
                    paused_since = now_ts
            time.sleep(CHECK_INTERVAL_S)
            continue

        # Only monitor during market hours (9:30–16:00 ET)
        market_open = (hour > 9 or (hour == 9 and minute >= 30)) and hour < 16
        if not market_open:
            time.sleep(CHECK_INTERVAL_S)
            continue

        acct     = fetch_account()
        equity   = float(acct.get("equity", 0))
        cash     = float(acct.get("cash", 0))
        long_mv  = float(acct.get("long_market_value", 0))
        now_str  = now_et.strftime("%H:%M ET")
        print(f"  [{now_str}] equity=${equity:.2f}  positions=${long_mv:.2f}  cash=${cash:.2f}")

        trigger_reason = None

        # EOD exit at 3:55 PM ET
        if hour == 15 and minute >= 55 and not eod_executed:
            trigger_reason = "eod"

        # Stop-loss trigger
        elif long_mv > 0 and long_mv <= STOP_LOSS_THRESHOLD:
            trigger_reason = "stop_loss"

        if trigger_reason:
            positions = fetch_positions()
            if positions or trigger_reason == "eod":
                if trigger_reason == "eod":
                    alert = (f"\ud83d\udd14 <b>END OF DAY — CLOSING ALL POSITIONS</b>\n\n"
                             f"Time: {now_str} — market closes at 16:00 ET\n"
                             f"Positions: {len(positions)} | Equity: ${equity:.2f}\nExiting now...")
                else:
                    alert = (f"\ud83d\udea8 <b>STOP-LOSS TRIGGERED</b>\n\n"
                             f"Position value ${long_mv:.2f} \u2264 ${STOP_LOSS_THRESHOLD:.2f}\n"
                             f"Equity: ${equity:.2f} | Cash: ${cash:.2f}\nExiting now...")
                send_telegram(alert)
                print(f"  [{trigger_reason.upper()}] Executing exit...")

                resp, cancelled = close_all_positions()

                acct2  = fetch_account()
                equity2 = float(acct2.get("equity", 0))
                cash2   = float(acct2.get("cash", 0))
                remaining = fetch_positions()

                confirm = (f"\u2705 <b>EXIT COMPLETE</b>\n\n"
                           f"Reason: {trigger_reason.upper()}\n"
                           f"Cancelled {cancelled} open order(s)\n"
                           f"Remaining positions: {len(remaining)}\n"
                           f"Equity: ${equity2:.2f} | Cash: ${cash2:.2f}\n\n"
                           "Balance held until you approve next trades.\n"
                           "<b>Next steps:</b> Reply here to discuss re-entry strategy.")
                send_telegram(confirm)
                print(f"  [DONE] equity=${equity2:.2f} cash=${cash2:.2f}")

                PAUSE_FILE.write_text(json.dumps({
                    "triggered_at": datetime.now(timezone.utc).isoformat(),
                    "reason": trigger_reason,
                    "equity_after": equity2,
                }))
                exit_executed = True
                if trigger_reason == "eod": eod_executed = True
                paused_since = time.time()

        time.sleep(CHECK_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        break
    except Exception as e:
        print(f"  [ERROR] {e}")
        time.sleep(30)
