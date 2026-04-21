#!/usr/bin/env python3
"""Kalshi Macro/FOMC Bridge — prediction market probabilities for Fed decisions and macro events."""
import json, datetime, urllib.request

KALSHI_BASE = "https://api.elections.kalshi.com/v2"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def poll():
    results = {"timestamp": iso_now(), "markets": [], "fed_rate": {}, "macro": {}}
    # Get event markets
    for tag in ["fed-funds-rate", "gdp", "inflation", "recession"]:
        try:
            url = f"{KALSHI_BASE}/events?status=open&series_ticker={tag}&limit=10"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            for event in data.get("events", []):
                for market in event.get("markets", []):
                    entry = {
                        "title": market.get("title", ""),
                        "ticker": market.get("ticker", ""),
                        "yes_price": market.get("yes_bid", 0) / 100.0 if market.get("yes_bid") else 0,
                        "no_price": market.get("no_bid", 0) / 100.0 if market.get("no_bid") else 0,
                        "volume": market.get("volume", 0),
                        "category": tag,
                    }
                    results["markets"].append(entry)
                    if tag == "fed-funds-rate":
                        results["fed_rate"][market.get("title", "")] = entry["yes_price"]
                    else:
                        results["macro"][market.get("title", "")] = entry["yes_price"]
        except Exception as e:
            results[f"error_{tag}"] = str(e)[:200]
    return results

if __name__ == "__main__":
    print(json.dumps(poll(), indent=2))
