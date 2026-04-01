#!/usr/bin/env python3
"""
Power Market Trading Strategy (3 Approaches)
=============================================
Based on Neel Somani (@neelsalami), former Citadel quant.
Three ways to trade power/electricity markets:

1. BASIS TRADING — trade the spread between two locations/nodes
2. DIRECTIONAL TRADING — take a view on price direction using
   weather, grid conditions, fuel prices
3. VIRTUAL TRADING (DART) — Day-Ahead vs Real-Time spread

For retail traders: implemented via power/utility ETFs, energy futures,
and related equities as proxies.

Writes: data/quantum_feed/power_market_signals.json
Appends: data/quantum_feed/power_market_history.jsonl
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
OUTPUT_PATH = QF / "power_market_signals.json"
HISTORY_PATH = QF / "power_market_history.jsonl"

# === INSTRUMENT UNIVERSE ===
# Proxies for power market exposure (retail-accessible)
POWER_PROXIES = {
    "utilities": ["XLU", "UTG", "BEP"],           # Utility sector ETFs
    "clean_energy": ["ICLN", "TAN", "QCLN"],       # Renewables (duck curve plays)
    "nat_gas": ["UNG", "BOIL", "KOLD"],             # Natural gas (fuel price input)
    "oil": ["USO", "XLE", "XOP"],                   # Oil (generation cost driver)
    "grid_infra": ["GRID", "PAVE"],                 # Grid infrastructure
    "nuclear": ["URA", "URNM", "NLR"],              # Nuclear (baseload)
    "independent_power": ["VST", "CEG", "NRG", "AES", "SO"],  # IPPs
}

# Key driver symbols for signal generation
DRIVER_SYMBOLS = ["UNG", "XLU", "VST", "CEG", "NRG", "USO", "TAN"]


def iso_now():
    return datetime.datetime.now(UTC).isoformat()


def et_now():
    return datetime.datetime.now(ET)


def log(msg):
    print(f"[{iso_now()}] POWER-MKT: {msg}", flush=True)


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


# === STRATEGY 1: BASIS TRADING ===
# Trade the spread between utility subsectors / regional power producers
# When congestion increases, spread between generators and utilities widens

def analyze_basis_spread(period="1mo"):
    """Analyze spread between independent power producers and utility ETFs."""
    signals = []
    try:
        # IPPs (generators) vs Utilities (distributors)
        ipp_symbols = ["VST", "CEG", "NRG"]
        util_symbols = ["XLU", "SO", "AES"]

        ipp_data = {}
        util_data = {}

        for sym in ipp_symbols:
            df = yf.Ticker(sym).history(period=period, interval="1d")
            if df is not None and len(df) > 5:
                ipp_data[sym] = df

        for sym in util_symbols:
            df = yf.Ticker(sym).history(period=period, interval="1d")
            if df is not None and len(df) > 5:
                util_data[sym] = df

        if not ipp_data or not util_data:
            return signals

        # Calculate relative performance (IPP vs Utility)
        ipp_returns = []
        for sym, df in ipp_data.items():
            ret_5d = (df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1) * 100 if len(df) >= 6 else 0
            ret_20d = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
            ipp_returns.append({"symbol": sym, "ret_5d": ret_5d, "ret_20d": ret_20d})

        util_returns = []
        for sym, df in util_data.items():
            ret_5d = (df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1) * 100 if len(df) >= 6 else 0
            ret_20d = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
            util_returns.append({"symbol": sym, "ret_5d": ret_5d, "ret_20d": ret_20d})

        avg_ipp_5d = np.mean([r["ret_5d"] for r in ipp_returns])
        avg_util_5d = np.mean([r["ret_5d"] for r in util_returns])
        avg_ipp_20d = np.mean([r["ret_20d"] for r in ipp_returns])
        avg_util_20d = np.mean([r["ret_20d"] for r in util_returns])

        spread_5d = round(avg_ipp_5d - avg_util_5d, 2)
        spread_20d = round(avg_ipp_20d - avg_util_20d, 2)

        # Signal: when spread is extreme, mean reversion likely
        if spread_5d > 3.0:
            signals.append({
                "strategy": "basis_mean_reversion",
                "signal": "short_ipp_long_util",
                "rationale": f"IPP outperforming utilities by {spread_5d}% (5d) — spread likely to compress",
                "long": ["XLU", "SO"],
                "short": ["VST", "CEG"],
                "spread_5d": spread_5d,
                "spread_20d": spread_20d,
                "confidence": min(abs(spread_5d) / 5.0, 1.0),
            })
        elif spread_5d < -3.0:
            signals.append({
                "strategy": "basis_mean_reversion",
                "signal": "long_ipp_short_util",
                "rationale": f"Utilities outperforming IPPs by {abs(spread_5d)}% (5d) — spread likely to widen",
                "long": ["VST", "CEG", "NRG"],
                "short": ["XLU"],
                "spread_5d": spread_5d,
                "spread_20d": spread_20d,
                "confidence": min(abs(spread_5d) / 5.0, 1.0),
            })

        # Trend signal: sustained spread direction
        if spread_20d > 5.0 and spread_5d > 0:
            signals.append({
                "strategy": "basis_trend",
                "signal": "long_ipp_momentum",
                "rationale": f"IPPs in sustained outperformance ({spread_20d}% over 20d) — power demand/pricing strength",
                "long": ["VST", "CEG", "NRG"],
                "spread_5d": spread_5d,
                "spread_20d": spread_20d,
                "confidence": min(spread_20d / 10.0, 1.0),
            })

        return signals
    except Exception as e:
        log(f"Basis analysis error: {e}")
        return signals


# === STRATEGY 2: DIRECTIONAL TRADING ===
# Take a view on power price direction using fuel costs + sector momentum

def analyze_directional(period="3mo"):
    """Directional view on power prices via fuel costs and sector momentum."""
    signals = []
    try:
        # Natural gas is the marginal fuel — drives power prices
        ung = yf.Ticker("UNG").history(period=period, interval="1d")
        if ung is None or len(ung) < 20:
            return signals

        ung_price = float(ung["Close"].iloc[-1])
        ung_sma20 = float(ung["Close"].rolling(20).mean().iloc[-1])
        ung_sma50 = float(ung["Close"].rolling(50).mean().iloc[-1]) if len(ung) >= 50 else ung_sma20
        ung_ret_5d = (ung["Close"].iloc[-1] / ung["Close"].iloc[-6] - 1) * 100 if len(ung) >= 6 else 0
        ung_ret_20d = (ung["Close"].iloc[-1] / ung["Close"].iloc[-21] - 1) * 100 if len(ung) >= 21 else 0

        # Power producer momentum (VST is the purest play)
        vst = yf.Ticker("VST").history(period=period, interval="1d")
        vst_price = float(vst["Close"].iloc[-1]) if vst is not None and len(vst) > 0 else 0
        vst_sma20 = float(vst["Close"].rolling(20).mean().iloc[-1]) if vst is not None and len(vst) >= 20 else vst_price

        # Rising nat gas = rising power prices = bullish IPPs
        if ung_price > ung_sma20 and ung_ret_5d > 2.0:
            signals.append({
                "strategy": "directional_fuel_driven",
                "signal": "long_power_producers",
                "rationale": f"Nat gas rising ({ung_ret_5d:+.1f}% 5d, above SMA20) — power prices follow fuel costs higher",
                "long": ["VST", "CEG", "NRG", "BOIL"],
                "fuel_trend": "rising",
                "ung_vs_sma20": round((ung_price / ung_sma20 - 1) * 100, 2),
                "confidence": min(abs(ung_ret_5d) / 5.0, 1.0),
            })
        elif ung_price < ung_sma20 and ung_ret_5d < -2.0:
            signals.append({
                "strategy": "directional_fuel_driven",
                "signal": "short_power_or_long_renewables",
                "rationale": f"Nat gas falling ({ung_ret_5d:+.1f}% 5d, below SMA20) — renewables become relatively cheaper",
                "long": ["TAN", "ICLN"],
                "short": ["KOLD"],  # Inverse nat gas
                "fuel_trend": "falling",
                "ung_vs_sma20": round((ung_price / ung_sma20 - 1) * 100, 2),
                "confidence": min(abs(ung_ret_5d) / 5.0, 1.0),
            })

        # Golden cross / death cross on nat gas
        if len(ung) >= 50:
            if ung_sma20 > ung_sma50 and float(ung["Close"].rolling(20).mean().iloc[-2]) <= ung_sma50:
                signals.append({
                    "strategy": "directional_golden_cross",
                    "signal": "nat_gas_golden_cross",
                    "rationale": "Nat gas SMA20 crossed above SMA50 — bullish fuel trend starting",
                    "long": ["UNG", "VST", "CEG"],
                    "confidence": 0.7,
                })

        return signals
    except Exception as e:
        log(f"Directional analysis error: {e}")
        return signals


# === STRATEGY 3: VIRTUAL / DART TRADING ===
# Day-Ahead vs Real-Time spread proxy — trade volatility in power names
# High volatility = wider DART spreads = opportunity

def analyze_virtual_dart(period="1mo"):
    """Virtual/DART proxy — trade power name volatility regimes."""
    signals = []
    try:
        # Use realized volatility of power producers as DART proxy
        symbols = ["VST", "CEG", "NRG"]
        vol_data = {}

        for sym in symbols:
            df = yf.Ticker(sym).history(period=period, interval="1d")
            if df is not None and len(df) >= 20:
                returns = df["Close"].pct_change().dropna()
                vol_5d = float(returns.tail(5).std() * np.sqrt(252) * 100)
                vol_20d = float(returns.tail(20).std() * np.sqrt(252) * 100)
                vol_data[sym] = {
                    "vol_5d": round(vol_5d, 1),
                    "vol_20d": round(vol_20d, 1),
                    "vol_ratio": round(vol_5d / vol_20d, 2) if vol_20d > 0 else 1.0,
                    "current_price": float(df["Close"].iloc[-1]),
                }

        if not vol_data:
            return signals

        avg_vol_ratio = np.mean([v["vol_ratio"] for v in vol_data.values()])

        # High vol regime = DART spreads widening = opportunities
        if avg_vol_ratio > 1.3:
            signals.append({
                "strategy": "dart_high_vol",
                "signal": "power_vol_expanding",
                "rationale": f"Power name vol expanding (ratio {avg_vol_ratio:.2f}x) — DART-like spreads widening, trade mean reversion",
                "action": "sell_vol_on_spikes",
                "symbols": vol_data,
                "avg_vol_ratio": round(avg_vol_ratio, 2),
                "confidence": min((avg_vol_ratio - 1.0) / 0.5, 1.0),
            })
        elif avg_vol_ratio < 0.7:
            signals.append({
                "strategy": "dart_low_vol",
                "signal": "power_vol_compressing",
                "rationale": f"Power name vol compressing (ratio {avg_vol_ratio:.2f}x) — calm regime, buy breakouts",
                "action": "buy_breakouts",
                "symbols": vol_data,
                "avg_vol_ratio": round(avg_vol_ratio, 2),
                "confidence": min((1.0 - avg_vol_ratio) / 0.5, 1.0),
            })

        return signals
    except Exception as e:
        log(f"DART analysis error: {e}")
        return signals


# === MAIN ENGINE ===

def run_power_market():
    """Run all three power market strategies. Returns master output dict."""
    log("Starting power market analysis (3 strategies)...")
    now = et_now()

    all_signals = []
    strategy_results = {}

    # 1. Basis Trading
    try:
        basis_signals = analyze_basis_spread()
        strategy_results["basis"] = {"signals": basis_signals, "count": len(basis_signals)}
        all_signals.extend(basis_signals)
        log(f"  Basis: {len(basis_signals)} signals")
    except Exception as e:
        log(f"  Basis: ERROR - {e}")
        strategy_results["basis"] = {"error": str(e)}

    # 2. Directional Trading
    try:
        directional_signals = analyze_directional()
        strategy_results["directional"] = {"signals": directional_signals, "count": len(directional_signals)}
        all_signals.extend(directional_signals)
        log(f"  Directional: {len(directional_signals)} signals")
    except Exception as e:
        log(f"  Directional: ERROR - {e}")
        strategy_results["directional"] = {"error": str(e)}

    # 3. Virtual/DART
    try:
        dart_signals = analyze_virtual_dart()
        strategy_results["dart"] = {"signals": dart_signals, "count": len(dart_signals)}
        all_signals.extend(dart_signals)
        log(f"  DART: {len(dart_signals)} signals")
    except Exception as e:
        log(f"  DART: ERROR - {e}")
        strategy_results["dart"] = {"error": str(e)}

    master = {
        "timestamp": iso_now(),
        "session_date": now.strftime("%Y-%m-%d"),
        "source": "neelsalami (Neel Somani, former Citadel quant)",
        "method": "power_market_3_strategies",
        "strategies": strategy_results,
        "total_signals": len(all_signals),
        "all_signals": all_signals,
        "instrument_universe": POWER_PROXIES,
    }

    save_json(OUTPUT_PATH, master)
    append_jsonl(HISTORY_PATH, {
        "timestamp": master["timestamp"],
        "session_date": master["session_date"],
        "total_signals": len(all_signals),
        "basis": len(strategy_results.get("basis", {}).get("signals", [])),
        "directional": len(strategy_results.get("directional", {}).get("signals", [])),
        "dart": len(strategy_results.get("dart", {}).get("signals", [])),
    })

    log(f"Power market analysis complete: {len(all_signals)} total signals")
    return master


if __name__ == "__main__":
    master = run_power_market()
    print(f"\n{'='*60}")
    print(f"POWER MARKET STRATEGY — {master['session_date']}")
    print(f"{'='*60}")
    print(f"Total signals: {master['total_signals']}")
    for sig in master["all_signals"]:
        print(f"  [{sig['strategy']}] {sig['signal']}: {sig['rationale'][:80]}...")
