#!/usr/bin/env python3
"""Real-Time P&L Tracker — polls Alpaca paper account every 60s during market hours."""

import json
import os
import sys
import time
import signal
import requests
from datetime import datetime, timezone, timedelta
from threading import Timer

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

DOTENV = os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel") + "/.env"
STATE_FILE = "/tmp/gs_pnl_state.json"

# ── Load .env ──────────────────────────────────────────────────────
def load_env():
    if os.path.exists(DOTENV):
        with open(DOTENV) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_env()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7091381625")
ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE = "https://paper-api.alpaca.markets"
ALPACA_HEADERS = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

ACCOUNT_LABEL = os.environ.get("GS_PNL_ACCOUNT_LABEL", "Alpaca PAPER")
POLL_INTERVAL = int(os.environ.get("GS_PNL_POLL_INTERVAL", "60"))       # seconds
ALERT_THRESHOLD = float(os.environ.get("GS_PNL_ALERT_THRESHOLD", "150.0"))   # dollars
MOVE_ALERT_COOLDOWN_SECONDS = int(os.environ.get("GS_PNL_MOVE_ALERT_COOLDOWN_SEC", "900"))
ENABLE_MOVE_ALERTS = os.environ.get("GS_PNL_ENABLE_MOVE_ALERTS", "1") == "1"
ENABLE_POSITION_ALERTS = os.environ.get("GS_PNL_ENABLE_POSITION_ALERTS", "1") == "1"
ENABLE_DAILY_CLOSE_SUMMARY = os.environ.get("GS_PNL_ENABLE_DAILY_CLOSE_SUMMARY", "1") == "1"
BATCH_WINDOW = 30        # seconds

running = True

def handle_signal(sig, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# ── State management ───────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"positions": {}, "equity": 0, "last_alert_time": {}, "daily_close_sent": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ── Alpaca API ─────────────────────────────────────────────────────
def get_positions():
    try:
        resp = requests.get(f"{ALPACA_BASE}/v2/positions", headers=ALPACA_HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[WARN] positions fetch failed: {e}", file=sys.stderr)
        return None

def get_account():
    try:
        resp = requests.get(f"{ALPACA_BASE}/v2/account", headers=ALPACA_HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[WARN] account fetch failed: {e}", file=sys.stderr)
        return None

def get_clock():
    try:
        resp = requests.get(f"{ALPACA_BASE}/v2/clock", headers=ALPACA_HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[WARN] clock fetch failed: {e}", file=sys.stderr)
        return None

# ── Telegram ───────────────────────────────────────────────────────
alert_batch = []
batch_timer = None

def flush_batch():
    global alert_batch, batch_timer
    if not alert_batch:
        return
    message = "\n\n".join(alert_batch)
    alert_batch = []
    batch_timer = None
    send_telegram(message)

def queue_alert(text):
    global batch_timer
    alert_batch.append(text)
    if batch_timer is None:
        batch_timer = Timer(BATCH_WINDOW, flush_batch)
        batch_timer.daemon = True
        batch_timer.start()

def send_telegram(text):
    if _send_topic:
        try:
            _send_topic(text[:4000] if isinstance(text, str) else str(text)[:4000], topic="trading")
            return
        except Exception:
            pass
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            resp = requests.post(url, json={
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True, "message_thread_id": 74,
            }, timeout=15)
            if not resp.ok:
                print(f"[ERROR] Telegram: {resp.status_code} {resp.text}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Telegram send: {e}", file=sys.stderr)
        time.sleep(0.3)

# ── Core logic ─────────────────────────────────────────────────────
def check_positions(state):
    positions = get_positions()
    if positions is None:
        return state

    current = {}
    for p in positions:
        sym = p.get("symbol", "")
        current[sym] = {
            "qty": float(p.get("qty", 0)),
            "market_value": float(p.get("market_value", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
            "avg_entry": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "side": p.get("side", "long"),
        }

    prev = state.get("positions", {})

    if ENABLE_POSITION_ALERTS:
        # Detect new positions
        for sym, data in current.items():
            if sym not in prev:
                side = data["side"].upper()
                queue_alert(
                    f"<b>🆕 New Position</b>\n"
                    f"<b>Broker:</b> {ACCOUNT_LABEL}\n"
                    f"<b>{sym}</b> {side}  {data['qty']:.0f} shares\n"
                    f"Entry: ${data['avg_entry']:.2f}  Value: ${data['market_value']:,.2f}"
                )
    
        # Detect closed positions
        for sym, data in prev.items():
            if sym not in current:
                queue_alert(
                    f"<b>✅ Position Closed</b>\n"
                    f"<b>Broker:</b> {ACCOUNT_LABEL}\n"
                    f"<b>{sym}</b> — was {data.get('qty', 0):.0f} shares\n"
                    f"Last P&L: ${data.get('unrealized_pl', 0):+,.2f}"
                )
    
    # Detect significant P&L moves
    last_alerts = state.get("last_alert_time", {})
    now = time.time()
    for sym, data in current.items():
        if sym in prev:
            prev_pl = prev[sym].get("unrealized_pl", 0)
            curr_pl = data["unrealized_pl"]
            move = curr_pl - prev_pl
            # Only alert if move > threshold AND we haven't alerted this symbol in recently
            if not ENABLE_MOVE_ALERTS:
                continue
            if abs(move) >= ALERT_THRESHOLD:
                last_t = last_alerts.get(sym, 0)
                if now - last_t > MOVE_ALERT_COOLDOWN_SECONDS:
                    arrow = "📈" if move > 0 else "📉"
                    queue_alert(
                        f"<b>{arrow} P&L Move: {sym}</b>\n"
                        f"<b>Broker:</b> {ACCOUNT_LABEL}\n"
                        f"Change: ${move:+,.2f}  Total P&L: ${curr_pl:+,.2f}\n"
                        f"Price: ${data['current_price']:.2f}  ({data['unrealized_plpc']*100:+.2f}%)"
                    )
                    last_alerts[sym] = now

    state["positions"] = current
    state["last_alert_time"] = last_alerts
    return state

def send_daily_close_summary(state):
    """Send EOD summary at market close."""
    account = get_account()
    if not account:
        return state

    equity = float(account.get("equity", 0))
    last_equity = float(account.get("last_equity", 0))
    day_change = equity - last_equity
    day_pct = (day_change / last_equity * 100) if last_equity else 0
    buying_power = float(account.get("buying_power", 0))

    positions = state.get("positions", {})
    pos_lines = []
    total_pl = 0
    for sym, data in sorted(positions.items(), key=lambda x: abs(x[1].get("unrealized_pl", 0)), reverse=True):
        pl = data.get("unrealized_pl", 0)
        total_pl += pl
        arrow = "🟢" if pl >= 0 else "🔴"
        pos_lines.append(f"  {arrow} <b>{sym}</b>  ${pl:+,.2f}  ({data.get('unrealized_plpc', 0)*100:+.2f}%)")

    pos_str = "\n".join(pos_lines) if pos_lines else "  No open positions"
    day_arrow = "▲" if day_change >= 0 else "▼"

    msg = (
        f"<b>═══ DAILY CLOSE SUMMARY ═══</b>\n\n"
        f"<b>💰 Equity:</b> ${equity:,.2f}  {day_arrow} ${day_change:+,.2f} ({day_pct:+.2f}%)\n"
        f"<b>Buying Power:</b> ${buying_power:,.2f}\n\n"
        f"<b>Open Positions ({len(positions)}):</b>\n{pos_str}\n"
        f"<b>Total Unrealized P&L:</b> ${total_pl:+,.2f}"
    )
    send_telegram(msg)

    prev_equity = state.get("equity", 0)
    state["equity"] = equity
    return state

# ── Main loop ──────────────────────────────────────────────────────
def main():
    print("[INFO] P&L Tracker starting...", file=sys.stderr)
    state = load_state()
    close_summary_sent_today = state.get("daily_close_sent")

    while running:
        try:
            clock = get_clock()
            if clock is None:
                time.sleep(POLL_INTERVAL)
                continue

            is_open = clock.get("is_open", False)
            next_close = clock.get("next_close", "")

            # Parse today's date for close-summary tracking
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if is_open:
                state = check_positions(state)
                save_state(state)
                # Reset close summary flag when market is open
                if close_summary_sent_today == today_str:
                    pass  # Already sent today, market might reopen from halt

            else:
                # Market closed — send daily close summary once
                if close_summary_sent_today != today_str and state.get("positions"):
                    # Check if we're after close (not before open)
                    now_utc = datetime.now(timezone.utc)
                    if now_utc.hour >= 20 or (now_utc.hour == 20 and now_utc.minute >= 0):
                        state = send_daily_close_summary(state)
                        close_summary_sent_today = today_str
                        state["daily_close_sent"] = today_str
                        save_state(state)

                # Flush any remaining alerts
                flush_batch()

        except Exception as e:
            print(f"[ERROR] Main loop: {e}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)

    # Graceful shutdown
    flush_batch()
    print("[INFO] P&L Tracker stopped.", file=sys.stderr)

if __name__ == "__main__":
    main()
