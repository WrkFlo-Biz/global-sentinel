#!/usr/bin/env python3
"""
AMD Phase Detector — Accumulation, Manipulation, Distribution
Based on ICT market structure theory (Raghee Horner / ICT concepts).

Detects which phase the market is in for a given symbol:
- ACCUMULATION: Low volume consolidation, building a range
- MANIPULATION: Stop hunt / fake breakout above/below range
- DISTRIBUTION: Real expansion move, trend follow-through

Integrates with session intelligence for time-of-day context.
"""
import json, os, datetime, urllib.request
import numpy as np
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/amd_phase.json"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] AMD: {msg}", flush=True)

def get_intraday_bars(symbol, key, secret):
    """Get today intraday 5-min bars."""
    try:
        today = datetime.date.today().isoformat()
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe=5Min&start={today}T04:00:00Z&limit=200&sort=asc"
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", key)
        req.add_header("APCA-API-SECRET-KEY", secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("bars", {}).get(symbol, [])
    except:
        return []

def detect_range(bars, lookback=12):
    """Detect if price is in a consolidation range (accumulation phase)."""
    if len(bars) < lookback:
        return None
    recent = bars[-lookback:]
    highs = [b["h"] for b in recent]
    lows = [b["l"] for b in recent]
    range_high = max(highs)
    range_low = min(lows)
    range_size = range_high - range_low
    avg_price = (range_high + range_low) / 2
    range_pct = range_size / avg_price * 100

    # Consolidation = tight range relative to average
    is_consolidating = range_pct < 0.5  # Less than 0.5% range = tight

    # Volume declining = accumulation signal
    volumes = [b["v"] for b in recent]
    vol_trend = np.polyfit(range(len(volumes)), volumes, 1)[0]
    declining_volume = vol_trend < 0

    return {
        "range_high": round(range_high, 2),
        "range_low": round(range_low, 2),
        "range_pct": round(range_pct, 3),
        "is_consolidating": is_consolidating,
        "declining_volume": declining_volume,
        "avg_volume": int(np.mean(volumes)),
    }

def detect_stop_hunt(bars, range_info):
    """Detect manipulation — price breaks range then reverses back inside."""
    if not range_info or len(bars) < 3:
        return None

    rh = range_info["range_high"]
    rl = range_info["range_low"]

    # Check last 3-6 bars for a wick above/below range that reversed
    recent = bars[-6:]
    hunts = []

    for i, bar in enumerate(recent):
        # Hunt above range (bull trap)
        if bar["h"] > rh and bar["c"] < rh:
            hunts.append({
                "type": "bull_trap",
                "direction": "bearish_after",
                "wick_high": round(bar["h"], 2),
                "close": round(bar["c"], 2),
                "range_high": round(rh, 2),
                "overshoot_pct": round((bar["h"] - rh) / rh * 100, 3),
            })
        # Hunt below range (bear trap)
        if bar["l"] < rl and bar["c"] > rl:
            hunts.append({
                "type": "bear_trap",
                "direction": "bullish_after",
                "wick_low": round(bar["l"], 2),
                "close": round(bar["c"], 2),
                "range_low": round(rl, 2),
                "overshoot_pct": round((rl - bar["l"]) / rl * 100, 3),
            })

    return hunts if hunts else None

def detect_expansion(bars, range_info):
    """Detect distribution — sustained break outside the range with volume."""
    if not range_info or len(bars) < 3:
        return None

    rh = range_info["range_high"]
    rl = range_info["range_low"]
    avg_vol = range_info["avg_volume"]

    recent = bars[-3:]
    last = recent[-1]

    # Bullish expansion: close above range with increasing volume
    if last["c"] > rh and last["v"] > avg_vol * 1.5:
        return {
            "type": "bullish_expansion",
            "direction": "long",
            "breakout_price": round(rh, 2),
            "current_price": round(last["c"], 2),
            "expansion_pct": round((last["c"] - rh) / rh * 100, 3),
            "volume_ratio": round(last["v"] / max(1, avg_vol), 2),
            "confirmed": last["v"] > avg_vol * 2,
        }
    # Bearish expansion
    if last["c"] < rl and last["v"] > avg_vol * 1.5:
        return {
            "type": "bearish_expansion",
            "direction": "short",
            "breakdown_price": round(rl, 2),
            "current_price": round(last["c"], 2),
            "expansion_pct": round((rl - last["c"]) / rl * 100, 3),
            "volume_ratio": round(last["v"] / max(1, avg_vol), 2),
            "confirmed": last["v"] > avg_vol * 2,
        }
    return None

def classify_phase(range_info, hunts, expansion):
    """Classify current AMD phase."""
    if expansion and expansion.get("confirmed"):
        return "DISTRIBUTION", expansion["direction"], 0.9
    if expansion:
        return "DISTRIBUTION_EARLY", expansion["direction"], 0.7
    if hunts:
        latest_hunt = hunts[-1]
        return "MANIPULATION", latest_hunt["direction"], 0.8
    if range_info and range_info["is_consolidating"]:
        return "ACCUMULATION", "neutral", 0.6
    return "UNKNOWN", "neutral", 0.3

def run(symbols=None):
    """Run AMD phase detection on watchlist symbols."""
    if symbols is None:
        symbols = ["SPY", "QQQ", "TSLA", "NVDA", "AMD", "META", "XLE", "USO"]

    env = {}
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v

    key = env.get("ALPACA_API_KEY_LIVE", "")
    secret = env.get("ALPACA_SECRET_KEY_LIVE", "")
    if not key or not secret:
        log("No API keys")
        return

    results = []
    for sym in symbols:
        bars = get_intraday_bars(sym, key, secret)
        if len(bars) < 12:
            continue

        range_info = detect_range(bars)
        hunts = detect_stop_hunt(bars, range_info)
        expansion = detect_expansion(bars, range_info)
        phase, direction, confidence = classify_phase(range_info, hunts, expansion)

        entry = {
            "symbol": sym,
            "phase": phase,
            "direction": direction,
            "confidence": confidence,
            "range": range_info,
            "stop_hunts": hunts,
            "expansion": expansion,
            "bars_analyzed": len(bars),
        }
        results.append(entry)
        log(f"{sym:6s} Phase={phase:20s} Dir={direction:8s} Conf={confidence:.1f}")

    # Trading signals based on AMD
    signals = []
    for r in results:
        if r["phase"] == "MANIPULATION" and r["stop_hunts"]:
            hunt = r["stop_hunts"][-1]
            signals.append({
                "symbol": r["symbol"],
                "signal": f"FADE_{hunt['type'].upper()}",
                "direction": hunt["direction"],
                "description": f"Stop hunt detected at {r['symbol']} — fade the fake breakout",
                "confidence": 0.8,
            })
        elif r["phase"].startswith("DISTRIBUTION") and r["expansion"]:
            signals.append({
                "symbol": r["symbol"],
                "signal": "FOLLOW_EXPANSION",
                "direction": r["expansion"]["direction"],
                "description": f"Confirmed expansion at {r['symbol']} — follow the trend",
                "confidence": r["expansion"].get("volume_ratio", 1) / 3,
            })

    output = {
        "timestamp": iso_now(),
        "phases": results,
        "signals": signals,
        "scenario_templates": {
            "accumulation_to_manipulation": "When multiple symbols in accumulation phase simultaneously, expect coordinated stop hunt within 1-2 hours",
            "manipulation_to_distribution": "After stop hunt completes (wick reversal), the real move begins. Entry signal.",
            "failed_manipulation": "If stop hunt does NOT reverse (sustained break), this is real — not a fake. Follow it.",
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    log(f"AMD analysis complete. {len(results)} symbols, {len(signals)} signals")
    return output

if __name__ == "__main__":
    result = run()
    if result and result.get("signals"):
        print("\nTRADING SIGNALS:")
        for s in result["signals"]:
            print(f"  {s['direction']:8s} {s['symbol']:6s} — {s['description']}")
