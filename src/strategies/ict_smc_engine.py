#!/usr/bin/env python3
"""
ICT Smart Money Concepts Engine
Based on @jadecapofficial 7 ICT lessons. Implements all 7 concepts:

1. ORDER BLOCKS — Last opposite candle before a strong move
2. FAIR VALUE GAPS (FVG) — Three-candle imbalance pattern
3. LIQUIDITY SWEEPS — Price takes out prev high/low then reverses
4. BREAK OF STRUCTURE (BOS) — Price breaks significant swing high/low
5. CHANGE OF CHARACTER (CHoCH) — First sign of trend change
6. PREMIUM/DISCOUNT ZONES — Fibonacci-based buy/sell zones
7. SMART MONEY TRAIL — Combined institutional order flow model

Scans intraday 5-min bars on top symbols via Alpaca API for real-time data.
Output: data/quantum_feed/ict_smc_signals.json
"""
import json, os, datetime, traceback
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd

# Alpaca SDK for real-time data
try:
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False

# Fallback to yfinance
try:
    import yfinance as yf
except ImportError:
    yf = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QF / "ict_smc_signals.json"

# Top symbols to scan
DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "AMZN", "GOOGL"]

# Alpaca credentials from env
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

# Thresholds
STRONG_MOVE_MULTIPLIER = 2.0  # body must be 2x avg body for "strong move"
FVG_MIN_GAP_PCT = 0.001       # minimum 0.1% gap for FVG
SWING_LOOKBACK = 5             # bars to look back for swing highs/lows
LIQUIDITY_SWEEP_TOLERANCE = 0.001  # 0.1% beyond prev high/low counts as sweep


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def log(msg):
    print(f"[{iso_now()}] ICT_SMC: {msg}", flush=True)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Data Fetching — Alpaca primary, yfinance fallback
# ---------------------------------------------------------------------------

def get_alpaca_client() -> Optional[object]:
    """Create Alpaca data client."""
    if not ALPACA_AVAILABLE or not ALPACA_API_KEY:
        return None
    try:
        return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    except Exception as e:
        log(f"  Alpaca client error: {e}")
        return None


def fetch_bars_alpaca(client, symbol: str, timeframe: str = "5Min",
                      days: int = 5) -> Optional[pd.DataFrame]:
    """Fetch bars from Alpaca."""
    if client is None:
        return None
    try:
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(days=days)

        if timeframe == "1Min":
            tf = TimeFrame(1, TimeFrameUnit.Minute)
        elif timeframe == "5Min":
            tf = TimeFrame(5, TimeFrameUnit.Minute)
        else:
            tf = TimeFrame(15, TimeFrameUnit.Minute)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
        )
        bars = client.get_stock_bars(request)
        df = bars.df
        if df is None or df.empty:
            return None
        # Reset multi-index if present
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level=0, drop=True)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        log(f"  Alpaca fetch error {symbol}: {e}")
        return None


def fetch_bars_yfinance(symbol: str, interval: str = "5m",
                        period: str = "5d") -> Optional[pd.DataFrame]:
    """Fallback: fetch from yfinance."""
    if yf is None:
        return None
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception:
        return None


def fetch_bars(symbol: str, alpaca_client=None) -> Optional[pd.DataFrame]:
    """Fetch 5-min bars, Alpaca first then yfinance fallback."""
    df = fetch_bars_alpaca(alpaca_client, symbol, "5Min", 5)
    if df is not None and len(df) > 10:
        return df
    return fetch_bars_yfinance(symbol, "5m", "5d")


def fetch_daily(symbol: str, alpaca_client=None) -> Optional[pd.DataFrame]:
    """Fetch daily bars for context."""
    if yf is not None:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="30d", interval="1d")
            if df is not None and not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Helper: Swing Points
# ---------------------------------------------------------------------------

def find_swing_highs(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> List[Tuple[int, float]]:
    """Find swing highs: bar where high > surrounding bars."""
    swings = []
    highs = df["high"].values
    for i in range(lookback, len(df) - lookback):
        is_swing = True
        for j in range(1, lookback + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_swing = False
                break
        if is_swing:
            swings.append((i, float(highs[i])))
    return swings


def find_swing_lows(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> List[Tuple[int, float]]:
    """Find swing lows: bar where low < surrounding bars."""
    swings = []
    lows = df["low"].values
    for i in range(lookback, len(df) - lookback):
        is_swing = True
        for j in range(1, lookback + 1):
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_swing = False
                break
        if is_swing:
            swings.append((i, float(lows[i])))
    return swings


def avg_body_size(df: pd.DataFrame, lookback: int = 50) -> float:
    """Average absolute candle body size."""
    bodies = abs(df["close"] - df["open"]).iloc[-lookback:]
    return float(bodies.mean()) if len(bodies) > 0 else 0


# ---------------------------------------------------------------------------
# 1. ORDER BLOCKS
# ---------------------------------------------------------------------------

def detect_order_blocks(df: pd.DataFrame, symbol: str) -> List[Dict]:
    """
    Identify order blocks: last opposite candle before a strong move.
    Bullish OB: last bearish candle before strong bullish move.
    Bearish OB: last bullish candle before strong bearish move.
    """
    obs = []
    if df is None or len(df) < 20:
        return obs

    avg_body = avg_body_size(df)
    if avg_body == 0:
        return obs

    for i in range(1, len(df) - 1):
        curr_body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        prev_o = df["open"].iloc[i - 1]
        prev_c = df["close"].iloc[i - 1]
        curr_o = df["open"].iloc[i]
        curr_c = df["close"].iloc[i]

        # Strong bullish move (current candle)
        if curr_c > curr_o and curr_body > avg_body * STRONG_MOVE_MULTIPLIER:
            # Previous candle was bearish = bullish order block
            if prev_c < prev_o:
                obs.append({
                    "symbol": symbol,
                    "type": "order_block",
                    "direction": "bullish_ob",
                    "ob_high": round(float(max(prev_o, prev_c)), 4),
                    "ob_low": round(float(min(prev_o, prev_c)), 4),
                    "ob_index": i - 1,
                    "strong_move_size": round(float(curr_body), 4),
                    "timestamp": str(df.index[i - 1]),
                    "active": True,
                })

        # Strong bearish move (current candle)
        elif curr_c < curr_o and curr_body > avg_body * STRONG_MOVE_MULTIPLIER:
            # Previous candle was bullish = bearish order block
            if prev_c > prev_o:
                obs.append({
                    "symbol": symbol,
                    "type": "order_block",
                    "direction": "bearish_ob",
                    "ob_high": round(float(max(prev_o, prev_c)), 4),
                    "ob_low": round(float(min(prev_o, prev_c)), 4),
                    "ob_index": i - 1,
                    "strong_move_size": round(float(curr_body), 4),
                    "timestamp": str(df.index[i - 1]),
                    "active": True,
                })

    # Check if price has returned to any OB (making it a live signal)
    current_price = float(df["close"].iloc[-1])
    for ob in obs:
        if ob["direction"] == "bullish_ob":
            if ob["ob_low"] <= current_price <= ob["ob_high"]:
                ob["price_at_ob"] = True
                ob["signal"] = "long_entry"
                ob["entry"] = round(current_price, 4)
            else:
                ob["price_at_ob"] = False
        elif ob["direction"] == "bearish_ob":
            if ob["ob_low"] <= current_price <= ob["ob_high"]:
                ob["price_at_ob"] = True
                ob["signal"] = "short_entry"
                ob["entry"] = round(current_price, 4)
            else:
                ob["price_at_ob"] = False

    # Return only recent OBs (last 20)
    return obs[-20:]


# ---------------------------------------------------------------------------
# 2. FAIR VALUE GAPS (FVG / Imbalance)
# ---------------------------------------------------------------------------

def detect_fvg(df: pd.DataFrame, symbol: str) -> List[Dict]:
    """
    Detect Fair Value Gaps: three-candle pattern where middle candle body
    doesn't overlap with candle 1 high and candle 3 low (bullish FVG)
    or candle 1 low and candle 3 high (bearish FVG).
    """
    fvgs = []
    if df is None or len(df) < 3:
        return fvgs

    for i in range(2, len(df)):
        c1_high = float(df["high"].iloc[i - 2])
        c1_low = float(df["low"].iloc[i - 2])
        c2_high = float(df["high"].iloc[i - 1])
        c2_low = float(df["low"].iloc[i - 1])
        c3_high = float(df["high"].iloc[i])
        c3_low = float(df["low"].iloc[i])

        # Bullish FVG: gap between candle 1 high and candle 3 low
        if c3_low > c1_high:
            gap_size = c3_low - c1_high
            mid_price = (c3_low + c1_high) / 2
            gap_pct = gap_size / mid_price if mid_price > 0 else 0
            if gap_pct >= FVG_MIN_GAP_PCT:
                fvgs.append({
                    "symbol": symbol,
                    "type": "fvg",
                    "direction": "bullish_fvg",
                    "gap_high": round(c3_low, 4),
                    "gap_low": round(c1_high, 4),
                    "gap_size": round(gap_size, 4),
                    "gap_pct": round(gap_pct, 4),
                    "timestamp": str(df.index[i - 1]),
                    "filled": False,
                    "role": "support",
                })

        # Bearish FVG: gap between candle 3 high and candle 1 low
        if c1_low > c3_high:
            gap_size = c1_low - c3_high
            mid_price = (c1_low + c3_high) / 2
            gap_pct = gap_size / mid_price if mid_price > 0 else 0
            if gap_pct >= FVG_MIN_GAP_PCT:
                fvgs.append({
                    "symbol": symbol,
                    "type": "fvg",
                    "direction": "bearish_fvg",
                    "gap_high": round(c1_low, 4),
                    "gap_low": round(c3_high, 4),
                    "gap_size": round(gap_size, 4),
                    "gap_pct": round(gap_pct, 4),
                    "timestamp": str(df.index[i - 1]),
                    "filled": False,
                    "role": "resistance",
                })

    # Check if any FVGs have been filled
    current_price = float(df["close"].iloc[-1])
    for fvg in fvgs:
        if fvg["direction"] == "bullish_fvg":
            if current_price <= fvg["gap_high"] and current_price >= fvg["gap_low"]:
                fvg["filling"] = True
            elif current_price < fvg["gap_low"]:
                fvg["filled"] = True
        elif fvg["direction"] == "bearish_fvg":
            if current_price >= fvg["gap_low"] and current_price <= fvg["gap_high"]:
                fvg["filling"] = True
            elif current_price > fvg["gap_high"]:
                fvg["filled"] = True

    # Return unfilled FVGs (most recent 15)
    unfilled = [f for f in fvgs if not f.get("filled", False)]
    return unfilled[-15:]


# ---------------------------------------------------------------------------
# 3. LIQUIDITY SWEEPS
# ---------------------------------------------------------------------------

def detect_liquidity_sweeps(df: pd.DataFrame, symbol: str,
                            swing_highs: List[Tuple[int, float]],
                            swing_lows: List[Tuple[int, float]]) -> List[Dict]:
    """
    Detect liquidity sweeps: price takes out a previous high/low then reverses.
    Sweep of high then bearish close = short signal.
    Sweep of low then bullish close = long signal.
    """
    sweeps = []
    if df is None or len(df) < 10:
        return sweeps

    # Look at recent bars for sweep events
    scan_start = max(0, len(df) - 50)

    for i in range(scan_start, len(df)):
        bar_high = float(df["high"].iloc[i])
        bar_low = float(df["low"].iloc[i])
        bar_close = float(df["close"].iloc[i])
        bar_open = float(df["open"].iloc[i])

        # Check sweep of swing highs (price goes above then closes below)
        for sh_idx, sh_price in swing_highs:
            if sh_idx >= i:
                continue
            if sh_idx < scan_start - 20:
                continue
            # Bar wicked above the swing high
            if bar_high > sh_price * (1 + LIQUIDITY_SWEEP_TOLERANCE):
                # But closed below or near the swing high (bearish rejection)
                if bar_close < sh_price and bar_close < bar_open:
                    sweeps.append({
                        "symbol": symbol,
                        "type": "liquidity_sweep",
                        "direction": "bearish_sweep",
                        "swept_level": round(sh_price, 4),
                        "sweep_high": round(bar_high, 4),
                        "close_after": round(bar_close, 4),
                        "signal": "short",
                        "timestamp": str(df.index[i]),
                        "swept_at_index": sh_idx,
                    })

        # Check sweep of swing lows (price goes below then closes above)
        for sl_idx, sl_price in swing_lows:
            if sl_idx >= i:
                continue
            if sl_idx < scan_start - 20:
                continue
            # Bar wicked below the swing low
            if bar_low < sl_price * (1 - LIQUIDITY_SWEEP_TOLERANCE):
                # But closed above or near the swing low (bullish rejection)
                if bar_close > sl_price and bar_close > bar_open:
                    sweeps.append({
                        "symbol": symbol,
                        "type": "liquidity_sweep",
                        "direction": "bullish_sweep",
                        "swept_level": round(sl_price, 4),
                        "sweep_low": round(bar_low, 4),
                        "close_after": round(bar_close, 4),
                        "signal": "long",
                        "timestamp": str(df.index[i]),
                        "swept_at_index": sl_idx,
                    })

    return sweeps[-10:]


# ---------------------------------------------------------------------------
# 4. BREAK OF STRUCTURE (BOS)
# ---------------------------------------------------------------------------

def detect_bos(df: pd.DataFrame, symbol: str,
               swing_highs: List[Tuple[int, float]],
               swing_lows: List[Tuple[int, float]]) -> List[Dict]:
    """
    Break of Structure: price breaks a significant swing high or low.
    Bullish BOS: higher high made (breaks above previous swing high).
    Bearish BOS: lower low made (breaks below previous swing low).
    """
    events = []
    if df is None or len(df) < 10:
        return events

    scan_start = max(0, len(df) - 50)

    for i in range(scan_start, len(df)):
        bar_high = float(df["high"].iloc[i])
        bar_low = float(df["low"].iloc[i])
        bar_close = float(df["close"].iloc[i])

        # Bullish BOS: close above previous swing high
        for sh_idx, sh_price in swing_highs:
            if sh_idx >= i or sh_idx < scan_start - 20:
                continue
            if bar_close > sh_price:
                events.append({
                    "symbol": symbol,
                    "type": "bos",
                    "direction": "bullish_bos",
                    "broken_level": round(sh_price, 4),
                    "close_price": round(bar_close, 4),
                    "timestamp": str(df.index[i]),
                    "confirms": "uptrend",
                })
                break  # one BOS per bar

        # Bearish BOS: close below previous swing low
        for sl_idx, sl_price in swing_lows:
            if sl_idx >= i or sl_idx < scan_start - 20:
                continue
            if bar_close < sl_price:
                events.append({
                    "symbol": symbol,
                    "type": "bos",
                    "direction": "bearish_bos",
                    "broken_level": round(sl_price, 4),
                    "close_price": round(bar_close, 4),
                    "timestamp": str(df.index[i]),
                    "confirms": "downtrend",
                })
                break

    return events[-10:]


# ---------------------------------------------------------------------------
# 5. CHANGE OF CHARACTER (CHoCH)
# ---------------------------------------------------------------------------

def detect_choch(df: pd.DataFrame, symbol: str,
                 swing_highs: List[Tuple[int, float]],
                 swing_lows: List[Tuple[int, float]]) -> List[Dict]:
    """
    Change of Character: first sign of trend change.
    Bullish CHoCH: first higher high after a series of lower highs (downtrend ending).
    Bearish CHoCH: first lower low after a series of higher lows (uptrend ending).
    """
    events = []
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return events

    # Detect downtrend (lower highs) then first higher high = bullish CHoCH
    for i in range(2, len(swing_highs)):
        idx_curr, price_curr = swing_highs[i]
        idx_prev, price_prev = swing_highs[i - 1]
        idx_prev2, price_prev2 = swing_highs[i - 2]

        # Was in downtrend (lower highs)
        if price_prev < price_prev2:
            # Now made a higher high = CHoCH
            if price_curr > price_prev:
                events.append({
                    "symbol": symbol,
                    "type": "choch",
                    "direction": "bullish_choch",
                    "previous_lower_high": round(price_prev, 4),
                    "new_higher_high": round(price_curr, 4),
                    "timestamp": str(df.index[idx_curr]) if idx_curr < len(df) else "",
                    "signal": "trend_reversal_bullish",
                    "earlier_than_bos": True,
                })

    # Detect uptrend (higher lows) then first lower low = bearish CHoCH
    for i in range(2, len(swing_lows)):
        idx_curr, price_curr = swing_lows[i]
        idx_prev, price_prev = swing_lows[i - 1]
        idx_prev2, price_prev2 = swing_lows[i - 2]

        # Was in uptrend (higher lows)
        if price_prev > price_prev2:
            # Now made a lower low = CHoCH
            if price_curr < price_prev:
                events.append({
                    "symbol": symbol,
                    "type": "choch",
                    "direction": "bearish_choch",
                    "previous_higher_low": round(price_prev, 4),
                    "new_lower_low": round(price_curr, 4),
                    "timestamp": str(df.index[idx_curr]) if idx_curr < len(df) else "",
                    "signal": "trend_reversal_bearish",
                    "earlier_than_bos": True,
                })

    return events[-10:]


# ---------------------------------------------------------------------------
# 6. PREMIUM / DISCOUNT ZONES
# ---------------------------------------------------------------------------

def calculate_premium_discount(df: pd.DataFrame, symbol: str,
                               lookback: int = 100) -> Dict:
    """
    Divide recent price range into premium/discount using equilibrium (50%).
    Above 50% = premium (sell zone), below 50% = discount (buy zone).
    Uses Fibonacci levels: 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%.
    """
    if df is None or len(df) < lookback:
        lookback = len(df) if df is not None else 0
    if lookback < 10:
        return {}

    data = df.iloc[-lookback:]
    range_high = float(data["high"].max())
    range_low = float(data["low"].min())
    current_price = float(df["close"].iloc[-1])
    range_size = range_high - range_low

    if range_size <= 0:
        return {}

    # Position in range (0 = at low, 1 = at high)
    position_pct = (current_price - range_low) / range_size

    # Fibonacci levels
    fib_levels = {
        "0.0": round(range_low, 4),
        "0.236": round(range_low + range_size * 0.236, 4),
        "0.382": round(range_low + range_size * 0.382, 4),
        "0.5": round(range_low + range_size * 0.5, 4),
        "0.618": round(range_low + range_size * 0.618, 4),
        "0.786": round(range_low + range_size * 0.786, 4),
        "1.0": round(range_high, 4),
    }

    equilibrium = fib_levels["0.5"]
    zone = "premium" if current_price > equilibrium else "discount"
    bias = "sell" if zone == "premium" else "buy"

    # Optimal entry zones
    optimal_buy_zone = (fib_levels["0.618"], fib_levels["0.786"])  # deep discount
    optimal_sell_zone = (fib_levels["0.236"], fib_levels["0.382"])  # deep premium (from top)

    return {
        "symbol": symbol,
        "type": "premium_discount",
        "range_high": round(range_high, 4),
        "range_low": round(range_low, 4),
        "equilibrium": round(equilibrium, 4),
        "current_price": round(current_price, 4),
        "position_in_range": round(position_pct, 4),
        "zone": zone,
        "bias": bias,
        "fib_levels": fib_levels,
        "optimal_buy_zone": {
            "low": round(range_low + range_size * 0.236, 4),  # 0.236 from bottom
            "high": round(range_low + range_size * 0.382, 4),  # 0.382 from bottom
        },
        "optimal_sell_zone": {
            "low": round(range_low + range_size * 0.618, 4),  # 0.618 from bottom
            "high": round(range_low + range_size * 0.786, 4),  # 0.786 from bottom
        },
        "rule": "Only buy in discount zone (below 50%), only sell in premium zone (above 50%)",
    }


# ---------------------------------------------------------------------------
# 7. SMART MONEY TRAIL — Combined Model
# ---------------------------------------------------------------------------

def generate_smart_money_signals(symbol: str, order_blocks: List[Dict],
                                  fvgs: List[Dict], sweeps: List[Dict],
                                  bos_events: List[Dict], choch_events: List[Dict],
                                  pd_zone: Dict) -> List[Dict]:
    """
    Combine all ICT concepts into actionable Smart Money signals.
    Full model: Liquidity Sweep -> CHoCH -> Enter at OB in discount zone -> Target FVG fill.
    """
    signals = []

    zone = pd_zone.get("zone", "neutral")
    bias = pd_zone.get("bias", "neutral")
    current_price = pd_zone.get("current_price", 0)

    # Score confluence
    bullish_score = 0
    bearish_score = 0

    # Recent bullish events
    recent_bull_sweep = any(s["direction"] == "bullish_sweep" for s in sweeps[-3:])
    recent_bull_choch = any(c["direction"] == "bullish_choch" for c in choch_events[-3:])
    recent_bull_bos = any(b["direction"] == "bullish_bos" for b in bos_events[-3:])
    active_bull_ob = [ob for ob in order_blocks if ob["direction"] == "bullish_ob" and ob.get("price_at_ob")]
    bull_fvg_below = [f for f in fvgs if f["direction"] == "bullish_fvg" and f.get("gap_high", 0) < current_price]
    in_discount = zone == "discount"

    if recent_bull_sweep:
        bullish_score += 2
    if recent_bull_choch:
        bullish_score += 2
    if recent_bull_bos:
        bullish_score += 1
    if active_bull_ob:
        bullish_score += 2
    if bull_fvg_below:
        bullish_score += 1
    if in_discount:
        bullish_score += 1

    # Recent bearish events
    recent_bear_sweep = any(s["direction"] == "bearish_sweep" for s in sweeps[-3:])
    recent_bear_choch = any(c["direction"] == "bearish_choch" for c in choch_events[-3:])
    recent_bear_bos = any(b["direction"] == "bearish_bos" for b in bos_events[-3:])
    active_bear_ob = [ob for ob in order_blocks if ob["direction"] == "bearish_ob" and ob.get("price_at_ob")]
    bear_fvg_above = [f for f in fvgs if f["direction"] == "bearish_fvg" and f.get("gap_low", 0) > current_price]
    in_premium = zone == "premium"

    if recent_bear_sweep:
        bearish_score += 2
    if recent_bear_choch:
        bearish_score += 2
    if recent_bear_bos:
        bearish_score += 1
    if active_bear_ob:
        bearish_score += 2
    if bear_fvg_above:
        bearish_score += 1
    if in_premium:
        bearish_score += 1

    # Generate combined signal
    if bullish_score >= 5 and bullish_score > bearish_score:
        # Full bullish smart money trail
        entry = active_bull_ob[0]["ob_low"] if active_bull_ob else current_price
        target = bull_fvg_below[0]["gap_high"] if bull_fvg_below else current_price * 1.02
        stop = entry * 0.99  # 1% stop

        signals.append({
            "symbol": symbol,
            "type": "smart_money_trail",
            "direction": "long",
            "entry": round(float(entry), 4),
            "stop": round(float(stop), 4),
            "target": round(float(target), 4),
            "confluence_score": bullish_score,
            "max_score": 9,
            "zone": zone,
            "components": {
                "liquidity_sweep": recent_bull_sweep,
                "choch": recent_bull_choch,
                "bos": recent_bull_bos,
                "at_order_block": bool(active_bull_ob),
                "fvg_target": bool(bull_fvg_below),
                "in_discount": in_discount,
            },
            "model": "Sweep -> CHoCH -> OB entry in discount -> FVG fill target",
            "confidence": round(bullish_score / 9, 2),
        })

    # For bearish: target FVGs BELOW price (bullish FVGs that price will drop to fill)
    bear_target_fvgs = [f for f in fvgs if f["direction"] == "bullish_fvg" and f.get("gap_high", 0) < current_price]

    if bearish_score >= 5 and bearish_score > bullish_score:
        entry = active_bear_ob[0]["ob_high"] if active_bear_ob else current_price
        target = bear_target_fvgs[0]["gap_high"] if bear_target_fvgs else current_price * 0.98
        stop = entry * 1.01

        signals.append({
            "symbol": symbol,
            "type": "smart_money_trail",
            "direction": "short",
            "entry": round(float(entry), 4),
            "stop": round(float(stop), 4),
            "target": round(float(target), 4),
            "confluence_score": bearish_score,
            "max_score": 9,
            "zone": zone,
            "components": {
                "liquidity_sweep": recent_bear_sweep,
                "choch": recent_bear_choch,
                "bos": recent_bear_bos,
                "at_order_block": bool(active_bear_ob),
                "fvg_target": bool(bear_fvg_above),
                "in_premium": in_premium,
            },
            "model": "Sweep -> CHoCH -> OB entry in premium -> FVG fill target",
            "confidence": round(bearish_score / 9, 2),
        })

    return signals


# ---------------------------------------------------------------------------
# Main Runner
# ---------------------------------------------------------------------------

def run_ict_smc_engine(symbols: List[str] = None) -> Dict:
    """Run ICT Smart Money Concepts engine across all symbols."""
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    log(f"Running ICT SMC Engine on {len(symbols)} symbols...")

    # Initialize Alpaca client
    alpaca_client = get_alpaca_client()
    data_source = "alpaca" if alpaca_client else "yfinance"
    log(f"  Data source: {data_source}")

    output = {
        "timestamp": iso_now(),
        "strategy": "ict_smc",
        "data_source": data_source,
        "symbols_scanned": len(symbols),
        "symbols": {},
        "all_order_blocks": [],
        "all_fvgs": [],
        "all_sweeps": [],
        "all_bos": [],
        "all_choch": [],
        "all_premium_discount": [],
        "smart_money_signals": [],
        "errors": [],
    }

    for sym in symbols:
        try:
            log(f"  Scanning {sym}...")

            # Fetch 5-min bars
            df = fetch_bars(sym, alpaca_client)
            if df is None or len(df) < 20:
                log(f"    {sym}: insufficient data")
                output["errors"].append({"symbol": sym, "error": "insufficient_data"})
                continue

            # Find swing points (used by multiple concepts)
            swing_highs = find_swing_highs(df)
            swing_lows = find_swing_lows(df)

            # 1. Order Blocks
            obs = detect_order_blocks(df, sym)

            # 2. Fair Value Gaps
            fvgs = detect_fvg(df, sym)

            # 3. Liquidity Sweeps
            sweeps = detect_liquidity_sweeps(df, sym, swing_highs, swing_lows)

            # 4. Break of Structure
            bos_events = detect_bos(df, sym, swing_highs, swing_lows)

            # 5. Change of Character
            choch_events = detect_choch(df, sym, swing_highs, swing_lows)

            # 6. Premium/Discount Zones
            pd_zone = calculate_premium_discount(df, sym)

            # 7. Smart Money Trail (combined)
            smt_signals = generate_smart_money_signals(
                sym, obs, fvgs, sweeps, bos_events, choch_events, pd_zone
            )

            # Per-symbol summary
            sym_data = {
                "order_blocks": len(obs),
                "active_obs_at_price": sum(1 for ob in obs if ob.get("price_at_ob")),
                "fvgs": len(fvgs),
                "fvgs_filling": sum(1 for f in fvgs if f.get("filling")),
                "liquidity_sweeps": len(sweeps),
                "bos_events": len(bos_events),
                "choch_events": len(choch_events),
                "zone": pd_zone.get("zone", "unknown"),
                "bias": pd_zone.get("bias", "unknown"),
                "position_in_range": pd_zone.get("position_in_range", 0),
                "smart_money_signals": len(smt_signals),
                "current_price": pd_zone.get("current_price", 0),
            }
            output["symbols"][sym] = sym_data

            # Aggregate
            output["all_order_blocks"].extend(obs)
            output["all_fvgs"].extend(fvgs)
            output["all_sweeps"].extend(sweeps)
            output["all_bos"].extend(bos_events)
            output["all_choch"].extend(choch_events)
            if pd_zone:
                output["all_premium_discount"].append(pd_zone)
            output["smart_money_signals"].extend(smt_signals)

            log(f"    {sym}: OBs={len(obs)} FVGs={len(fvgs)} Sweeps={len(sweeps)} "
                f"BOS={len(bos_events)} CHoCH={len(choch_events)} Zone={pd_zone.get('zone', '?')} "
                f"SMT_signals={len(smt_signals)}")

        except Exception as e:
            log(f"    {sym}: ERROR - {e}")
            traceback.print_exc()
            output["errors"].append({"symbol": sym, "error": str(e)})

    # Read Kelly sizing for position sizing
    kelly = load_json(QF / "kelly_sizing.json")
    if kelly and "strategies" in kelly:
        ict_kelly = kelly["strategies"].get("ict_smc", {})
        output["kelly_fraction"] = ict_kelly.get("quarter_kelly", None)
        output["kelly_recommendation"] = ict_kelly.get("recommendation", "no data")

    # Summary stats
    output["summary"] = {
        "total_order_blocks": len(output["all_order_blocks"]),
        "total_fvgs": len(output["all_fvgs"]),
        "total_sweeps": len(output["all_sweeps"]),
        "total_bos": len(output["all_bos"]),
        "total_choch": len(output["all_choch"]),
        "total_smart_money_signals": len(output["smart_money_signals"]),
        "symbols_in_discount": [s for s, d in output["symbols"].items() if d.get("zone") == "discount"],
        "symbols_in_premium": [s for s, d in output["symbols"].items() if d.get("zone") == "premium"],
    }

    save_json(OUTPUT_FILE, output)
    log(f"ICT SMC Engine complete: {len(output['smart_money_signals'])} combined signals. Saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    result = run_ict_smc_engine()
    print(f"\nICT Smart Money Concepts Results:")
    print(f"  Symbols scanned: {result['symbols_scanned']}")
    print(f"  Data source: {result['data_source']}")
    print(f"\n  Summary:")
    for k, v in result["summary"].items():
        print(f"    {k}: {v}")
    if result["smart_money_signals"]:
        print(f"\n  Smart Money Trail Signals:")
        for s in result["smart_money_signals"]:
            print(f"    {s['symbol']} {s['direction']} conf={s['confluence_score']}/{s['max_score']} "
                  f"entry={s['entry']} stop={s['stop']} target={s['target']}")
    print(f"\n  Per-Symbol Zones:")
    for sym, data in result["symbols"].items():
        print(f"    {sym:>6s}: {data['zone']:>8s} ({data['bias']}) | "
              f"OBs={data['order_blocks']} FVGs={data['fvgs']} Sweeps={data['liquidity_sweeps']} "
              f"BOS={data['bos_events']} CHoCH={data['choch_events']}")
