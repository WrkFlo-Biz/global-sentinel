#!/usr/bin/env python3
"""
Pre-Session Chart Markup Strategy (No Fibonacci)
=================================================
Based on @adriannajones.official's approach: mark up charts before
each trading session using key structural levels — NO Fibonacci.

Method:
  1. Mark previous session highs/lows (daily, weekly, monthly)
  2. Identify horizontal support/resistance from swing pivots
  3. Mark psychological round-number levels
  4. Generate trade ideas at confluence zones

Writes: data/quantum_feed/chart_markup_levels.json
Appends: data/quantum_feed/chart_markup_history.jsonl
"""

import json, os, sys, datetime, traceback
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}. Install with: pip3 install yfinance pandas numpy")
    sys.exit(1)

import zoneinfo

ET = zoneinfo.ZoneInfo("America/New_York")
UTC = zoneinfo.ZoneInfo("UTC")

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"
MARKUP_PATH = QF / "chart_markup_levels.json"
MARKUP_HISTORY = QF / "chart_markup_history.jsonl"

# Default symbols — can be overridden via quantum_feed config
DEFAULT_SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "AMD", "META", "AAPL", "MSFT", "IWM", "XLE"]

# Pivot lookback for swing high/low detection
SWING_LOOKBACK = 5  # bars each side to confirm a swing point
MAX_SR_LEVELS = 6   # max support/resistance levels per symbol
ROUND_NUMBER_THRESHOLD = 0.005  # 0.5% proximity to round number counts


def iso_now():
    return datetime.datetime.now(UTC).isoformat()


def et_now():
    return datetime.datetime.now(ET)


def log(msg):
    print(f"[{iso_now()}] CHART-MARKUP: {msg}", flush=True)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


# === LEVEL DETECTION ===

def get_session_levels(df_daily):
    """Extract previous day, week, and month high/low/close."""
    levels = {}
    if df_daily is None or len(df_daily) < 2:
        return levels

    # Previous day
    prev = df_daily.iloc[-2]
    levels["prev_day_high"] = float(prev["High"])
    levels["prev_day_low"] = float(prev["Low"])
    levels["prev_day_close"] = float(prev["Close"])

    # Previous week (last 5 trading days excluding current)
    if len(df_daily) >= 6:
        week_slice = df_daily.iloc[-6:-1]
        levels["prev_week_high"] = float(week_slice["High"].max())
        levels["prev_week_low"] = float(week_slice["Low"].min())

    # Previous month (last ~21 trading days excluding current)
    if len(df_daily) >= 22:
        month_slice = df_daily.iloc[-22:-1]
        levels["prev_month_high"] = float(month_slice["High"].max())
        levels["prev_month_low"] = float(month_slice["Low"].min())

    return levels


def find_swing_pivots(df, lookback=SWING_LOOKBACK):
    """Find swing highs and lows using left/right bar comparison."""
    highs = []
    lows = []

    high_arr = df["High"].values
    low_arr = df["Low"].values

    for i in range(lookback, len(df) - lookback):
        # Swing high: bar's high is highest in window
        if high_arr[i] == max(high_arr[i - lookback:i + lookback + 1]):
            highs.append(float(high_arr[i]))
        # Swing low: bar's low is lowest in window
        if low_arr[i] == min(low_arr[i - lookback:i + lookback + 1]):
            lows.append(float(low_arr[i]))

    return highs, lows


def cluster_levels(levels, tolerance_pct=0.003):
    """Cluster nearby price levels and return the average of each cluster.
    Levels that are touched multiple times get higher weight."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = []
    current_cluster = [levels[0]]

    for lvl in levels[1:]:
        if abs(lvl - current_cluster[-1]) / current_cluster[-1] <= tolerance_pct:
            current_cluster.append(lvl)
        else:
            clusters.append({
                "price": round(sum(current_cluster) / len(current_cluster), 2),
                "touches": len(current_cluster),
                "strength": min(len(current_cluster) / 3.0, 1.0),
            })
            current_cluster = [lvl]

    # Last cluster
    clusters.append({
        "price": round(sum(current_cluster) / len(current_cluster), 2),
        "touches": len(current_cluster),
        "strength": min(len(current_cluster) / 3.0, 1.0),
    })

    # Sort by number of touches (strongest first)
    clusters.sort(key=lambda x: x["touches"], reverse=True)
    return clusters[:MAX_SR_LEVELS]


def get_round_number_levels(current_price):
    """Find nearby psychological round numbers."""
    levels = []
    # Determine rounding increment based on price magnitude
    if current_price > 500:
        increments = [50, 100]
    elif current_price > 100:
        increments = [10, 25, 50]
    elif current_price > 20:
        increments = [5, 10]
    else:
        increments = [1, 5]

    for inc in increments:
        base = round(current_price / inc) * inc
        for offset in [-2, -1, 0, 1, 2]:
            rn = base + offset * inc
            if rn > 0 and abs(rn - current_price) / current_price < 0.10:
                levels.append(round(rn, 2))

    return sorted(set(levels))


def classify_level(price, current_price):
    """Classify a level as support or resistance relative to current price."""
    if price < current_price * 0.998:
        return "support"
    elif price > current_price * 1.002:
        return "resistance"
    else:
        return "pivot"


def find_confluence_zones(session_levels, sr_clusters, round_levels, current_price, tolerance_pct=0.005):
    """Find zones where multiple level types converge — highest probability trades."""
    all_levels = []

    for name, price in session_levels.items():
        all_levels.append({"price": price, "source": name})
    for cl in sr_clusters:
        all_levels.append({"price": cl["price"], "source": "swing_pivot", "touches": cl["touches"]})
    for rn in round_levels:
        all_levels.append({"price": rn, "source": "round_number"})

    if not all_levels:
        return []

    all_levels.sort(key=lambda x: x["price"])

    # Find clusters of levels from different sources
    zones = []
    used = set()
    for i, lvl in enumerate(all_levels):
        if i in used:
            continue
        zone_members = [lvl]
        used.add(i)
        for j, other in enumerate(all_levels):
            if j in used:
                continue
            if abs(other["price"] - lvl["price"]) / lvl["price"] <= tolerance_pct:
                zone_members.append(other)
                used.add(j)

        if len(zone_members) >= 2:
            sources = list(set(m["source"] for m in zone_members))
            avg_price = round(sum(m["price"] for m in zone_members) / len(zone_members), 2)
            zones.append({
                "price": avg_price,
                "type": classify_level(avg_price, current_price),
                "confluence_count": len(zone_members),
                "sources": sources,
                "strength": round(min(len(zone_members) / 4.0, 1.0), 2),
            })

    zones.sort(key=lambda x: x["confluence_count"], reverse=True)
    return zones


def generate_trade_ideas(symbol, current_price, confluence_zones, session_levels):
    """Generate actionable trade ideas from confluence zones."""
    ideas = []
    for zone in confluence_zones:
        distance_pct = round((zone["price"] - current_price) / current_price * 100, 2)

        # Skip zones too far away (>5%)
        if abs(distance_pct) > 5.0:
            continue

        if zone["type"] == "support" and zone["confluence_count"] >= 2:
            ideas.append({
                "symbol": symbol,
                "direction": "long",
                "entry_zone": zone["price"],
                "entry_type": "limit_at_support",
                "stop_loss": round(zone["price"] * 0.99, 2),  # 1% below support
                "target": round(zone["price"] * 1.02, 2),     # 2% above entry
                "risk_reward": 2.0,
                "confluence": zone["confluence_count"],
                "sources": zone["sources"],
                "strength": zone["strength"],
                "distance_pct": distance_pct,
                "idea_type": "bounce_at_support",
            })
        elif zone["type"] == "resistance" and zone["confluence_count"] >= 2:
            ideas.append({
                "symbol": symbol,
                "direction": "short",
                "entry_zone": zone["price"],
                "entry_type": "limit_at_resistance",
                "stop_loss": round(zone["price"] * 1.01, 2),  # 1% above resistance
                "target": round(zone["price"] * 0.98, 2),     # 2% below entry
                "risk_reward": 2.0,
                "confluence": zone["confluence_count"],
                "sources": zone["sources"],
                "strength": zone["strength"],
                "distance_pct": distance_pct,
                "idea_type": "rejection_at_resistance",
            })

    ideas.sort(key=lambda x: x["confluence"], reverse=True)
    return ideas[:3]  # top 3 ideas per symbol


# === MAIN ENGINE ===

def markup_symbol(symbol):
    """Run full chart markup for a single symbol."""
    try:
        # Fetch daily data (3 months for swing pivots + session levels)
        ticker = yf.Ticker(symbol)
        df_daily = ticker.history(period="3mo", interval="1d")
        if df_daily is None or len(df_daily) < 10:
            return {"symbol": symbol, "error": "insufficient_data"}

        current_price = float(df_daily["Close"].iloc[-1])

        # 1. Session levels (prev day/week/month H/L)
        session_levels = get_session_levels(df_daily)

        # 2. Swing pivot support/resistance
        swing_highs, swing_lows = find_swing_pivots(df_daily)
        sr_clusters = cluster_levels(swing_highs + swing_lows)

        # 3. Round number levels
        round_levels = get_round_number_levels(current_price)

        # 4. Find confluence zones
        confluence_zones = find_confluence_zones(
            session_levels, sr_clusters, round_levels, current_price
        )

        # 5. Generate trade ideas
        trade_ideas = generate_trade_ideas(symbol, current_price, confluence_zones, session_levels)

        return {
            "symbol": symbol,
            "current_price": current_price,
            "session_levels": session_levels,
            "swing_sr_levels": sr_clusters,
            "round_number_levels": round_levels,
            "confluence_zones": confluence_zones,
            "trade_ideas": trade_ideas,
            "total_levels_marked": len(session_levels) + len(sr_clusters) + len(round_levels),
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def run_chart_markup(symbols=None):
    """Run chart markup for all symbols. Returns master output dict."""
    log("Starting pre-session chart markup (no fib)...")
    symbols = symbols or DEFAULT_SYMBOLS
    now = et_now()

    results = []
    all_ideas = []
    for sym in symbols:
        try:
            result = markup_symbol(sym)
            results.append(result)
            if "trade_ideas" in result:
                all_ideas.extend(result["trade_ideas"])
            log(f"  {sym}: {result.get('total_levels_marked', 0)} levels, "
                f"{len(result.get('trade_ideas', []))} ideas")
        except Exception as e:
            log(f"  {sym}: ERROR - {e}")
            results.append({"symbol": sym, "error": str(e)})

    # Sort all ideas by confluence strength
    all_ideas.sort(key=lambda x: (x["confluence"], x["strength"]), reverse=True)

    master = {
        "timestamp": iso_now(),
        "session_date": now.strftime("%Y-%m-%d"),
        "method": "structural_markup_no_fib",
        "source": "adriannajones.official",
        "symbols_analyzed": len(symbols),
        "symbols_ok": sum(1 for r in results if "error" not in r),
        "total_confluence_zones": sum(len(r.get("confluence_zones", [])) for r in results),
        "total_trade_ideas": len(all_ideas),
        "top_ideas": all_ideas[:5],
        "results": results,
    }

    save_json(MARKUP_PATH, master)
    append_jsonl(MARKUP_HISTORY, {
        "timestamp": master["timestamp"],
        "session_date": master["session_date"],
        "symbols": len(symbols),
        "zones": master["total_confluence_zones"],
        "ideas": master["total_trade_ideas"],
    })

    log(f"Chart markup complete: {master['symbols_ok']}/{len(symbols)} symbols, "
        f"{master['total_confluence_zones']} confluence zones, "
        f"{master['total_trade_ideas']} trade ideas")

    return master


if __name__ == "__main__":
    master = run_chart_markup()
    print(f"\n{'='*60}")
    print(f"CHART MARKUP SUMMARY — {master['session_date']}")
    print(f"{'='*60}")
    print(f"Symbols: {master['symbols_ok']}/{master['symbols_analyzed']}")
    print(f"Confluence zones: {master['total_confluence_zones']}")
    print(f"Trade ideas: {master['total_trade_ideas']}")
    if master["top_ideas"]:
        print(f"\nTOP IDEAS:")
        for idea in master["top_ideas"]:
            print(f"  {idea['symbol']} {idea['direction'].upper()} @ {idea['entry_zone']} "
                  f"({idea['idea_type']}, confluence={idea['confluence']}, "
                  f"sources={idea['sources']})")
