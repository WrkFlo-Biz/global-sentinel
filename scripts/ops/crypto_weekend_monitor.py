#!/usr/bin/env python3
"""
Weekend Crypto Monitor — Alerts on key level breaks for BTC, ETH, LINK.
Sends alerts via Telegram when thesis-supporting or thesis-breaking levels hit.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/opt/global-sentinel")

# --- Config ---
CHECK_INTERVAL = 120  # seconds between checks

# Thesis levels
LEVELS = {
    "BTC": {
        "symbol": "BTCUSD",
        "bullish_breakout": 67500,    # Breakout above consolidation
        "strong_breakout": 69000,     # Confirms rally
        "moon_target": 71000,         # Full thesis target
        "support_hold": 66500,        # Must hold
        "stop_loss": 65500,           # Thesis weakens
        "critical_break": 64000,      # Thesis invalidated
    },
    "ETH": {
        "symbol": "ETHUSD",
        "bullish_breakout": 2050,
        "strong_breakout": 2150,
        "moon_target": 2300,
        "support_hold": 2000,
        "stop_loss": 1970,
        "critical_break": 1940,
    },
    "LINK": {
        "symbol": "LINKUSD",
        "bullish_breakout": 9.00,
        "strong_breakout": 10.00,
        "moon_target": 12.00,
        "support_hold": 8.40,
        "stop_loss": 7.80,
        "critical_break": 7.50,
    },
}

# Track which alerts already fired
fired_alerts = set()

# --- Telegram ---
def send_telegram(message: str):
    """Send alert via mo2darkbot to Moses."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "8415413828:AAH557N2gqzJlvwtO3S8FBxGArSdFdNl9qI")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "7091381625")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"  [TG] Sent: {message[:80]}...")
    except Exception as e:
        print(f"  [TG ERR] {e}")


def get_prices() -> dict:
    """Fetch prices from CoinGecko free API."""
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,chainlink&vs_currencies=usd&include_24hr_change=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return {
            "BTC": {"price": data["bitcoin"]["usd"], "change_24h": data["bitcoin"].get("usd_24h_change", 0)},
            "ETH": {"price": data["ethereum"]["usd"], "change_24h": data["ethereum"].get("usd_24h_change", 0)},
            "LINK": {"price": data["chainlink"]["usd"], "change_24h": data["chainlink"].get("usd_24h_change", 0)},
        }
    except Exception as e:
        print(f"  [ERR] Price fetch failed: {e}")
        return None


def check_levels(prices: dict):
    """Check all levels and fire alerts."""
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    for asset, data in prices.items():
        price = data["price"]
        change = data["change_24h"]
        levels = LEVELS[asset]

        # --- BULLISH ALERTS (thesis confirming) ---
        if price >= levels["moon_target"]:
            key = f"{asset}_moon"
            if key not in fired_alerts:
                fired_alerts.add(key)
                send_telegram(
                    f"🚀 *{asset} MOON TARGET HIT* — ${price:,.2f}\n"
                    f"Target ${levels['moon_target']:,.2f} reached!\n"
                    f"24h: {change:+.1f}% | {now}\n"
                    f"Consider taking profits on swing position"
                )
        elif price >= levels["strong_breakout"]:
            key = f"{asset}_strong_break"
            if key not in fired_alerts:
                fired_alerts.add(key)
                send_telegram(
                    f"📈 *{asset} STRONG BREAKOUT* — ${price:,.2f}\n"
                    f"Broke above ${levels['strong_breakout']:,.2f}!\n"
                    f"24h: {change:+.1f}% | {now}\n"
                    f"Rally confirmed — trail stops, hold for target ${levels['moon_target']:,.2f}"
                )
        elif price >= levels["bullish_breakout"]:
            key = f"{asset}_bullish"
            if key not in fired_alerts:
                fired_alerts.add(key)
                send_telegram(
                    f"✅ *{asset} BULLISH BREAKOUT* — ${price:,.2f}\n"
                    f"Broke above ${levels['bullish_breakout']:,.2f} consolidation!\n"
                    f"24h: {change:+.1f}% | {now}\n"
                    f"Momentum building — consider adding from cash reserve"
                )

        # --- BEARISH ALERTS (thesis weakening) ---
        if price <= levels["critical_break"]:
            key = f"{asset}_critical"
            if key not in fired_alerts:
                fired_alerts.add(key)
                send_telegram(
                    f"🔴 *{asset} CRITICAL BREAK* — ${price:,.2f}\n"
                    f"Broke below ${levels['critical_break']:,.2f}!\n"
                    f"24h: {change:+.1f}% | {now}\n"
                    f"THESIS INVALIDATED — close position immediately"
                )
        elif price <= levels["stop_loss"]:
            key = f"{asset}_stop"
            if key not in fired_alerts:
                fired_alerts.add(key)
                send_telegram(
                    f"⚠️ *{asset} STOP LOSS ZONE* — ${price:,.2f}\n"
                    f"Approaching stop at ${levels['stop_loss']:,.2f}\n"
                    f"24h: {change:+.1f}% | {now}\n"
                    f"Tighten stops or reduce position"
                )
        elif price <= levels["support_hold"]:
            key = f"{asset}_support"
            if key not in fired_alerts:
                fired_alerts.add(key)
                send_telegram(
                    f"👀 *{asset} TESTING SUPPORT* — ${price:,.2f}\n"
                    f"At support ${levels['support_hold']:,.2f}\n"
                    f"24h: {change:+.1f}% | {now}\n"
                    f"Watch closely — if it holds, good entry to add"
                )


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Weekend Crypto Monitor starting...")
    print(f"  Checking every {CHECK_INTERVAL}s")
    print(f"  Levels: BTC ${LEVELS['BTC']['stop_loss']}-${LEVELS['BTC']['moon_target']}")
    print(f"          ETH ${LEVELS['ETH']['stop_loss']}-${LEVELS['ETH']['moon_target']}")
    print(f"          LINK ${LEVELS['LINK']['stop_loss']}-${LEVELS['LINK']['moon_target']}")

    # Initial price check + startup alert
    prices = get_prices()
    if prices:
        msg = (
            f"📊 *Weekend Crypto Monitor Started*\n\n"
            f"BTC: ${prices['BTC']['price']:,.0f} ({prices['BTC']['change_24h']:+.1f}%)\n"
            f"ETH: ${prices['ETH']['price']:,.2f} ({prices['ETH']['change_24h']:+.1f}%)\n"
            f"LINK: ${prices['LINK']['price']:,.2f} ({prices['LINK']['change_24h']:+.1f}%)\n\n"
            f"*Bullish targets:*\n"
            f"BTC: $67,500 → $69,000 → $71,000\n"
            f"ETH: $2,050 → $2,150 → $2,300\n"
            f"LINK: $9.00 → $10.00 → $12.00\n\n"
            f"*Stop levels:*\n"
            f"BTC: $65,500 | ETH: $1,970 | LINK: $7.80\n\n"
            f"Monitoring every 2 min. Alerts on key breaks."
        )
        send_telegram(msg)
        print(f"  BTC=${prices['BTC']['price']:,.0f}  ETH=${prices['ETH']['price']:,.2f}  LINK=${prices['LINK']['price']:,.2f}")

    cycle = 0
    while True:
        time.sleep(CHECK_INTERVAL)
        cycle += 1
        prices = get_prices()
        if not prices:
            continue

        check_levels(prices)

        # Periodic status update every 30 min
        if cycle % 15 == 0:
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"  [{now}] BTC=${prices['BTC']['price']:,.0f} ({prices['BTC']['change_24h']:+.1f}%)  "
                  f"ETH=${prices['ETH']['price']:,.2f} ({prices['ETH']['change_24h']:+.1f}%)  "
                  f"LINK=${prices['LINK']['price']:,.2f} ({prices['LINK']['change_24h']:+.1f}%)")


if __name__ == "__main__":
    main()
