import requests
import time
import sys

ALPACA_HEADERS = {
    "APCA-API-KEY-ID": "AKXM6W3IPXYEJUVO67ELCTEAHX",
    "APCA-API-SECRET-KEY": "C3tYHmaesMGmiRRdA1QunvVpivp1S8GBwyB2YjwnXt2d"
}
TELEGRAM_TOKEN = "8415413828:AAH557N2gqzJlvwtO3S8FBxGArSdFdNl9qI"
TELEGRAM_CHAT = "7091381625"
SYMBOL = "RKLB260417C00085000"
REFERENCE_PRICE = 0.90

def send_telegram(msg):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": msg})

def get_position():
    r = requests.get("https://api.alpaca.markets/v2/positions/" + SYMBOL, headers=ALPACA_HEADERS)
    if r.status_code == 200:
        return r.json()
    return None

def sell():
    r = requests.post("https://api.alpaca.markets/v2/orders", headers=ALPACA_HEADERS, json={
        "symbol": SYMBOL,
        "qty": "1",
        "side": "sell",
        "type": "market",
        "time_in_force": "day"
    })
    return r.status_code, r.json()

def main():
    send_telegram("[RKLB Gap Monitor] Script started. Waiting for market open to check gap...")

    # Check we still hold the position
    pos = get_position()
    if not pos:
        send_telegram("[RKLB Gap Monitor] No RKLB position found. Exiting.")
        sys.exit(0)

    # Wait a moment after open for price to populate
    time.sleep(15)

    # Re-check position with current price
    pos = get_position()
    if not pos:
        send_telegram("[RKLB Gap Monitor] Position gone. Exiting.")
        sys.exit(0)

    current_price = float(pos["current_price"])
    unrealized_pl = float(pos["unrealized_pl"])

    if current_price < REFERENCE_PRICE:
        # Gap down - sell immediately
        send_telegram(
            "[RKLB Gap Monitor] GAP DOWN detected!\n"
            "Reference: $" + format(REFERENCE_PRICE, ".2f") + "\n"
            "Open price: $" + format(current_price, ".2f") + "\n"
            "Executing sell..."
        )
        status, resp = sell()
        if status in (200, 201):
            send_telegram("[RKLB Gap Monitor] SELL order placed. Order ID: " + resp.get("id", "unknown"))
        else:
            send_telegram("[RKLB Gap Monitor] SELL FAILED: " + str(resp))
    else:
        send_telegram(
            "[RKLB Gap Monitor] No gap down.\n"
            "Reference: $" + format(REFERENCE_PRICE, ".2f") + "\n"
            "Current: $" + format(current_price, ".2f") + "\n"
            "Holding position. No action taken."
        )

if __name__ == "__main__":
    main()
