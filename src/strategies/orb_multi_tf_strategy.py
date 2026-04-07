#!/usr/bin/env python3
"""
Opening Range Breakout (ORB) + Multi-Timeframe Alignment Strategy
=================================================================
Based on @ceewillii ORB approach + @kaycapitals multi-TF wisdom.
15-min ORB is optimal per backtesting research.

Runs as a daemon from 9:25 AM - 4:05 PM ET on trading days.
  - 9:25-9:30: Pre-open setup (load overnight gaps, session intel)
  - 9:30-9:45: Accumulate Opening Range bars
  - 9:45: Lock Opening Range (OR_HIGH, OR_LOW, OR_RANGE, OR_MIDPOINT)
  - 9:45-16:00: Monitor for breakouts every ~60s
  - 16:00-16:05: EOD summary, save history

Writes: data/quantum_feed/orb_signals.json
Appends: data/quantum_feed/orb_history.jsonl
"""

import json, os, sys, time, datetime, traceback, statistics
from pathlib import Path
from collections import defaultdict

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}. Install with: pip3 install yfinance pandas numpy")
    sys.exit(1)

# --- Configuration ---

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"
ORB_SIGNALS_PATH = QF / "orb_signals.json"
ORB_HISTORY_PATH = QF / "orb_history.jsonl"

SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "AMD", "META", "AAPL", "XLE", "IWM"]

# Timezone
import zoneinfo
ET = zoneinfo.ZoneInfo("America/New_York")
UTC = zoneinfo.ZoneInfo("UTC")

# ORB parameters
ORB_MINUTES = 15  # 15-min opening range (optimal per backtesting)
VOLUME_MULTIPLIER = 1.5  # Breakout volume must be >1.5x avg
HOLD_BARS = 2  # Breakout must hold for 2+ consecutive bars
BUFFER_PCT = 0.001  # 0.1% entry buffer

# Multi-TF parameters
TF_PERIODS = {
    "weekly": {"sma": 20, "rsi": 14},
    "daily": {"sma": 20, "rsi": 14},
    "4h": {"sma": 20, "rsi": 14},
    "15min": {"sma": 20, "rsi": 14},
}

# --- Helpers ---

def iso_now():
    return datetime.datetime.now(UTC).isoformat()

def et_now():
    return datetime.datetime.now(ET)

def log(msg):
    print(f"[{iso_now()}] ORB-MTF: {msg}", flush=True)

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# --- Technical Indicators ---

def compute_rsi(series, period=14):
    """Compute RSI from a price series."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_macd(series, fast=12, slow=26, signal=9):
    """Compute MACD line and signal line."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def classify_trend(price, sma, rsi_val, macd_val, signal_val):
    """Classify trend as BULLISH, BEARISH, or NEUTRAL."""
    bullish_count = 0
    bearish_count = 0

    if price > sma:
        bullish_count += 1
    elif price < sma:
        bearish_count += 1

    if rsi_val > 50:
        bullish_count += 1
    elif rsi_val < 50:
        bearish_count += 1

    if macd_val > signal_val:
        bullish_count += 1
    elif macd_val < signal_val:
        bearish_count += 1

    if bullish_count == 3:
        return "BULLISH"
    elif bearish_count == 3:
        return "BEARISH"
    else:
        return "NEUTRAL"


# ===========================================================================
# PART 2: MULTI-TIMEFRAME SYNCHRONIZATION
# ===========================================================================

class MultiTimeframeAnalyzer:
    """
    Compute trend on 4 timeframes for each symbol using yfinance.
    Weekly, Daily from standard intervals; 4H and 15min aggregated from intraday.
    """

    def __init__(self, symbols=SYMBOLS):
        self.symbols = symbols
        self._cache = {}
        self._cache_time = None

    def analyze_all(self):
        """Return dict: symbol -> {weekly, daily, 4h, 15min, alignment_score, direction}"""
        results = {}
        for sym in self.symbols:
            try:
                results[sym] = self._analyze_symbol(sym)
            except Exception as e:
                log(f"MTF error for {sym}: {e}")
                results[sym] = {
                    "weekly": "NEUTRAL", "daily": "NEUTRAL",
                    "4h": "NEUTRAL", "15min": "NEUTRAL",
                    "alignment_score": 0, "aligned_direction": "NEUTRAL",
                    "size_factor": 0.0, "error": str(e)
                }
        self._cache = results
        self._cache_time = datetime.datetime.now(UTC)
        return results

    def _analyze_symbol(self, symbol):
        ticker = yf.Ticker(symbol)

        # --- Weekly ---
        weekly_trend = self._compute_tf_trend(ticker, period="1y", interval="1wk")

        # --- Daily ---
        daily_trend = self._compute_tf_trend(ticker, period="3mo", interval="1d")

        # --- 4H (from 5-min bars, last 5 days, aggregated) ---
        fourh_trend = self._compute_intraday_tf(ticker, agg_minutes=240)

        # --- 15min (from 5-min bars) ---
        fifteen_trend = self._compute_intraday_tf(ticker, agg_minutes=15)

        trends = [weekly_trend, daily_trend, fourh_trend, fifteen_trend]
        bullish = sum(1 for t in trends if t == "BULLISH")
        bearish = sum(1 for t in trends if t == "BEARISH")

        if bullish >= bearish:
            alignment_score = bullish
            direction = "BULLISH"
        else:
            alignment_score = bearish
            direction = "BEARISH"

        # Size factor based on alignment
        size_map = {4: 1.0, 3: 0.75, 2: 0.0, 1: 0.0, 0: 0.0}
        size_factor = size_map.get(alignment_score, 0.0)

        return {
            "weekly": weekly_trend,
            "daily": daily_trend,
            "4h": fourh_trend,
            "15min": fifteen_trend,
            "alignment_score": alignment_score,
            "aligned_direction": direction,
            "size_factor": size_factor,
        }

    def _compute_tf_trend(self, ticker, period, interval):
        """Compute trend from standard yfinance period/interval."""
        try:
            df = ticker.history(period=period, interval=interval)
            if df.empty or len(df) < 26:
                return "NEUTRAL"
            close = df["Close"]
            sma = close.rolling(20).mean()
            rsi = compute_rsi(close, 14)
            macd_line, signal_line = compute_macd(close)

            return classify_trend(
                close.iloc[-1], sma.iloc[-1],
                rsi.iloc[-1], macd_line.iloc[-1], signal_line.iloc[-1]
            )
        except Exception:
            return "NEUTRAL"

    def _compute_intraday_tf(self, ticker, agg_minutes=240):
        """Compute trend from intraday 5-min bars aggregated to agg_minutes."""
        try:
            df = ticker.history(period="5d", interval="5m")
            if df.empty or len(df) < 30:
                return "NEUTRAL"

            if agg_minutes > 5:
                df_resampled = df.resample(f"{agg_minutes}min").agg({
                    "Open": "first", "High": "max", "Low": "min",
                    "Close": "last", "Volume": "sum"
                }).dropna()
            else:
                df_resampled = df

            if len(df_resampled) < 26:
                return "NEUTRAL"

            close = df_resampled["Close"]
            sma = close.rolling(20).mean()
            rsi = compute_rsi(close, 14)
            macd_line, signal_line = compute_macd(close)

            return classify_trend(
                close.iloc[-1], sma.iloc[-1],
                rsi.iloc[-1], macd_line.iloc[-1], signal_line.iloc[-1]
            )
        except Exception:
            return "NEUTRAL"

    def get_cached(self):
        """Return cached results if less than 5 min old."""
        if self._cache_time and (datetime.datetime.now(UTC) - self._cache_time).total_seconds() < 300:
            return self._cache
        return self.analyze_all()


# ===========================================================================
# PART 1: OPENING RANGE BREAKOUT (ORB)
# ===========================================================================

class OpeningRangeTracker:
    """
    Captures the 15-min Opening Range (9:30-9:45 ET) and monitors for breakouts.
    """

    def __init__(self, symbols=SYMBOLS):
        self.symbols = symbols
        self.opening_ranges = {}  # sym -> {or_high, or_low, or_range, or_mid, volume_bars}
        self.breakout_state = {}  # sym -> {direction, hold_count, triggered}
        self.or_locked = False
        self.or_bars = defaultdict(list)  # sym -> list of bar dicts during OR
        self._avg_volumes = {}
        self._success_history = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        self._load_history()

    def _load_history(self):
        """Load historical ORB success rates."""
        if ORB_HISTORY_PATH.exists():
            try:
                for line in ORB_HISTORY_PATH.read_text().strip().split("\n"):
                    if not line:
                        continue
                    rec = json.loads(line)
                    sym = rec.get("symbol", "")
                    if rec.get("outcome") == "WIN":
                        self._success_history[sym]["wins"] += 1
                    elif rec.get("outcome") == "LOSS":
                        self._success_history[sym]["losses"] += 1
                    self._success_history[sym]["total"] += 1
            except Exception as e:
                log(f"Could not load ORB history: {e}")

    def get_success_rate(self, symbol):
        h = self._success_history[symbol]
        if h["total"] == 0:
            return 0.5  # default 50%
        return h["wins"] / h["total"]

    def fetch_avg_volume(self):
        """Fetch 20-day average volume for each symbol."""
        for sym in self.symbols:
            try:
                t = yf.Ticker(sym)
                hist = t.history(period="1mo", interval="1d")
                if not hist.empty:
                    self._avg_volumes[sym] = hist["Volume"].tail(20).mean()
                else:
                    self._avg_volumes[sym] = 1_000_000
            except Exception:
                self._avg_volumes[sym] = 1_000_000
        log(f"Average volumes loaded for {len(self._avg_volumes)} symbols")

    def rebuild_opening_ranges_from_history(self):
        """
        Rebuild missing opening ranges from today's 5-minute history.
        This lets the daemon recover if it is restarted after 9:45 ET.
        """
        rebuilt = 0
        today_et = et_now().date()
        for sym in self.symbols:
            if sym in self.opening_ranges:
                continue
            try:
                t = yf.Ticker(sym)
                df = t.history(period="1d", interval="5m")
                if df.empty:
                    continue
                if df.index.tz is None:
                    df.index = df.index.tz_localize(ET)
                else:
                    df.index = df.index.tz_convert(ET)
                session_df = df[df.index.date == today_et]
                if session_df.empty:
                    continue
                or_bars = session_df[
                    (session_df.index.time >= datetime.time(9, 30))
                    & (session_df.index.time < datetime.time(9, 45))
                ]
                if or_bars.empty:
                    continue

                or_high = float(or_bars["High"].max())
                or_low = float(or_bars["Low"].min())
                or_range = or_high - or_low
                or_mid = (or_high + or_low) / 2
                or_volume = int(or_bars["Volume"].sum())

                self.opening_ranges[sym] = {
                    "or_high": round(or_high, 4),
                    "or_low": round(or_low, 4),
                    "or_range": round(or_range, 4),
                    "or_midpoint": round(or_mid, 4),
                    "or_volume": or_volume,
                    "bar_count": len(or_bars),
                }
                self.breakout_state.setdefault(
                    sym,
                    {"direction": None, "hold_count": 0, "triggered": False, "signal": "RANGE_BOUND"},
                )
                rebuilt += 1
            except Exception as e:
                log(f"Historical OR rebuild error {sym}: {e}")

        if rebuilt:
            self.or_locked = True
            log(f"Opening Range rebuilt from intraday history for {rebuilt} symbols")
        return rebuilt

    def accumulate_or_bar(self, symbol, high, low, close, volume, bar_time):
        """Add a bar to the Opening Range accumulation (called during 9:30-9:45)."""
        self.or_bars[symbol].append({
            "high": high, "low": low, "close": close,
            "volume": volume, "time": bar_time
        })

    def lock_opening_range(self):
        """Called at 9:45 ET. Compute OR_HIGH, OR_LOW, etc."""
        for sym in self.symbols:
            bars = self.or_bars.get(sym, [])
            if not bars:
                log(f"No OR bars for {sym}, skipping")
                continue

            or_high = max(b["high"] for b in bars)
            or_low = min(b["low"] for b in bars)
            or_range = or_high - or_low
            or_mid = (or_high + or_low) / 2
            or_volume = sum(b["volume"] for b in bars)

            self.opening_ranges[sym] = {
                "or_high": round(or_high, 4),
                "or_low": round(or_low, 4),
                "or_range": round(or_range, 4),
                "or_midpoint": round(or_mid, 4),
                "or_volume": or_volume,
                "bar_count": len(bars),
            }
            self.breakout_state[sym] = {
                "direction": None, "hold_count": 0,
                "triggered": False, "signal": "RANGE_BOUND"
            }
        if len(self.opening_ranges) < len(self.symbols):
            now_et = et_now()
            if now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 45):
                self.rebuild_opening_ranges_from_history()
        self.or_locked = bool(self.opening_ranges)
        log(f"Opening Range LOCKED for {len(self.opening_ranges)} symbols")
        for sym, orng in self.opening_ranges.items():
            log(f"  {sym}: H={orng['or_high']:.2f} L={orng['or_low']:.2f} R={orng['or_range']:.2f}")

    def fetch_current_bars(self):
        """Fetch latest 5-min bars for all symbols via yfinance."""
        results = {}
        for sym in self.symbols:
            try:
                t = yf.Ticker(sym)
                df = t.history(period="1d", interval="5m")
                if df.empty:
                    continue
                last = df.iloc[-1]
                results[sym] = {
                    "high": float(last["High"]),
                    "low": float(last["Low"]),
                    "close": float(last["Close"]),
                    "open": float(last["Open"]),
                    "volume": int(last["Volume"]),
                    "time": str(df.index[-1]),
                }
            except Exception as e:
                log(f"Fetch error {sym}: {e}")
        return results

    def evaluate_breakout(self, symbol, bar, overnight_gap=None):
        """
        Evaluate if current bar constitutes an ORB breakout.
        Returns signal dict.
        """
        orng = self.opening_ranges.get(symbol)
        if not orng:
            return {"signal": "NO_OR", "symbol": symbol}

        state = self.breakout_state.get(symbol)
        if not state:
            state = {"direction": None, "hold_count": 0, "triggered": False, "signal": "RANGE_BOUND"}
            self.breakout_state[symbol] = state

        close = bar["close"]
        volume = bar["volume"]
        avg_vol = self._avg_volumes.get(symbol, 1_000_000)
        vol_ratio = volume / avg_vol if avg_vol > 0 else 0.0

        # Determine gap alignment
        gap_direction = None
        if overnight_gap and symbol in overnight_gap:
            gap_pct = overnight_gap[symbol].get("gap_pct", 0)
            gap_direction = "BULLISH" if gap_pct > 0.3 else ("BEARISH" if gap_pct < -0.3 else "NEUTRAL")

        # Check breakout conditions
        if close > orng["or_high"]:
            proposed = "BREAKOUT_LONG"
        elif close < orng["or_low"]:
            proposed = "BREAKOUT_SHORT"
        else:
            # Price within opening range
            state["direction"] = None
            state["hold_count"] = 0
            state["signal"] = "RANGE_BOUND"
            return {
                "signal": "RANGE_BOUND",
                "symbol": symbol,
                "price": close,
                "or_high": orng["or_high"],
                "or_low": orng["or_low"],
                "distance_to_high_pct": round((orng["or_high"] - close) / close * 100, 3),
                "distance_to_low_pct": round((close - orng["or_low"]) / close * 100, 3),
            }

        # Volume filter
        strong_volume = vol_ratio >= VOLUME_MULTIPLIER

        # Hold count (consecutive bars in breakout direction)
        if proposed == state.get("direction"):
            state["hold_count"] += 1
        else:
            state["direction"] = proposed
            state["hold_count"] = 1

        confirmed = state["hold_count"] >= HOLD_BARS

        # Gap alignment check
        gap_aligned = True
        if gap_direction:
            if proposed == "BREAKOUT_LONG" and gap_direction == "BEARISH":
                gap_aligned = False
            elif proposed == "BREAKOUT_SHORT" and gap_direction == "BULLISH":
                gap_aligned = False

        # Final signal classification
        if strong_volume and confirmed:
            if proposed == "BREAKOUT_LONG":
                signal = "BREAKOUT_LONG"
            else:
                signal = "BREAKOUT_SHORT"
            state["triggered"] = True
        elif not strong_volume and (close > orng["or_high"] or close < orng["or_low"]):
            signal = "FADE_BREAKOUT"
        else:
            signal = "PENDING_CONFIRMATION"

        state["signal"] = signal

        # Entry, stop, target
        if signal == "BREAKOUT_LONG":
            entry = orng["or_high"] * (1 + BUFFER_PCT)
            stop = orng["or_low"]
            target = entry + 2 * orng["or_range"]
        elif signal == "BREAKOUT_SHORT":
            entry = orng["or_low"] * (1 - BUFFER_PCT)
            stop = orng["or_high"]
            target = entry - 2 * orng["or_range"]
        elif signal == "FADE_BREAKOUT":
            entry = close
            stop = close * (1.01 if close > orng["or_high"] else 0.99)
            target = orng["or_midpoint"]
        else:
            entry = stop = target = None

        risk_reward = None
        if entry and stop and target and abs(entry - stop) > 0:
            risk_reward = round(abs(target - entry) / abs(entry - stop), 2)

        return {
            "signal": signal,
            "symbol": symbol,
            "price": round(close, 4),
            "or_high": orng["or_high"],
            "or_low": orng["or_low"],
            "or_range": orng["or_range"],
            "or_midpoint": orng["or_midpoint"],
            "volume_ratio": round(vol_ratio, 2),
            "strong_volume": strong_volume,
            "hold_bars": state["hold_count"],
            "confirmed": confirmed,
            "gap_direction": gap_direction,
            "gap_aligned": gap_aligned,
            "entry": round(entry, 4) if entry else None,
            "stop": round(stop, 4) if stop else None,
            "target": round(target, 4) if target else None,
            "risk_reward": risk_reward,
            "success_rate": round(self.get_success_rate(symbol), 3),
        }


# ===========================================================================
# PART 3: COMBINED SIGNAL ENGINE
# ===========================================================================

class CombinedSignalEngine:
    """
    Merges ORB signals + Multi-TF alignment + gap/session/phase context.
    Produces priority-ranked trade signals.
    """

    PRIORITY_MATRIX = {
        # (alignment_score, orb_signal_type) -> conviction level
        (4, "BREAKOUT"): {"conviction": "HIGHEST", "size_pct": 1.0, "label": "ORB+4TF+GAP"},
        (3, "BREAKOUT"): {"conviction": "HIGH", "size_pct": 0.75, "label": "ORB+3TF"},
        (2, "BREAKOUT"): {"conviction": "SKIP", "size_pct": 0.0, "label": "CONFLICTING"},
        (4, "FADE"): {"conviction": "MEDIUM", "size_pct": 0.5, "label": "FADE+COUNTER"},
        (3, "FADE"): {"conviction": "MEDIUM", "size_pct": 0.4, "label": "FADE+PARTIAL"},
        (0, "NONE"): {"conviction": "WAIT", "size_pct": 0.0, "label": "NO_SETUP"},
    }

    def __init__(self):
        self.signals = []

    def combine(self, orb_signals, mtf_data, shared_context):
        """
        orb_signals: dict sym -> ORB signal
        mtf_data: dict sym -> MTF analysis
        shared_context: loaded JSON files from quantum_feed
        """
        self.signals = []

        overnight_gaps = shared_context.get("overnight_gap_signals", {})
        session_intel = shared_context.get("session_intelligence", {})
        amd_phase = shared_context.get("amd_phase", {})
        uncertainty = shared_context.get("uncertainty_premium", {})

        session_name = session_intel.get("session", "unknown")
        entry_quality = 0.5
        if isinstance(session_intel.get("strategy"), dict):
            entry_quality = session_intel["strategy"].get("entry_quality", 0.5)
        uncertainty_premium = uncertainty.get("uncertainty_premium", 0)
        risk_interpretation = uncertainty.get("premium_interpretation", "NORMAL")

        # AMD phases indexed by symbol
        amd_by_sym = {}
        for p in amd_phase.get("phases", []):
            amd_by_sym[p.get("symbol", "")] = p

        for sym, orb_sig in orb_signals.items():
            mtf = mtf_data.get(sym, {})
            alignment = mtf.get("alignment_score", 0)
            aligned_dir = mtf.get("aligned_direction", "NEUTRAL")
            size_factor = mtf.get("size_factor", 0.0)

            orb_type = orb_sig.get("signal", "RANGE_BOUND")

            # Classify ORB type for matrix lookup
            if orb_type in ("BREAKOUT_LONG", "BREAKOUT_SHORT"):
                orb_class = "BREAKOUT"
            elif orb_type == "FADE_BREAKOUT":
                orb_class = "FADE"
            else:
                orb_class = "NONE"

            # Check directional alignment between ORB and MTF
            directional_match = True
            if orb_type == "BREAKOUT_LONG" and aligned_dir == "BEARISH":
                directional_match = False
            elif orb_type == "BREAKOUT_SHORT" and aligned_dir == "BULLISH":
                directional_match = False

            # Effective alignment (reduce if direction mismatch)
            eff_alignment = alignment if directional_match else max(0, alignment - 2)

            # Priority matrix lookup
            matrix_key = (eff_alignment, orb_class)
            priority = self.PRIORITY_MATRIX.get(matrix_key)
            if not priority:
                # Find closest
                for score in range(eff_alignment, -1, -1):
                    if (score, orb_class) in self.PRIORITY_MATRIX:
                        priority = self.PRIORITY_MATRIX[(score, orb_class)]
                        break
                if not priority:
                    priority = {"conviction": "SKIP", "size_pct": 0.0, "label": "UNMATCHED"}

            # Gap alignment bonus
            gap_aligned = orb_sig.get("gap_aligned", True)
            if gap_aligned and orb_class == "BREAKOUT" and eff_alignment >= 4:
                priority = {"conviction": "HIGHEST", "size_pct": 1.0, "label": "ORB+4TF+GAP"}

            # Risk adjustment from uncertainty premium
            risk_adj = 1.0
            if risk_interpretation == "COMPLACENT" and uncertainty_premium > 3:
                risk_adj = 0.7  # reduce size when market is complacent about real risks
            elif risk_interpretation == "FEARFUL":
                risk_adj = 0.85

            final_size = round(priority["size_pct"] * risk_adj, 3)

            # Determine broker routing (Part 4)
            broker, instrument = self._route_execution(orb_type, eff_alignment, final_size)

            # AMD phase context
            sym_phase = amd_by_sym.get(sym, {})
            direction = "long"
            if orb_type == "BREAKOUT_SHORT":
                direction = "short"
            elif orb_type == "FADE_BREAKOUT" and aligned_dir == "BEARISH":
                direction = "short"

            combined = {
                "symbol": sym,
                "timestamp": iso_now(),
                "direction": direction,
                "orb": orb_sig,
                "multi_tf": {
                    "weekly": mtf.get("weekly", "NEUTRAL"),
                    "daily": mtf.get("daily", "NEUTRAL"),
                    "4h": mtf.get("4h", "NEUTRAL"),
                    "15min": mtf.get("15min", "NEUTRAL"),
                    "alignment_score": alignment,
                    "aligned_direction": aligned_dir,
                    "directional_match": directional_match,
                    "effective_alignment": eff_alignment,
                },
                "context": {
                    "session": session_name,
                    "entry_quality": entry_quality,
                    "uncertainty_premium": uncertainty_premium,
                    "risk_interpretation": risk_interpretation,
                    "amd_phase": sym_phase.get("phase", "UNKNOWN"),
                    "amd_direction": sym_phase.get("direction", "neutral"),
                },
                "conviction": priority["conviction"],
                "priority_label": priority["label"],
                "size_pct": final_size,
                "risk_adjustment": risk_adj,
                "execution": {
                    "broker": broker,
                    "instrument": instrument,
                    "entry": orb_sig.get("entry"),
                    "stop": orb_sig.get("stop"),
                    "target": orb_sig.get("target"),
                    "risk_reward": orb_sig.get("risk_reward"),
                },
            }
            self.signals.append(combined)

        # Sort by conviction priority
        conviction_order = {"HIGHEST": 0, "HIGH": 1, "MEDIUM": 2, "WAIT": 3, "SKIP": 4}
        self.signals.sort(key=lambda s: conviction_order.get(s["conviction"], 5))

        return self.signals

    def _route_execution(self, orb_type, alignment, size_pct):
        """
        Part 4: Execution routing.
        - Tastytrade Cash / IBKR Cash: day trades with 0DTE options
        - Alpaca: swing trades with weekly options or shares
        """
        if size_pct <= 0:
            return "NONE", "NO_TRADE"

        if orb_type in ("BREAKOUT_LONG", "BREAKOUT_SHORT", "FADE_BREAKOUT"):
            live_allowed = os.getenv("ALPACA_ALLOW_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}
            # Day trade -> prefer a currently-usable execution route.
            if alignment >= 4:
                return "tastytrade_cash", "0DTE_options"
            elif live_allowed:
                return "alpaca_live", "shares"
            elif alignment >= 3:
                return "alpaca_paper", "shares"
            else:
                return "alpaca_paper", "shares"
        else:
            # No active ORB but aligned TFs -> swing on Alpaca
            return "alpaca", "weekly_options_or_shares"

    def get_actionable(self):
        """Return only signals with conviction >= MEDIUM."""
        return [s for s in self.signals if s["conviction"] in ("HIGHEST", "HIGH", "MEDIUM")]


# ===========================================================================
# PART 5: HISTORY LOGGER
# ===========================================================================

class ORBHistoryLogger:
    """
    Saves all ORB results to orb_history.jsonl for quantum learner consumption.
    """

    @staticmethod
    def log_signal(signal_data, outcome=None):
        """Append a signal to history with optional outcome."""
        record = {
            "date": datetime.datetime.now(ET).strftime("%Y-%m-%d"),
            "timestamp": iso_now(),
            "symbol": signal_data.get("symbol"),
            "orb_signal": signal_data.get("orb", {}).get("signal"),
            "conviction": signal_data.get("conviction"),
            "alignment_score": signal_data.get("multi_tf", {}).get("alignment_score"),
            "entry": signal_data.get("execution", {}).get("entry"),
            "stop": signal_data.get("execution", {}).get("stop"),
            "target": signal_data.get("execution", {}).get("target"),
            "risk_reward": signal_data.get("execution", {}).get("risk_reward"),
            "broker": signal_data.get("execution", {}).get("broker"),
            "size_pct": signal_data.get("size_pct"),
            "or_high": signal_data.get("orb", {}).get("or_high"),
            "or_low": signal_data.get("orb", {}).get("or_low"),
            "or_range": signal_data.get("orb", {}).get("or_range"),
            "outcome": outcome,  # WIN, LOSS, SCRATCH -- filled in by EOD review
        }
        append_jsonl(ORB_HISTORY_PATH, record)


# ===========================================================================
# PART 6: DAEMON RUNNER
# ===========================================================================

class ORBDaemon:
    """
    Single daemon running 9:25 AM - 4:05 PM ET on trading days.
    Phases:
      1. Pre-open (9:25-9:30): Load context, fetch avg volumes
      2. OR Capture (9:30-9:45): Accumulate 5-min bars
      3. Post-OR Monitor (9:45-16:00): Check breakouts every 60s
      4. EOD (16:00-16:05): Summary, save history
    """

    def __init__(self):
        self.orb = OpeningRangeTracker(SYMBOLS)
        self.mtf = MultiTimeframeAnalyzer(SYMBOLS)
        self.engine = CombinedSignalEngine()
        self.logger = ORBHistoryLogger()
        self._last_mtf_update = None

    def is_trading_day(self):
        """Check if today is a US market trading day."""
        today = et_now().date()
        weekday = today.weekday()
        if weekday >= 5:  # Sat/Sun
            return False
        try:
            import pandas_market_calendars as mcal
            nyse = mcal.get_calendar("NYSE")
            schedule = nyse.schedule(start_date=str(today), end_date=str(today))
            return len(schedule) > 0
        except Exception:
            return weekday < 5  # fallback: weekdays only

    def load_shared_context(self):
        """Load all quantum_feed JSON for cross-strategy signals."""
        ctx = {}
        for f in QF.glob("*.json"):
            try:
                ctx[f.stem] = json.loads(f.read_text())
            except Exception:
                pass
        return ctx

    def _update_mtf_if_needed(self):
        """Update MTF analysis every 5 minutes max."""
        now = datetime.datetime.now(UTC)
        if self._last_mtf_update is None or (now - self._last_mtf_update).total_seconds() > 300:
            log("Refreshing Multi-Timeframe analysis...")
            self.mtf.analyze_all()
            self._last_mtf_update = now

    def run(self):
        """Main daemon loop."""
        log("ORB + Multi-TF Daemon starting...")

        if not self.is_trading_day():
            log("Not a trading day. Exiting.")
            return

        now_et = et_now()
        market_open = now_et.replace(hour=9, minute=25, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=5, second=0, microsecond=0)

        # If we start before 9:25, wait
        if now_et < market_open:
            wait_secs = (market_open - now_et).total_seconds()
            log(f"Waiting {wait_secs:.0f}s until 9:25 AM ET...")
            time.sleep(wait_secs)

        if now_et > market_close:
            log("Past market close. Exiting.")
            return

        # -- Phase 1: Pre-open setup (9:25-9:30) --
        log("=== PHASE 1: PRE-OPEN SETUP ===")
        shared_ctx = self.load_shared_context()
        log(f"Loaded {len(shared_ctx)} shared context files")

        self.orb.fetch_avg_volume()
        self.mtf.analyze_all()
        self._last_mtf_update = datetime.datetime.now(UTC)

        # Log initial MTF state
        mtf_data = self.mtf.get_cached()
        for sym, data in mtf_data.items():
            log(f"  MTF {sym}: W={data['weekly']} D={data['daily']} 4H={data['4h']} 15m={data['15min']} => {data['alignment_score']}/4 {data['aligned_direction']}")

        # Wait for 9:30 if needed
        now_et = et_now()
        open_930 = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        if now_et < open_930:
            wait_secs = (open_930 - now_et).total_seconds()
            log(f"Waiting {wait_secs:.0f}s for market open (9:30 ET)...")
            time.sleep(wait_secs)

        # -- Phase 2: OR Capture (9:30-9:45) --
        log("=== PHASE 2: CAPTURING OPENING RANGE (9:30-9:45 ET) ===")
        or_end = et_now().replace(hour=9, minute=45, second=5, microsecond=0)

        while et_now() < or_end:
            bars = self.orb.fetch_current_bars()
            for sym, bar in bars.items():
                self.orb.accumulate_or_bar(sym, bar["high"], bar["low"], bar["close"], bar["volume"], bar["time"])
            log(f"  OR bar captured for {len(bars)} symbols (t={et_now().strftime('%H:%M:%S')})")
            # Fetch every ~60 seconds during OR period
            time.sleep(60)

        # Lock the Opening Range at 9:45
        self.orb.lock_opening_range()

        # Save initial OR data
        initial_output = {
            "timestamp": iso_now(),
            "et_time": et_now().strftime("%Y-%m-%d %H:%M ET"),
            "phase": "OR_LOCKED",
            "opening_ranges": self.orb.opening_ranges,
            "multi_tf": {sym: mtf_data[sym] for sym in SYMBOLS if sym in mtf_data},
            "signals": [],
            "actionable": [],
        }
        save_json(ORB_SIGNALS_PATH, initial_output)
        log(f"Initial ORB data saved to {ORB_SIGNALS_PATH}")

        # -- Phase 3: Breakout Monitoring (9:45-16:00) --
        log("=== PHASE 3: MONITORING FOR BREAKOUTS ===")
        eod_time = et_now().replace(hour=16, minute=0, second=0, microsecond=0)

        while et_now() < eod_time:
            try:
                self._monitor_cycle(shared_ctx)
            except Exception as e:
                log(f"Monitor cycle error: {e}")
                traceback.print_exc()

            # Sleep 60 seconds between checks
            time.sleep(60)

        # -- Phase 4: EOD Summary --
        log("=== PHASE 4: END OF DAY SUMMARY ===")
        self._eod_summary()
        log("ORB + Multi-TF Daemon complete for today.")

    def _monitor_cycle(self, shared_ctx):
        """One monitoring cycle: fetch bars, evaluate breakouts, combine signals."""
        # Refresh MTF every 5 min
        self._update_mtf_if_needed()
        mtf_data = self.mtf.get_cached()

        # Refresh shared context every cycle (lightweight)
        shared_ctx = self.load_shared_context()

        # Parse overnight gap signals if available
        overnight_gaps = {}
        gap_data = shared_ctx.get("overnight_gap_signals", {})
        for g in gap_data.get("gaps", []) if isinstance(gap_data, dict) else []:
            sym = g.get("symbol", "")
            overnight_gaps[sym] = g

        if not self.orb.opening_ranges:
            now_et = et_now()
            if now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 45):
                self.orb.rebuild_opening_ranges_from_history()

        # Fetch current bars and evaluate breakouts
        bars = self.orb.fetch_current_bars()
        orb_signals = {}
        for sym, bar in bars.items():
            orb_signals[sym] = self.orb.evaluate_breakout(sym, bar, overnight_gaps)

        # Combine with MTF
        combined = self.engine.combine(orb_signals, mtf_data, shared_ctx)
        actionable = self.engine.get_actionable()

        # Log actionable signals
        for sig in actionable:
            log(f"  ** {sig['conviction']} | {sig['symbol']} | {sig['orb']['signal']} | "
                f"TF={sig['multi_tf']['alignment_score']}/4 | Size={sig['size_pct']} | "
                f"Broker={sig['execution']['broker']}")

        # Log history for actionable signals
        for sig in actionable:
            self.logger.log_signal(sig)

        # Save output
        output = {
            "timestamp": iso_now(),
            "et_time": et_now().strftime("%Y-%m-%d %H:%M ET"),
            "phase": "MONITORING",
            "opening_ranges": self.orb.opening_ranges,
            "multi_tf_summary": {
                sym: {
                    "alignment": mtf_data.get(sym, {}).get("alignment_score", 0),
                    "direction": mtf_data.get(sym, {}).get("aligned_direction", "NEUTRAL"),
                    "weekly": mtf_data.get(sym, {}).get("weekly", "?"),
                    "daily": mtf_data.get(sym, {}).get("daily", "?"),
                    "4h": mtf_data.get(sym, {}).get("4h", "?"),
                    "15min": mtf_data.get(sym, {}).get("15min", "?"),
                } for sym in SYMBOLS
            },
            "signals": combined,
            "actionable": actionable,
            "actionable_count": len(actionable),
            "success_rates": {sym: round(self.orb.get_success_rate(sym), 3) for sym in SYMBOLS},
        }
        save_json(ORB_SIGNALS_PATH, output)

    def _eod_summary(self):
        """End-of-day: final summary and mark signal file as complete."""
        mtf_data = self.mtf.get_cached()
        output = load_json(ORB_SIGNALS_PATH)
        output["phase"] = "EOD_COMPLETE"
        output["eod_timestamp"] = iso_now()
        output["summary"] = {
            "date": et_now().strftime("%Y-%m-%d"),
            "symbols_tracked": len(self.orb.opening_ranges),
            "breakouts_detected": sum(
                1 for s in self.orb.breakout_state.values()
                if s.get("triggered")
            ),
            "signals_generated": len(self.engine.signals),
            "actionable_count": len(self.engine.get_actionable()),
        }
        save_json(ORB_SIGNALS_PATH, output)
        log(f"EOD Summary: {output['summary']}")


# ===========================================================================
# ONE-SHOT MODE: For strategy_orchestrator.py integration
# ===========================================================================

def run_orb_mtf_snapshot():
    """
    One-shot analysis (non-daemon) for orchestrator integration.
    Returns combined signal dict that can be saved to quantum_feed.
    """
    log("Running ORB+MTF snapshot analysis...")
    orb = OpeningRangeTracker(SYMBOLS)
    mtf = MultiTimeframeAnalyzer(SYMBOLS)
    engine = CombinedSignalEngine()

    # Fetch MTF analysis
    mtf_data = mtf.analyze_all()

    # Load shared context
    ctx = {}
    for f in QF.glob("*.json"):
        try:
            ctx[f.stem] = json.loads(f.read_text())
        except Exception:
            pass

    # Check if we have a locked OR already from today
    existing = load_json(ORB_SIGNALS_PATH)
    now_et = et_now()
    today_str = now_et.strftime("%Y-%m-%d")

    has_or = False
    if existing.get("opening_ranges") and today_str in existing.get("et_time", ""):
        orb.opening_ranges = existing["opening_ranges"]
        orb.or_locked = True
        has_or = True
        log("Using existing Opening Range from today")

    # If no OR yet but market is open past 9:45, try to compute from intraday bars
    if not has_or and (now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 45)):
        log("Computing Opening Range from intraday bars...")
        orb.rebuild_opening_ranges_from_history()
        orb.fetch_avg_volume()

    # Evaluate current breakout state if OR is locked
    orb_signals = {}
    if orb.or_locked:
        bars = orb.fetch_current_bars()
        overnight_gaps = {}
        gap_data = ctx.get("overnight_gap_signals", {})
        for g in gap_data.get("gaps", []) if isinstance(gap_data, dict) else []:
            overnight_gaps[g.get("symbol", "")] = g

        for sym, bar in bars.items():
            orb_signals[sym] = orb.evaluate_breakout(sym, bar, overnight_gaps)

    # Combine
    combined = engine.combine(orb_signals, mtf_data, ctx)
    actionable = engine.get_actionable()

    output = {
        "timestamp": iso_now(),
        "et_time": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "phase": "SNAPSHOT",
        "or_locked": orb.or_locked,
        "opening_ranges": orb.opening_ranges,
        "multi_tf_summary": {
            sym: {
                "alignment": mtf_data.get(sym, {}).get("alignment_score", 0),
                "direction": mtf_data.get(sym, {}).get("aligned_direction", "NEUTRAL"),
                "weekly": mtf_data.get(sym, {}).get("weekly", "?"),
                "daily": mtf_data.get(sym, {}).get("daily", "?"),
                "4h": mtf_data.get(sym, {}).get("4h", "?"),
                "15min": mtf_data.get(sym, {}).get("15min", "?"),
            } for sym in SYMBOLS
        },
        "signals": combined,
        "actionable": actionable,
        "actionable_count": len(actionable),
        "success_rates": {sym: round(orb.get_success_rate(sym), 3) for sym in SYMBOLS},
    }
    save_json(ORB_SIGNALS_PATH, output)
    log(f"Snapshot saved: {len(combined)} signals, {len(actionable)} actionable")
    return output


# ===========================================================================
# MAIN ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ORB + Multi-TF Alignment Strategy")
    parser.add_argument("--mode", choices=["daemon", "snapshot"], default="daemon",
                        help="daemon=run all day, snapshot=one-shot analysis")
    args = parser.parse_args()

    if args.mode == "daemon":
        daemon = ORBDaemon()
        daemon.run()
    else:
        run_orb_mtf_snapshot()
