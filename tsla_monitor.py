import os, json, requests, time
from dotenv import load_dotenv
load_dotenv("/opt/global-sentinel/.env")

live_key = os.environ.get("ALPACA_API_KEY_LIVE","")
live_secret = os.environ.get("ALPACA_SECRET_KEY_LIVE","")
alp_headers = {"APCA-API-KEY-ID": live_key, "APCA-API-SECRET-KEY": live_secret}
data_base = "https://data.alpaca.markets"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID","")

def send_tg(msg):
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}, timeout=5)
        except:
            pass

entry_price = 0.81
qty = 2
cost = entry_price * qty * 100
prev_price = None
alerted = set()

print("TSLA Monitor started. Entry: $0.81 x 2 contracts. Checking every 60s.", flush=True)
send_tg("TSLA Monitor Started\n2x $362.50P 0DTE @ $0.81\nChecking every 60s")

while True:
    try:
        snap = requests.get(data_base + "/v2/stocks/snapshots", headers=alp_headers,
            params={"symbols": "TSLA", "feed": "iex"}, timeout=10).json()
        tsla = snap.get("TSLA",{})
        price = tsla.get("latestTrade",{}).get("p",0)
        prev_close = tsla.get("prevDailyBar",{}).get("c",0)
        day_chg = ((price - prev_close)/prev_close*100) if prev_close else 0

        intrinsic = max(362.50 - price, 0)
        put_val = max(intrinsic, 0.01)
        position_val = put_val * qty * 100
        pl = position_val - cost
        pl_pct = (pl / cost * 100)

        ts = time.strftime("%H:%M:%S")
        line = "[" + ts + "] TSLA $" + "{:.2f}".format(price) + " (" + "{:+.2f}".format(day_chg) + "%) | Put ~$" + "{:.2f}".format(put_val) + " | P/L: $" + "{:+.0f}".format(pl) + " (" + "{:+.1f}".format(pl_pct) + "%)"
        print(line, flush=True)

        for lvl in [358, 355, 360, 362.50, 350]:
            if price <= lvl and lvl not in alerted:
                alerted.add(lvl)
                msg = "TSLA HIT $" + str(lvl) + "!\nPrice: $" + "{:.2f}".format(price) + "\nPut ~$" + "{:.2f}".format(put_val) + "\nP/L: $" + "{:+.0f}".format(pl) + " (" + "{:+.1f}".format(pl_pct) + "%)"
                send_tg(msg)
                print(">>> ALERT: " + msg, flush=True)

        if prev_price and price > prev_price + 1.0:
            msg = "TSLA BOUNCING: $" + "{:.2f}".format(prev_price) + " -> $" + "{:.2f}".format(price) + " (+$" + "{:.2f}".format(price-prev_price) + ")"
            send_tg(msg)

        if pl_pct > 100 and "100pct" not in alerted:
            alerted.add("100pct")
            send_tg("TSLA Put +100%! P/L: $" + "{:+.0f}".format(pl) + ". Consider taking profits.")

        prev_price = price

    except Exception as e:
        print("[" + time.strftime("%H:%M:%S") + "] Error: " + str(e), flush=True)

    time.sleep(60)
