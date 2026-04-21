#!/usr/bin/env python3
"""
Ranked Asset Allocation Model (RAAM)
=====================================
Based on Gioele Giordano's 2018 Charles H. Dow Award winning paper.
Sourced via @macro_quant_rick on Instagram.

Core idea: Rank 11 ETFs across 7 asset classes monthly using:
  1. (M) Absolute Momentum — 4-month ROC on daily returns
  2. (V) Volatility Model — GARCH-like volatility (10-day smoothed)
  3. (C) Avg Relative Correlation — 4-month avg cross-correlation
  4. (T) ATR Trend/Breakout System — trend filter

TOTAL RANK = (wM*Rank(M) + wV*Rank(V) + wC*Rank(C) - T) + M/x

Rules:
  - Top 5 ETFs by Total Rank are selected
  - Only those with positive Absolute Momentum are allocated
  - Negative momentum ETFs replaced with Cash (SHY)
  - Equal weight (20% each) among selected assets
  - Rebalance monthly on last trading day

7Twelve Universe: VV, IJH, IJR, EFA, EEM, RWR, DBC, VAW, AGG, TIP, IGOV, SHY

Writes: data/quantum_feed/ranked_allocation_signals.json
Appends: data/quantum_feed/ranked_allocation_history.jsonl
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
OUTPUT_PATH = QF / "ranked_allocation_signals.json"
HISTORY_PATH = QF / "ranked_allocation_history.jsonl"

# === 7TWELVE UNIVERSE ===
UNIVERSE = {
    "VV":   {"name": "Vanguard Large-Cap", "class": "US Equities"},
    "IJH":  {"name": "iShares S&P Mid-Cap", "class": "US Equities"},
    "IJR":  {"name": "iShares S&P Small-Cap", "class": "US Equities"},
    "EFA":  {"name": "iShares MSCI EAFE", "class": "Intl Equities"},
    "EEM":  {"name": "iShares MSCI EM", "class": "Intl Equities"},
    "RWR":  {"name": "SPDR DJ REIT", "class": "Real Estate"},
    "DBC":  {"name": "Invesco DB Commodity", "class": "Commodities"},
    "VAW":  {"name": "Vanguard Materials", "class": "Commodities"},
    "AGG":  {"name": "iShares Core US Bond", "class": "US Bonds"},
    "TIP":  {"name": "iShares TIPS Bond", "class": "US Bonds"},
    "IGOV": {"name": "iShares Intl Treasury", "class": "Intl Bonds"},
}
CASH_ETF = "SHY"
RANKABLE_SYMBOLS = list(UNIVERSE.keys())  # 11 ETFs (excluding SHY)

# Ranking weights (from paper — can be tuned)
W_MOMENTUM = 0.40
W_VOLATILITY = 0.30
W_CORRELATION = 0.20
# T (trend) is additive, not weighted
# x = 1000 (tiebreaker denominator for momentum)
X_TIEBREAK = 1000

# Lookback
MOMENTUM_DAYS = 84  # ~4 months
CORRELATION_DAYS = 84
VOLATILITY_SMOOTH = 10
ATR_PERIOD = 42
ATR_UPPER_LOOKBACK = 63
ATR_LOWER_LOOKBACK = 105
TOP_N = 5  # Select top 5 ETFs


def iso_now():
    return datetime.datetime.now(UTC).isoformat()


def et_now():
    return datetime.datetime.now(ET)


def log(msg):
    print(f"[{iso_now()}] RAAM: {msg}", flush=True)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# === COMPONENT 1: ABSOLUTE MOMENTUM ===

def calc_momentum(df, days=MOMENTUM_DAYS):
    """4-month ROC on daily close prices."""
    if len(df) < days + 1:
        return 0.0
    return float((df["Close"].iloc[-1] / df["Close"].iloc[-days - 1] - 1) * 100)


# === COMPONENT 2: VOLATILITY MODEL ===

def calc_volatility(df, smooth=VOLATILITY_SMOOTH):
    """Simplified GARCH-like volatility: EWMA of squared returns, 10-day smoothed."""
    if len(df) < 30:
        return 999.0  # High default = bad rank
    returns = df["Close"].pct_change().dropna()
    # EWMA volatility (span=smooth)
    ewma_var = returns.ewm(span=smooth).var()
    vol = float(np.sqrt(ewma_var.iloc[-1]) * np.sqrt(252) * 100)
    return vol


# === COMPONENT 3: AVG RELATIVE CORRELATION ===

def calc_avg_correlation(sym, all_returns, days=CORRELATION_DAYS):
    """Average correlation of this asset with all others over lookback period."""
    if sym not in all_returns.columns:
        return 1.0  # High default = bad rank
    recent = all_returns.tail(days)
    if len(recent) < 20:
        return 1.0
    corrs = []
    for other in recent.columns:
        if other != sym:
            c = recent[sym].corr(recent[other])
            if not np.isnan(c):
                corrs.append(abs(c))
    return float(np.mean(corrs)) if corrs else 1.0


# === COMPONENT 4: ATR TREND/BREAKOUT SYSTEM ===

def calc_atr_trend(df, atr_period=ATR_PERIOD, upper_lookback=ATR_UPPER_LOOKBACK, lower_lookback=ATR_LOWER_LOOKBACK):
    """ATR Trend/Breakout: +2 (long) or -2 (neutral/short)."""
    if len(df) < max(atr_period, upper_lookback, lower_lookback) + 5:
        return 0

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # ATR calculation
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()

    # Upper Band = ATR + Highest Close of upper_lookback periods
    upper_band = atr + close.rolling(upper_lookback).max()
    # Lower Band = ATR + Highest Low of lower_lookback periods
    lower_band = atr + low.rolling(lower_lookback).max()

    # Current signal
    last_high = float(high.iloc[-1])
    last_low = float(low.iloc[-1])
    last_upper = float(upper_band.iloc[-2]) if len(upper_band) > 1 else 0
    last_lower = float(lower_band.iloc[-2]) if len(lower_band) > 1 else 0

    if last_high > last_upper:
        return 2   # Long trend
    elif last_low < last_lower:
        return -2  # Neutral/Short
    else:
        return 0   # No signal


# === RANKING ENGINE ===

def rank_assets(data_dict, all_returns):
    """Rank all 11 assets and return top 5 allocation."""
    metrics = {}

    for sym in RANKABLE_SYMBOLS:
        if sym not in data_dict or data_dict[sym] is None or len(data_dict[sym]) < 30:
            continue
        df = data_dict[sym]
        m = calc_momentum(df)
        v = calc_volatility(df)
        c = calc_avg_correlation(sym, all_returns)
        t = calc_atr_trend(df)
        metrics[sym] = {"momentum": m, "volatility": v, "correlation": c, "trend": t}

    if len(metrics) < 5:
        log(f"Only {len(metrics)} assets with data — need at least 5")
        return None, metrics

    symbols = list(metrics.keys())

    # Rank Momentum: ascending (higher momentum = higher rank number = better)
    sorted_m = sorted(symbols, key=lambda s: metrics[s]["momentum"])
    for i, s in enumerate(sorted_m):
        metrics[s]["rank_m"] = i + 1

    # Rank Volatility: descending (lower vol = higher rank number = better)
    sorted_v = sorted(symbols, key=lambda s: metrics[s]["volatility"], reverse=True)
    for i, s in enumerate(sorted_v):
        metrics[s]["rank_v"] = i + 1

    # Rank Correlation: descending (lower corr = higher rank number = better)
    sorted_c = sorted(symbols, key=lambda s: metrics[s]["correlation"], reverse=True)
    for i, s in enumerate(sorted_c):
        metrics[s]["rank_c"] = i + 1

    # Calculate Total Rank
    for s in symbols:
        m = metrics[s]
        total = (W_MOMENTUM * m["rank_m"] +
                 W_VOLATILITY * m["rank_v"] +
                 W_CORRELATION * m["rank_c"] -
                 m["trend"] +
                 m["momentum"] / X_TIEBREAK)
        m["total_rank"] = round(total, 4)

    # Sort by total rank descending (higher = better)
    ranked = sorted(symbols, key=lambda s: metrics[s]["total_rank"], reverse=True)

    # Top 5
    top5 = ranked[:TOP_N]

    # Apply momentum filter: only include if positive momentum
    allocation = {}
    for s in top5:
        if metrics[s]["momentum"] > 0:
            allocation[s] = 20.0  # Equal weight 20%
        else:
            allocation[CASH_ETF] = allocation.get(CASH_ETF, 0) + 20.0

    return allocation, metrics


# === MAIN ENGINE ===

def run_ranked_allocation():
    """Run RAAM strategy. Returns master output dict."""
    log("Starting Ranked Asset Allocation Model (RAAM)...")
    now = et_now()

    # Fetch data for all assets (6 months to cover all lookbacks)
    all_symbols = RANKABLE_SYMBOLS + [CASH_ETF]
    data_dict = {}
    returns_dict = {}

    for sym in all_symbols:
        try:
            df = yf.Ticker(sym).history(period="6mo", interval="1d")
            if df is not None and len(df) > 0:
                data_dict[sym] = df
                returns_dict[sym] = df["Close"].pct_change().dropna()
                log(f"  {sym}: {len(df)} bars")
        except Exception as e:
            log(f"  {sym}: ERROR fetching - {e}")

    # Build returns DataFrame for correlation
    all_returns = pd.DataFrame(returns_dict)

    # Run ranking
    allocation, metrics = rank_assets(data_dict, all_returns)

    if allocation is None:
        log("Insufficient data for ranking")
        return {"error": "insufficient_data", "timestamp": iso_now()}

    # Build detailed output
    ranked_list = sorted(
        [(s, m) for s, m in metrics.items()],
        key=lambda x: x[1].get("total_rank", 0),
        reverse=True
    )

    master = {
        "timestamp": iso_now(),
        "session_date": now.strftime("%Y-%m-%d"),
        "source": "@macro_quant_rick (Giordano 2018 Charles Dow Award paper)",
        "method": "ranked_asset_allocation_model",
        "rebalance_freq": "monthly",
        "allocation": allocation,
        "cash_pct": allocation.get(CASH_ETF, 0),
        "invested_pct": 100 - allocation.get(CASH_ETF, 0),
        "selected_assets": [s for s in allocation if s != CASH_ETF],
        "full_ranking": [
            {
                "rank": i + 1,
                "symbol": s,
                "name": UNIVERSE.get(s, {}).get("name", s),
                "asset_class": UNIVERSE.get(s, {}).get("class", "Cash"),
                "momentum_4m": round(m["momentum"], 2),
                "volatility_ann": round(m["volatility"], 2),
                "avg_correlation": round(m["correlation"], 3),
                "trend_signal": m["trend"],
                "rank_m": m.get("rank_m", 0),
                "rank_v": m.get("rank_v", 0),
                "rank_c": m.get("rank_c", 0),
                "total_rank": m.get("total_rank", 0),
                "in_portfolio": s in allocation and s != CASH_ETF,
            }
            for i, (s, m) in enumerate(ranked_list)
        ],
    }

    save_json(OUTPUT_PATH, master)
    append_jsonl(HISTORY_PATH, {
        "timestamp": master["timestamp"],
        "session_date": master["session_date"],
        "allocation": allocation,
        "cash_pct": master["cash_pct"],
        "selected": master["selected_assets"],
    })

    log(f"RAAM complete: {len(master['selected_assets'])} assets selected, "
        f"{master['cash_pct']}% cash, {master['invested_pct']}% invested")

    return master


if __name__ == "__main__":
    master = run_ranked_allocation()
    if "error" not in master:
        print(f"\n{'='*60}")
        print(f"RANKED ASSET ALLOCATION — {master['session_date']}")
        print(f"{'='*60}")
        print(f"\nALLOCATION:")
        for sym, pct in master["allocation"].items():
            name = UNIVERSE.get(sym, {}).get("name", sym)
            print(f"  {sym:5s} ({name:30s}): {pct:.0f}%")
        print(f"\nFULL RANKING:")
        for r in master["full_ranking"]:
            sel = " <-- SELECTED" if r["in_portfolio"] else ""
            print(f"  #{r['rank']:2d} {r['symbol']:5s} | Mom={r['momentum_4m']:+6.1f}% "
                  f"Vol={r['volatility_ann']:5.1f}% Corr={r['avg_correlation']:.3f} "
                  f"Trend={r['trend_signal']:+d} | Total={r['total_rank']:.4f}{sel}")
