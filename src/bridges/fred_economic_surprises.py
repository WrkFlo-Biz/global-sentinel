#!/usr/bin/env python3
"""
Global Sentinel — FRED Economic Surprise Calculator

Computes period-over-period changes for high-impact economic series.
Uses standard FRED observations endpoint (no vintagedates required).

Approach:
- For each series, fetch last 6 observations
- Compute MoM change and YoY change (where applicable)
- Compare against historical norms to identify surprises
- Score surprise magnitude and direction
- Feed regime shift signals into the regime rebalancer

Output: data/quantum_feed/economic_surprises.json
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- Setup paths ---
REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
sys.path.insert(0, str(REPO_ROOT)) if str(REPO_ROOT) not in sys.path else None

# --- Load .env ---
_env_path = REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

try:
    from src.monitoring.telegram_router import send as tg_send
except Exception:
    tg_send = None

# Use FRED_RATES_KEY as primary (less rate-limited), fallback to FRED_API_KEY
FRED_API_KEY = os.getenv("FRED_RATES_KEY", "") or os.getenv("FRED_API_KEY", "")
FRED_API_KEY_BACKUP = os.getenv("FRED_API_KEY", "")
QUANTUM_FEED = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QUANTUM_FEED / "economic_surprises.json"
HISTORY_FILE = QUANTUM_FEED / "economic_surprise_history.jsonl"
CACHE_FILE = REPO_ROOT / "data" / "cache" / "fred_surprise_cache.json"
ctx = ssl.create_default_context()

# ── Series definitions with historical norms ───────────────────────────
# norm_mom_pct: typical month-over-month % change (baseline)
# surprise_threshold_pct: deviation from norm to count as surprise
TRACKED_SERIES = {
    "CPIAUCSL": {
        "name": "CPI (All Urban Consumers)",
        "impact": "HIGH",
        "category": "inflation",
        "weight": 2.0,
        "norm_mom_pct": 0.3,       # ~0.3% MoM typical
        "surprise_threshold_pct": 0.2,  # >0.2% deviation from norm = surprise
        "units": "index",
        "pct_change": True,         # Compare % change, not absolute
    },
    "PAYEMS": {
        "name": "Nonfarm Payrolls",
        "impact": "HIGH",
        "category": "labor",
        "weight": 2.0,
        "norm_mom_abs": 200.0,     # ~200K/month typical
        "surprise_threshold_abs": 75.0,  # >75K deviation = surprise
        "units": "thousands",
        "pct_change": False,        # Compare absolute change
    },
    "UNRATE": {
        "name": "Unemployment Rate",
        "impact": "HIGH",
        "category": "labor",
        "weight": 1.5,
        "norm_mom_abs": 0.0,       # Flat is typical
        "surprise_threshold_abs": 0.2,  # >0.2pp deviation = surprise
        "units": "percent",
        "pct_change": False,
    },
    "GDP": {
        "name": "GDP",
        "impact": "HIGH",
        "category": "growth",
        "weight": 2.0,
        "norm_mom_pct": 0.5,       # ~0.5% QoQ typical
        "surprise_threshold_pct": 0.3,
        "units": "billions_chained",
        "pct_change": True,
    },
    "RSAFS": {
        "name": "Retail Sales (Advance)",
        "impact": "MEDIUM",
        "category": "consumption",
        "weight": 1.0,
        "norm_mom_pct": 0.3,
        "surprise_threshold_pct": 0.5,
        "units": "millions",
        "pct_change": True,
    },
    "INDPRO": {
        "name": "Industrial Production",
        "impact": "MEDIUM",
        "category": "production",
        "weight": 1.0,
        "norm_mom_pct": 0.2,
        "surprise_threshold_pct": 0.3,
        "units": "index",
        "pct_change": True,
    },
    "DGORDER": {
        "name": "Durable Goods Orders",
        "impact": "MEDIUM",
        "category": "manufacturing",
        "weight": 0.8,
        "norm_mom_pct": 0.5,
        "surprise_threshold_pct": 1.5,
        "units": "millions",
        "pct_change": True,
    },
    "JTSJOL": {
        "name": "JOLTS Job Openings",
        "impact": "MEDIUM",
        "category": "labor",
        "weight": 1.0,
        "norm_mom_pct": 0.0,
        "surprise_threshold_pct": 2.0,
        "units": "thousands",
        "pct_change": True,
    },
    "UMCSENT": {
        "name": "Consumer Sentiment (UMich)",
        "impact": "LOW",
        "category": "sentiment",
        "weight": 0.5,
        "norm_mom_pct": 0.0,
        "surprise_threshold_pct": 3.0,
        "units": "index",
        "pct_change": True,
    },
    "ICSA": {
        "name": "Initial Jobless Claims",
        "impact": "LOW",
        "category": "labor",
        "weight": 0.5,
        "norm_mom_abs": 0.0,
        "surprise_threshold_abs": 20.0,
        "units": "thousands",
        "pct_change": False,
    },
}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(msg: str):
    if tg_send:
        try:
            tg_send(msg[:4000], topic="macro")
        except Exception as e:
            log(f"[TG] Error: {e}")


def _fetch_observations_with_key(series_id: str, api_key: str, limit: int = 13) -> Optional[List[Dict[str, Any]]]:
    """Fetch recent observations from FRED with a specific key."""
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}"
        f"&file_type=json&sort_order=desc&limit={limit}"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "GlobalSentinel-EconSurprise/2.0"
        })
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        return [
            {"date": obs["date"], "value": float(obs["value"])}
            for obs in observations
            if obs.get("value", ".") not in (".", "", "NaN", "nan", "null")
        ]
    except Exception as e:
        return None


def fetch_observations(series_id: str, limit: int = 13) -> List[Dict[str, Any]]:
    """Fetch recent observations from FRED. Tries primary key, then backup."""
    result = _fetch_observations_with_key(series_id, FRED_API_KEY, limit)
    if result is not None:
        return result
    # Try backup key if primary fails (rate limit / 403)
    if FRED_API_KEY_BACKUP and FRED_API_KEY_BACKUP != FRED_API_KEY:
        log(f"  Primary key failed for {series_id}, trying backup key...")
        result = _fetch_observations_with_key(series_id, FRED_API_KEY_BACKUP, limit)
        if result is not None:
            return result
    log(f"FRED API error fetching {series_id}: all keys failed (403/rate-limited)")
    return []


def load_cache() -> Dict[str, Any]:
    """Load previous run's data for comparison."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache: Dict[str, Any]):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def compute_surprise(series_id: str, meta: Dict[str, Any], observations: List[Dict], cache: Dict) -> Optional[Dict[str, Any]]:
    """Compute surprise for a series based on period-over-period changes."""
    if len(observations) < 2:
        log(f"  {series_id}: Not enough observations ({len(observations)})")
        return None

    latest = observations[0]
    previous = observations[1]
    latest_val = latest["value"]
    prev_val = previous["value"]

    # Compute actual change
    if meta.get("pct_change", True):
        # Percentage change MoM
        if prev_val == 0:
            actual_change = 0.0
        else:
            actual_change = ((latest_val - prev_val) / abs(prev_val)) * 100.0
        norm = meta.get("norm_mom_pct", 0.0)
        threshold = meta.get("surprise_threshold_pct", 0.5)
    else:
        # Absolute change
        actual_change = latest_val - prev_val
        norm = meta.get("norm_mom_abs", 0.0)
        threshold = meta.get("surprise_threshold_abs", 50.0)

    # Deviation from norm
    deviation = actual_change - norm

    # YoY change (if we have 12+ months)
    yoy_change = None
    if len(observations) >= 12:
        yoy_val = observations[11]["value"]
        if yoy_val != 0:
            yoy_change = ((latest_val - yoy_val) / abs(yoy_val)) * 100.0

    # Compute rolling average of recent changes (last 6 periods) for context
    recent_changes = []
    for i in range(min(6, len(observations) - 1)):
        v1 = observations[i]["value"]
        v2 = observations[i + 1]["value"]
        if meta.get("pct_change", True):
            if v2 != 0:
                recent_changes.append(((v1 - v2) / abs(v2)) * 100.0)
        else:
            recent_changes.append(v1 - v2)

    avg_change = sum(recent_changes) / len(recent_changes) if recent_changes else 0.0
    stddev = (sum((x - avg_change) ** 2 for x in recent_changes) / len(recent_changes)) ** 0.5 if len(recent_changes) > 1 else 0.0

    # Surprise scoring
    surprise_type = "neutral"
    if abs(deviation) >= threshold:
        surprise_type = "positive" if deviation > 0 else "negative"

    # Z-score relative to recent history
    z_score = (actual_change - avg_change) / stddev if stddev > 0 else 0.0

    # Magnitude (0-10 scale based on deviation vs threshold)
    magnitude = min(10.0, (abs(deviation) / threshold) * 5.0) if threshold > 0 else 0.0

    # Regime shift signal: magnitude >= 7 or z-score > 2
    regime_shift_signal = magnitude >= 7.0 or abs(z_score) > 2.0

    # Check cache for new data detection
    cached = cache.get(series_id, {})
    is_new_data = cached.get("latest_date") != latest["date"]

    result = {
        "series_id": series_id,
        "name": meta["name"],
        "impact": meta["impact"],
        "category": meta["category"],
        "weight": meta["weight"],
        "observation_date": latest["date"],
        "previous_date": previous["date"],
        "latest_value": round(latest_val, 4),
        "previous_value": round(prev_val, 4),
        "actual_change": round(actual_change, 4),
        "norm_expected": round(norm, 4),
        "deviation": round(deviation, 4),
        "threshold": round(threshold, 4),
        "surprise_type": surprise_type,  # positive, negative, neutral
        "magnitude": round(magnitude, 2),
        "z_score": round(z_score, 2),
        "yoy_change_pct": round(yoy_change, 2) if yoy_change is not None else None,
        "avg_recent_change": round(avg_change, 4),
        "stddev_recent": round(stddev, 4),
        "regime_shift_signal": regime_shift_signal,
        "is_new_data": is_new_data,
        "computed_utc": datetime.now(timezone.utc).isoformat(),
    }

    direction = "beat" if surprise_type == "positive" else ("miss" if surprise_type == "negative" else "inline")
    log(f"  {series_id}: {direction} | change={actual_change:.3f} vs norm={norm:.3f} | "
        f"deviation={deviation:.3f} (threshold={threshold}) | magnitude={magnitude:.1f} | "
        f"z={z_score:.2f} | regime_shift={regime_shift_signal}")

    return result


def compute_aggregate_index(surprises: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute weighted aggregate surprise index."""
    if not surprises:
        return {"index": 0.0, "direction": "neutral", "strength": "none", "components": 0}

    weighted_sum = 0.0
    total_weight = 0.0

    for s in surprises:
        weight = s.get("weight", 1.0)
        signed = s["deviation"] if s["surprise_type"] != "neutral" else 0
        weighted_sum += signed * weight
        total_weight += weight

    index = weighted_sum / total_weight if total_weight > 0 else 0.0

    if index > 1.0:
        direction, strength = "positive", "strong"
    elif index > 0.3:
        direction, strength = "positive", "moderate"
    elif index > 0:
        direction, strength = "positive", "mild"
    elif index < -1.0:
        direction, strength = "negative", "strong"
    elif index < -0.3:
        direction, strength = "negative", "moderate"
    elif index < 0:
        direction, strength = "negative", "mild"
    else:
        direction, strength = "neutral", "none"

    return {
        "index": round(index, 4),
        "direction": direction,
        "strength": strength,
        "components": len(surprises),
        "total_weight": round(total_weight, 2),
    }


def run():
    log("=== FRED Economic Surprise Calculator v2 starting ===")
    if not FRED_API_KEY:
        log("ERROR: No FRED_API_KEY configured")
        sys.exit(1)

    cache = load_cache()
    surprises: List[Dict[str, Any]] = []
    errors: List[str] = []
    new_cache: Dict[str, Any] = {}

    for series_id, meta in TRACKED_SERIES.items():
        try:
            observations = fetch_observations(series_id, limit=13)
            if not observations:
                errors.append(f"{series_id}: no observations")
                continue

            result = compute_surprise(series_id, meta, observations, cache)
            if result:
                surprises.append(result)
                new_cache[series_id] = {
                    "latest_date": observations[0]["date"],
                    "latest_value": observations[0]["value"],
                    "computed_utc": datetime.now(timezone.utc).isoformat(),
                }

            time.sleep(0.6)  # Rate limit: stay well under 120 req/min
        except Exception as e:
            error_msg = f"{series_id}: {e}"
            log(f"Error: {error_msg}")
            errors.append(error_msg)

    # Compute aggregate
    aggregate = compute_aggregate_index(surprises)
    log(f"Aggregate surprise index: {aggregate['index']:.4f} ({aggregate['direction']}, {aggregate['strength']})")

    # Regime shift signals
    regime_signals = [s for s in surprises if s.get("regime_shift_signal")]
    if regime_signals:
        log(f"REGIME SHIFT SIGNALS: {[s['series_id'] for s in regime_signals]}")

    # Build output
    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "aggregate_surprise_index": aggregate,
        "surprises": surprises,
        "regime_shift_signals": regime_signals,
        "new_data_series": [s["series_id"] for s in surprises if s.get("is_new_data")],
        "errors": errors,
        "series_count": len(TRACKED_SERIES),
        "computed_count": len(surprises),
    }

    # Save output
    QUANTUM_FEED.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    log(f"Saved {len(surprises)} surprises to {OUTPUT_FILE}")

    # Save cache
    save_cache(new_cache)

    # Append to history
    history_entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "aggregate_index": aggregate["index"],
        "direction": aggregate["direction"],
        "strength": aggregate["strength"],
        "regime_signals": len(regime_signals),
        "new_data": len([s for s in surprises if s.get("is_new_data")]),
        "computed_utc": datetime.now(timezone.utc).isoformat(),
    }
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(history_entry) + "\n")

    # Telegram alerts for significant surprises
    if regime_signals:
        lines = ["<b>ECONOMIC SURPRISE - REGIME SHIFT SIGNAL</b>\n"]
        for sig in regime_signals:
            direction = "BEAT" if sig["surprise_type"] == "positive" else "MISS"
            lines.append(
                f"  {sig['name']}: {direction} | "
                f"Change: {sig['actual_change']:.3f} vs norm {sig['norm_expected']:.3f} | "
                f"Magnitude: {sig['magnitude']:.1f}/10 | Z-score: {sig['z_score']:.1f}"
            )
        lines.append(f"\nAggregate Index: {aggregate['index']:.2f} ({aggregate['direction']}, {aggregate['strength']})")
        lines.append("\nRegime rebalancer should evaluate position adjustments.")
        send_telegram("\n".join(lines))

    elif aggregate["strength"] in ("strong", "moderate"):
        msg = (
            f"<b>Economic Surprise Update</b>\n"
            f"Aggregate Index: {aggregate['index']:.2f} ({aggregate['direction']}, {aggregate['strength']})\n"
            f"Components: {aggregate['components']} series | "
            f"New data: {len([s for s in surprises if s.get('is_new_data')])} series"
        )
        send_telegram(msg)

    log("=== FRED Economic Surprise Calculator v2 complete ===")


if __name__ == "__main__":
    run()
