#!/usr/bin/env python3
"""Simulated Options P&L Tracker v2 — entry at 9:30 CST, polls every 3 minutes."""
import urllib.request, json, ssl, time, sys
from datetime import datetime, timezone, timedelta

REPO = "/opt/global-sentinel"
OUT = REPO + "/data/quantum_feed/sim_pnl.json"

env = {}
with open(REPO + "/.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"\t ")

h = {
    "APCA-API-KEY-ID": env.get("ALPACA_API_KEY_LIVE", ""),
    "APCA-API-SECRET-KEY": env.get("ALPACA_SECRET_KEY_LIVE", ""),
}
ctx = ssl.create_default_context()

# Entry prices estimated at 9:30 CST (10:30 ET) via delta-adjusted
# from daily open using underlying bars at 10:30 ET and current greeks
# Underlying at 9:30 CST: NVDA=201.83 AAPL=273.97 SPY=710.64 QQQ=653.73 XLE=56.70
positions = {
    "XLE260424C00057000":  {"name": "XLE 57C 4/24",     "entry": 0.28, "qty": 3, "cost": 84.0,   "thesis": "Oil momentum"},
    "NVDA260424C00205000": {"name": "NVDA 205C 4/24",   "entry": 0.72, "qty": 1, "cost": 72.0,   "thesis": "Smart money flow"},
    "NVDA260424C00207500": {"name": "NVDA 207.5C 4/24", "entry": 0.25, "qty": 2, "cost": 50.0,   "thesis": "OTM semi breakout"},
    "NVDA260424C00210000": {"name": "NVDA 210C 4/24",   "entry": 0.12, "qty": 6, "cost": 72.0,   "thesis": "Lottery ticket"},
    "SPY260424C00715000":  {"name": "SPY 715C 4/24",    "entry": 0.79, "qty": 1, "cost": 79.0,   "thesis": "Highest conf signal"},
    "QQQ260424C00660000":  {"name": "QQQ 660C 4/24",    "entry": 0.75, "qty": 1, "cost": 75.0,   "thesis": "Nasdaq momentum"},
    "AAPL260424C00277500": {"name": "AAPL 277.5C 4/24", "entry": 0.49, "qty": 1, "cost": 49.0,   "thesis": "Unusual flow"},
    "AAPL260424C00280000": {"name": "AAPL 280C 4/24",   "entry": 0.21, "qty": 2, "cost": 42.0,   "thesis": "Cheap OTM tech"},
}

underlyings = "NVDA,AAPL,SPY,QQQ,XLE"
POLL_INTERVAL = 180  # 3 minutes


def fetch_quotes():
    syms = ",".join(positions.keys())
    url = "https://data.alpaca.markets/v1beta1/options/quotes/latest?symbols=" + syms
    req = urllib.request.Request(url, headers=h)
    resp = urllib.request.urlopen(req, context=ctx)
    return json.loads(resp.read()).get("quotes", {})


def fetch_stock_quotes():
    url = "https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=" + underlyings
    req = urllib.request.Request(url, headers=h)
    resp = urllib.request.urlopen(req, context=ctx)
    return json.loads(resp.read()).get("quotes", {})


def run_snapshot():
    now = datetime.now(timezone(timedelta(hours=-5)))
    oq = fetch_quotes()
    sq = fetch_stock_quotes()

    results = []
    total_cost = 0
    total_value = 0
    total_pl = 0

    for sym, pos in positions.items():
        q = oq.get(sym, {})
        bid = float(q.get("bp", 0))
        ask = float(q.get("ap", 0))
        mid = round((bid + ask) / 2, 2) if bid and ask else 0
        value = round(mid * 100 * pos["qty"], 2)
        pl = round(value - pos["cost"], 2)
        pct = round((mid / pos["entry"] - 1) * 100, 1) if pos["entry"] else 0
        total_cost += pos["cost"]
        total_value += value
        total_pl += pl
        results.append({
            "symbol": sym,
            "name": pos["name"],
            "thesis": pos["thesis"],
            "entry": pos["entry"],
            "qty": pos["qty"],
            "cost": pos["cost"],
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "value": value,
            "pl": pl,
            "pct": pct,
        })

    stocks = {}
    for sym in underlyings.split(","):
        q = sq.get(sym, {})
        bp = float(q.get("bp", 0))
        ap = float(q.get("ap", 0))
        mid = round((bp + ap) / 2, 2)
        stocks[sym] = mid

    snapshot = {
        "timestamp": now.isoformat(),
        "entry_time": "2026-04-23T09:30:00-05:00",
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_pl": round(total_pl, 2),
        "total_pct": round((total_value / total_cost - 1) * 100, 1) if total_cost else 0,
        "positions": sorted(results, key=lambda x: x["pct"], reverse=True),
        "underlyings": stocks,
    }

    try:
        with open(OUT) as f:
            history = json.load(f)
    except Exception:
        history = []
    history.append(snapshot)
    with open(OUT, "w") as f:
        json.dump(history, f, indent=2)

    return snapshot


if __name__ == "__main__":
    if "--once" in sys.argv:
        snap = run_snapshot()
        print(json.dumps(snap, indent=2))
    else:
        print("Running sim tracker v2 every 3 minutes (entry=9:30 CST)...")
        while True:
            try:
                snap = run_snapshot()
                ts = snap["timestamp"]
                tpl = snap["total_pl"]
                tpct = snap["total_pct"]
                print(ts + " | total_pl=$" + str(tpl) + " (" + str(tpct) + "%)")
                for p in snap["positions"]:
                    nm = p["name"]
                    ppl = p["pl"]
                    ppct = p["pct"]
                    pmid = p["mid"]
                    print("  " + nm + ": $" + str(ppl) + " (" + str(ppct) + "%) mid=" + str(pmid))
            except Exception as e:
                print("Error: " + str(e))
            time.sleep(POLL_INTERVAL)
