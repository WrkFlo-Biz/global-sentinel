#!/usr/bin/env python3
"""Overnight Gap Strategy — tracks prev close → overnight → open for all assets."""
import json, os, datetime, sys, urllib.request
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data/quantum_feed"
CLOSE_PATH = QF / "prev_close.json"
GAP_PATH = QF / "overnight_gap_signals.json"
HISTORY_PATH = QF / "overnight_gap_history.jsonl"

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

SYMBOLS = ["SPY","QQQ","IWM","NVDA","TSLA","AMD","META","AAPL","AMZN","GOOGL","MSFT","PLTR","COIN",
           "XLE","XLF","XLK","XLV","XLI","XLU","XLY","XLP","XBI","TLT","TBT","SHY","IEF","HYG",
           "USO","GLD","SLV","UNG","GDX","UVXY","EEM","EWY","SOXL","TQQQ","OXY","MOS","DAL","JETS"]

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def alpaca_quotes(symbols):
    key = env.get("ALPACA_API_KEY_LIVE", "")
    secret = env.get("ALPACA_SECRET_KEY_LIVE", "")
    if not key: return {}
    syms = ",".join(symbols[:50])
    url = f"https://data.alpaca.markets/v2/stocks/quotes/latest?symbols={syms}"
    req = urllib.request.Request(url)
    req.add_header("APCA-API-KEY-ID", key)
    req.add_header("APCA-API-SECRET-KEY", secret)
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        quotes = {}
        for sym, q in data.get("quotes", {}).items():
            quotes[sym] = round((q["bp"] + q["ap"]) / 2, 2)
        return quotes
    except: return {}

def save_close():
    """Run at 4:05 PM ET — save closing prices."""
    print(f"[{iso_now()}] Saving close prices...")
    quotes = alpaca_quotes(SYMBOLS)
    data = {"timestamp": iso_now(), "type": "close", "prices": quotes}
    CLOSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLOSE_PATH.write_text(json.dumps(data, indent=2))
    print(f"Saved {len(quotes)} close prices")

def analyze_gap():
    """Run at 9:25 AM ET — compute overnight gaps."""
    print(f"[{iso_now()}] Analyzing overnight gaps...")
    prev = json.loads(CLOSE_PATH.read_text()) if CLOSE_PATH.exists() else {}
    prev_prices = prev.get("prices", {})
    current = alpaca_quotes(SYMBOLS)

    signals = []
    for sym in SYMBOLS:
        if sym not in prev_prices or sym not in current:
            continue
        prev_close = prev_prices[sym]
        now = current[sym]
        gap_pct = round((now - prev_close) / prev_close * 100, 3)

        if gap_pct > 0.5:
            gap_type = "gap_up"
            signal = "FOLLOW_GAP" if gap_pct > 1.5 else "FADE_GAP"
        elif gap_pct < -0.5:
            gap_type = "gap_down"
            signal = "FOLLOW_GAP" if gap_pct < -1.5 else "FADE_GAP"
        else:
            gap_type = "flat"
            signal = "NO_TRADE"

        signals.append({
            "symbol": sym, "prev_close": prev_close, "current": now,
            "gap_pct": gap_pct, "gap_type": gap_type, "signal": signal,
        })

    signals.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
    output = {"timestamp": iso_now(), "type": "gap_analysis", "signals": signals,
              "top_gaps": signals[:10], "gap_ups": [s for s in signals if s["gap_type"] == "gap_up"][:5],
              "gap_downs": [s for s in signals if s["gap_type"] == "gap_down"][:5]}
    GAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    GAP_PATH.write_text(json.dumps(output, indent=2))

    # Append to history
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps({"date": datetime.date.today().isoformat(), "gaps": signals[:20]}) + "\n")

    print(f"Analyzed {len(signals)} gaps. Top: {signals[0]['symbol']} {signals[0]['gap_pct']:+.2f}%" if signals else "No gaps")
    for s in signals[:5]:
        print(f"  {s['signal']:10s} {s['symbol']:6s} {s['gap_pct']:+.2f}%")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gap"
    if mode == "close":
        save_close()
    else:
        analyze_gap()
