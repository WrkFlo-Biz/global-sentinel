#!/usr/bin/env python3
"""
Scalping Engine — 4H Range Breakout + Candlestick Pattern Scanner
Based on @marketanalysis13 research: execution discipline > pattern knowledge.

Scans 15-min and 5-min intraday data via yfinance.
Filters: volume confirmation (>1.5x avg), higher TF trend alignment.
Entry: on pattern confirmation candle close.
Stop: opposite side of pattern.
Target: 1.5-2x risk.
Output: data/quantum_feed/scalping_signals.json
"""
import json, os, datetime, traceback
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QF / "scalping_signals.json"

# Top liquid symbols for scalping
DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "AMZN", "GOOGL"]

VOLUME_THRESHOLD = 1.5  # bars volume must be >1.5x average
RISK_REWARD_MIN = 1.5
RISK_REWARD_MAX = 2.0


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def log(msg):
    print(f"[{iso_now()}] SCALPING: {msg}", flush=True)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_intraday(symbol: str, interval: str = "15m", period: str = "5d") -> Optional[pd.DataFrame]:
    """Fetch intraday bars from yfinance."""
    if yf is None:
        return None
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        log(f"  fetch error {symbol} {interval}: {e}")
        return None


def fetch_higher_tf(symbol: str) -> Optional[pd.DataFrame]:
    """Fetch daily bars for higher TF trend."""
    if yf is None:
        return None
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="30d", interval="1d")
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Higher TF Trend — EMA crossover + price position
# ---------------------------------------------------------------------------

def determine_trend(daily_df: pd.DataFrame) -> str:
    """EMA-based trend from daily bars with strength classification."""
    if daily_df is None or len(daily_df) < 20:
        return "neutral"
    close = daily_df["close"]
    ema10 = close.ewm(span=10).mean()
    ema20 = close.ewm(span=20).mean()
    ema_spread = abs(ema10.iloc[-1] - ema20.iloc[-1]) / close.iloc[-1]
    if ema10.iloc[-1] > ema20.iloc[-1] and close.iloc[-1] > ema10.iloc[-1]:
        return "bullish" if ema_spread > 0.005 else "weak_bullish"
    elif ema10.iloc[-1] < ema20.iloc[-1] and close.iloc[-1] < ema10.iloc[-1]:
        return "bearish" if ema_spread > 0.005 else "weak_bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Support / Resistance detection (for doji context)
# ---------------------------------------------------------------------------

def find_support_resistance(df: pd.DataFrame, lookback: int = 50) -> Tuple[List[float], List[float]]:
    """Find recent swing highs/lows as S/R levels."""
    supports, resistances = [], []
    if df is None or len(df) < lookback:
        return supports, resistances
    data = df.iloc[-lookback:]
    highs = data["high"].values
    lows = data["low"].values
    for i in range(2, len(data) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            supports.append(float(lows[i]))
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistances.append(float(highs[i]))
    return supports[-5:], resistances[-5:]


def near_level(price: float, levels: List[float], tolerance: float = 0.003) -> bool:
    """Check if price is near any S/R level."""
    for lvl in levels:
        if abs(price - lvl) / lvl < tolerance:
            return True
    return False


# ---------------------------------------------------------------------------
# Volume confirmation
# ---------------------------------------------------------------------------

def volume_confirmed(df: pd.DataFrame, idx: int, lookback: int = 20) -> bool:
    """Check if bar at idx has volume > VOLUME_THRESHOLD * average."""
    if "volume" not in df.columns or idx < lookback:
        return False
    avg_vol = df["volume"].iloc[max(0, idx - lookback):idx].mean()
    if avg_vol == 0:
        return False
    return df["volume"].iloc[idx] > VOLUME_THRESHOLD * avg_vol


def volume_ratio(df: pd.DataFrame, idx: int, lookback: int = 20) -> float:
    """Return volume ratio for signal strength scoring."""
    if "volume" not in df.columns or idx < lookback:
        return 0.0
    avg_vol = df["volume"].iloc[max(0, idx - lookback):idx].mean()
    if avg_vol == 0:
        return 0.0
    return float(df["volume"].iloc[idx] / avg_vol)


# ---------------------------------------------------------------------------
# A) 4H Range Scalping
# ---------------------------------------------------------------------------

def four_hour_range_breakout(df_15m: pd.DataFrame, symbol: str, trend: str) -> List[Dict]:
    """
    Mark first 4H candle high/low (first 16 bars of 15-min data for the day).
    Trade breakouts with volume confirmation.
    """
    signals = []
    if df_15m is None or len(df_15m) < 20:
        return signals

    df_15m = df_15m.copy()
    df_15m["date"] = df_15m.index.date

    for date_val, day_group in df_15m.groupby("date"):
        if len(day_group) < 17:
            continue

        # First 4H = first 16 bars of 15-min
        first_4h = day_group.iloc[:16]
        range_high = first_4h["high"].max()
        range_low = first_4h["low"].min()
        range_size = range_high - range_low

        if range_size <= 0:
            continue

        # Scan remaining bars for breakout
        rest = day_group.iloc[16:]
        for i_abs in range(len(rest)):
            bar = rest.iloc[i_abs]
            bar_idx = day_group.index.get_loc(rest.index[i_abs])

            # Bullish breakout
            if bar["close"] > range_high and trend not in ("bearish", "weak_bearish"):
                if volume_confirmed(day_group, bar_idx):
                    stop = range_low
                    risk = bar["close"] - stop
                    target = bar["close"] + risk * RISK_REWARD_MIN
                    vol_r = volume_ratio(day_group, bar_idx)
                    signals.append({
                        "symbol": symbol,
                        "type": "4h_range_breakout",
                        "direction": "long",
                        "entry": round(float(bar["close"]), 2),
                        "stop": round(float(stop), 2),
                        "target": round(float(target), 2),
                        "risk_reward": RISK_REWARD_MIN,
                        "range_high": round(float(range_high), 2),
                        "range_low": round(float(range_low), 2),
                        "volume_ratio": round(vol_r, 2),
                        "confidence": min(1.0, round(0.5 + vol_r * 0.1, 2)),
                        "timestamp": str(rest.index[i_abs]),
                        "trend": trend,
                    })
                    break

            # Bearish breakout
            elif bar["close"] < range_low and trend not in ("bullish", "weak_bullish"):
                if volume_confirmed(day_group, bar_idx):
                    stop = range_high
                    risk = stop - bar["close"]
                    target = bar["close"] - risk * RISK_REWARD_MIN
                    vol_r = volume_ratio(day_group, bar_idx)
                    signals.append({
                        "symbol": symbol,
                        "type": "4h_range_breakout",
                        "direction": "short",
                        "entry": round(float(bar["close"]), 2),
                        "stop": round(float(stop), 2),
                        "target": round(float(target), 2),
                        "risk_reward": RISK_REWARD_MIN,
                        "range_high": round(float(range_high), 2),
                        "range_low": round(float(range_low), 2),
                        "volume_ratio": round(vol_r, 2),
                        "confidence": min(1.0, round(0.5 + vol_r * 0.1, 2)),
                        "timestamp": str(rest.index[i_abs]),
                        "trend": trend,
                    })
                    break

    return signals


# ---------------------------------------------------------------------------
# B) Candlestick Pattern Scanner
# ---------------------------------------------------------------------------

def _body(o, c):
    return abs(c - o)

def _upper_shadow(h, o, c):
    return h - max(o, c)

def _lower_shadow(l, o, c):
    return min(o, c) - l


def is_engulfing_bullish(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    prev_o, prev_c = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    curr_o, curr_c = df["open"].iloc[i], df["close"].iloc[i]
    return (prev_c < prev_o and
            curr_c > curr_o and
            curr_o <= prev_c and curr_c >= prev_o)


def is_engulfing_bearish(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    prev_o, prev_c = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    curr_o, curr_c = df["open"].iloc[i], df["close"].iloc[i]
    return (prev_c > prev_o and
            curr_c < curr_o and
            curr_o >= prev_c and curr_c <= prev_o)


def is_doji(df: pd.DataFrame, i: int) -> bool:
    o, c, h, l = df["open"].iloc[i], df["close"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
    body = _body(o, c)
    total_range = h - l
    if total_range == 0:
        return False
    return body / total_range < 0.1


def is_hammer(df: pd.DataFrame, i: int) -> bool:
    o, c, h, l = df["open"].iloc[i], df["close"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    if body == 0:
        return False
    return lower >= 2 * body and upper <= body * 0.5


def is_shooting_star(df: pd.DataFrame, i: int) -> bool:
    o, c, h, l = df["open"].iloc[i], df["close"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
    body = _body(o, c)
    upper = _upper_shadow(h, o, c)
    lower = _lower_shadow(l, o, c)
    if body == 0:
        return False
    return upper >= 2 * body and lower <= body * 0.5


def is_morning_star(df: pd.DataFrame, i: int) -> bool:
    if i < 2:
        return False
    o1, c1 = df["open"].iloc[i - 2], df["close"].iloc[i - 2]
    o2, c2 = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    o3, c3 = df["open"].iloc[i], df["close"].iloc[i]
    body1 = _body(o1, c1)
    body2 = _body(o2, c2)
    return (c1 < o1 and
            body2 < body1 * 0.3 and
            c3 > o3 and
            c3 > (o1 + c1) / 2)


def is_evening_star(df: pd.DataFrame, i: int) -> bool:
    if i < 2:
        return False
    o1, c1 = df["open"].iloc[i - 2], df["close"].iloc[i - 2]
    o2, c2 = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    o3, c3 = df["open"].iloc[i], df["close"].iloc[i]
    body1 = _body(o1, c1)
    body2 = _body(o2, c2)
    return (c1 > o1 and
            body2 < body1 * 0.3 and
            c3 < o3 and
            c3 < (o1 + c1) / 2)


def scan_candlestick_patterns(df: pd.DataFrame, symbol: str, trend: str,
                               timeframe: str, supports: List[float] = None,
                               resistances: List[float] = None) -> List[Dict]:
    """Scan for candlestick patterns, filter by volume and trend alignment."""
    signals = []
    if df is None or len(df) < 5:
        return signals
    if supports is None:
        supports = []
    if resistances is None:
        resistances = []

    scan_range = range(max(2, len(df) - 20), len(df))

    for i in scan_range:
        patterns_found = []

        if is_engulfing_bullish(df, i):
            patterns_found.append(("bullish_engulfing", "long"))
        if is_engulfing_bearish(df, i):
            patterns_found.append(("bearish_engulfing", "short"))
        if is_hammer(df, i):
            patterns_found.append(("hammer", "long"))
        if is_shooting_star(df, i):
            patterns_found.append(("shooting_star", "short"))
        if is_morning_star(df, i):
            patterns_found.append(("morning_star", "long"))
        if is_evening_star(df, i):
            patterns_found.append(("evening_star", "short"))
        if is_doji(df, i):
            price = float(df["close"].iloc[i])
            if near_level(price, supports) or trend in ("bearish", "weak_bearish"):
                patterns_found.append(("doji_at_support", "long"))
            elif near_level(price, resistances) or trend in ("bullish", "weak_bullish"):
                patterns_found.append(("doji_at_resistance", "short"))

        for pattern_name, direction in patterns_found:
            is_reversal = pattern_name in ("doji_at_support", "doji_at_resistance",
                                           "morning_star", "evening_star",
                                           "hammer", "shooting_star")
            if not is_reversal:
                if (direction == "long" and trend == "bearish") or \
                   (direction == "short" and trend == "bullish"):
                    continue

            if not volume_confirmed(df, i):
                continue

            entry = float(df["close"].iloc[i])
            vol_r = volume_ratio(df, i)
            if direction == "long":
                stop = float(df["low"].iloc[max(0, i - 2):i + 1].min())
                risk = entry - stop
                if risk <= 0:
                    continue
                target = entry + risk * RISK_REWARD_MIN
            else:
                stop = float(df["high"].iloc[max(0, i - 2):i + 1].max())
                risk = stop - entry
                if risk <= 0:
                    continue
                target = entry - risk * RISK_REWARD_MIN

            # Confidence scoring
            confidence = 0.5
            if vol_r > 2.0:
                confidence += 0.15
            if is_reversal and near_level(entry, supports + resistances):
                confidence += 0.1
            if trend.startswith("weak"):
                confidence -= 0.05
            confidence = min(1.0, max(0.1, round(confidence, 2)))

            signals.append({
                "symbol": symbol,
                "type": "candlestick_pattern",
                "pattern": pattern_name,
                "direction": direction,
                "timeframe": timeframe,
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk_reward": RISK_REWARD_MIN,
                "volume_ratio": round(vol_r, 2),
                "confidence": confidence,
                "timestamp": str(df.index[i]),
                "trend": trend,
            })

    return signals


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_scalping_engine(symbols: List[str] = None) -> Dict:
    """Run the full scalping engine across symbols."""
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    log(f"Running scalping engine on {len(symbols)} symbols...")

    all_signals = []
    summary = {
        "timestamp": iso_now(),
        "strategy": "scalping_engine",
        "symbols_scanned": len(symbols),
        "signals": [],
        "pattern_counts": {},
        "errors": [],
    }

    for sym in symbols:
        try:
            log(f"  Scanning {sym}...")

            # Get higher TF trend
            daily = fetch_higher_tf(sym)
            trend = determine_trend(daily)

            # Find S/R levels from daily for doji context
            supports, resistances = find_support_resistance(daily)

            # Fetch 15-min data
            df_15m = fetch_intraday(sym, interval="15m", period="5d")

            # Fetch 5-min data
            df_5m = fetch_intraday(sym, interval="5m", period="5d")

            # A) 4H range breakout on 15-min
            range_signals = four_hour_range_breakout(df_15m, sym, trend)
            all_signals.extend(range_signals)

            # B) Candlestick patterns on 15-min
            pattern_15m = scan_candlestick_patterns(df_15m, sym, trend, "15m", supports, resistances)
            all_signals.extend(pattern_15m)

            # B) Candlestick patterns on 5-min
            pattern_5m = scan_candlestick_patterns(df_5m, sym, trend, "5m", supports, resistances)
            all_signals.extend(pattern_5m)

            log(f"    {sym}: trend={trend}, 4h_range={len(range_signals)}, "
                f"patterns_15m={len(pattern_15m)}, patterns_5m={len(pattern_5m)}")

        except Exception as e:
            log(f"    {sym}: ERROR - {e}")
            summary["errors"].append({"symbol": sym, "error": str(e)})

    # Sort by confidence descending, then recency
    all_signals.sort(key=lambda x: (-x.get("confidence", 0), x.get("timestamp", "")))

    # Count patterns
    for s in all_signals:
        ptype = s.get("pattern", s.get("type", "unknown"))
        summary["pattern_counts"][ptype] = summary["pattern_counts"].get(ptype, 0) + 1

    summary["signals"] = all_signals
    summary["total_signals"] = len(all_signals)

    # Read Kelly sizing if available to annotate signals
    kelly = load_json(QF / "kelly_sizing.json")
    if kelly and "strategies" in kelly:
        scalp_kelly = kelly["strategies"].get("scalping_engine", {})
        summary["kelly_fraction"] = scalp_kelly.get("quarter_kelly", None)
        summary["kelly_recommendation"] = scalp_kelly.get("recommendation", "no data")

    save_json(OUTPUT_FILE, summary)
    log(f"Scalping engine complete: {len(all_signals)} signals saved to {OUTPUT_FILE}")
    return summary


if __name__ == "__main__":
    result = run_scalping_engine()
    print(f"\nScalping Engine Results:")
    print(f"  Symbols scanned: {result['symbols_scanned']}")
    print(f"  Total signals: {result['total_signals']}")
    print(f"  Pattern counts: {json.dumps(result['pattern_counts'], indent=2)}")
    if result['signals']:
        print(f"\n  Top signals:")
        for s in result['signals'][:5]:
            print(f"    {s['symbol']} {s['direction']} via {s.get('pattern', s['type'])} "
                  f"entry={s['entry']} stop={s['stop']} target={s['target']} conf={s.get('confidence', '?')}")
