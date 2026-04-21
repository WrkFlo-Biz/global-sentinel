#!/usr/bin/env python3
"""
Systematic Options Selling Engine — Straddles & Strangles
=========================================================
Based on the Wadhwa approach (Deepak & Pooja Wadhwa, @poojawadhwa.official,
183K followers). Systematic (rule-based) option SELLING adapted from Indian
Bank Nifty to US markets via SPY/QQQ/IWM.

Core principles:
- Sell options systematically, never buy
- Straddles and strangles on major indices
- Strict stop-loss discipline
- Focus on time decay (theta) collection
- Predefined rules, no discretionary trading

Three sub-strategies:
1. SHORT STRADDLE SCANNER — sell ATM when IV >> realized vol
2. SHORT STRANGLE SCANNER — sell OTM puts+calls in moderate vol
3. THETA DECAY REGIME — analyze DTE curve + VIX term structure

Writes: data/quantum_feed/systematic_options_signals.json
Appends: data/quantum_feed/systematic_options_history.jsonl
"""

import json, os, sys, datetime, traceback, math
from pathlib import Path
from typing import List, Dict, Optional

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
OUTPUT_PATH = QF / "systematic_options_signals.json"
HISTORY_PATH = QF / "systematic_options_history.jsonl"

# === INSTRUMENT UNIVERSE ===
# Major US index ETFs — liquid options, tight spreads
UNDERLYINGS = ["SPY", "QQQ", "IWM"]

# VIX and VIX futures term structure proxies
VIX_SYMBOL = "^VIX"
VIX9D_SYMBOL = "^VIX9D"     # 9-day VIX
VIX3M_SYMBOL = "^VIX3M"     # 3-month VIX

# === THRESHOLDS ===
STRADDLE_IV_RV_RATIO = 1.3      # IV must be >1.3x realized vol for straddle
STRADDLE_VIX_MIN = 20           # VIX floor for straddle signals
STRADDLE_STOP_PCT = 2.0         # Exit if underlying moves >2% from strike
STRADDLE_TARGET_PCT = 50        # Target 50% of premium collected

STRANGLE_VIX_LOW = 15           # Strangle VIX range lower bound
STRANGLE_VIX_HIGH = 25          # Strangle VIX range upper bound
STRANGLE_PUT_OTM_PCT = 3.0      # Put strike 3-5% below price
STRANGLE_CALL_OTM_PCT = 3.0     # Call strike 3-5% above price
STRANGLE_TARGET_PCT = 60        # Target 60% of premium collected

THETA_IDEAL_DTE_LOW = 30        # Best entry DTE range
THETA_IDEAL_DTE_HIGH = 45
THETA_ACCELERATING_DTE = 21     # Below this: hold, don't add new

# Realized vol lookback windows (trading days)
RV_WINDOW_SHORT = 10
RV_WINDOW_LONG = 20


def iso_now():
    return datetime.datetime.now(UTC).isoformat()


def et_now():
    return datetime.datetime.now(ET)


def log(msg):
    print(f"[{iso_now()}] SYSTEMATIC-OPTIONS: {msg}", flush=True)


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


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fetch_history(symbol: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV from yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        log(f"  fetch error {symbol}: {e}")
        return None


def calc_realized_vol(df: pd.DataFrame, window: int = 20) -> Optional[float]:
    """
    Calculate annualized realized (historical) volatility from daily closes.
    Returns percentage (e.g. 18.5 for 18.5%).
    """
    if df is None or len(df) < window + 1:
        return None
    log_returns = np.log(df["close"] / df["close"].shift(1)).dropna()
    if len(log_returns) < window:
        return None
    rv = log_returns.tail(window).std() * math.sqrt(252) * 100
    return round(rv, 2)


def fetch_vix() -> Optional[float]:
    """Get current VIX level."""
    df = fetch_history(VIX_SYMBOL, period="5d", interval="1d")
    if df is not None and len(df) > 0:
        return round(float(df["close"].iloc[-1]), 2)
    return None


def fetch_vix_term_structure() -> Dict:
    """
    Fetch VIX term structure data points.
    Contango (VIX < VIX3M) = favorable for sellers.
    Backwardation (VIX > VIX3M) = caution.
    """
    result = {"vix": None, "vix9d": None, "vix3m": None, "structure": "unknown"}

    for sym, key in [(VIX_SYMBOL, "vix"), (VIX9D_SYMBOL, "vix9d"), (VIX3M_SYMBOL, "vix3m")]:
        df = fetch_history(sym, period="5d", interval="1d")
        if df is not None and len(df) > 0:
            result[key] = round(float(df["close"].iloc[-1]), 2)

    if result["vix"] is not None and result["vix3m"] is not None:
        if result["vix"] < result["vix3m"]:
            result["structure"] = "contango"
        elif result["vix"] > result["vix3m"]:
            result["structure"] = "backwardation"
        else:
            result["structure"] = "flat"

    return result


def get_current_price(symbol: str) -> Optional[float]:
    """Get latest closing price for a symbol."""
    df = fetch_history(symbol, period="5d", interval="1d")
    if df is not None and len(df) > 0:
        return round(float(df["close"].iloc[-1]), 2)
    return None


# ---------------------------------------------------------------------------
# STRATEGY 1: SHORT STRADDLE SCANNER
# ---------------------------------------------------------------------------
# Sell ATM straddle when implied vol is elevated relative to realized vol.
# Best when IV > 1.3x RV and VIX > 20.
# Stop: exit if underlying moves >2% from strike.
# Target: collect 50% of premium.

def scan_short_straddles() -> List[Dict]:
    """Scan for short straddle opportunities on index ETFs."""
    signals = []
    vix = fetch_vix()

    if vix is None:
        log("  Straddle: VIX unavailable, skipping")
        return signals

    log(f"  Straddle scanner: VIX = {vix}")

    if vix < STRADDLE_VIX_MIN:
        log(f"  Straddle: VIX {vix} < {STRADDLE_VIX_MIN} threshold, no straddle signals")
        return signals

    for symbol in UNDERLYINGS:
        try:
            df = fetch_history(symbol, period="3mo", interval="1d")
            if df is None or len(df) < RV_WINDOW_LONG + 1:
                continue

            price = round(float(df["close"].iloc[-1]), 2)
            rv_short = calc_realized_vol(df, RV_WINDOW_SHORT)
            rv_long = calc_realized_vol(df, RV_WINDOW_LONG)

            if rv_short is None or rv_long is None:
                continue

            # Use VIX as IV proxy for SPY; scale for QQQ/IWM
            iv_proxy = vix
            if symbol == "QQQ":
                iv_proxy = vix * 1.15   # QQQ typically ~15% higher vol than SPY
            elif symbol == "IWM":
                iv_proxy = vix * 1.10   # IWM slightly higher than SPY

            rv_avg = (rv_short + rv_long) / 2
            iv_rv_ratio = iv_proxy / rv_avg if rv_avg > 0 else 0

            log(f"  {symbol}: price={price}, IV_proxy={iv_proxy:.1f}, "
                f"RV_10d={rv_short}, RV_20d={rv_long}, ratio={iv_rv_ratio:.2f}")

            if iv_rv_ratio >= STRADDLE_IV_RV_RATIO:
                # ATM strike = nearest round number to price
                atm_strike = round(price)

                # Confidence based on how far ratio exceeds threshold
                excess = iv_rv_ratio - STRADDLE_IV_RV_RATIO
                confidence = min(0.90, 0.55 + excess * 0.15)

                stop_price_up = round(atm_strike * (1 + STRADDLE_STOP_PCT / 100), 2)
                stop_price_dn = round(atm_strike * (1 - STRADDLE_STOP_PCT / 100), 2)

                signals.append({
                    "strategy": "short_straddle",
                    "symbol": symbol,
                    "signal": "SELL_STRADDLE",
                    "direction": "neutral",
                    "price": price,
                    "atm_strike": atm_strike,
                    "iv_proxy": round(iv_proxy, 2),
                    "rv_10d": rv_short,
                    "rv_20d": rv_long,
                    "iv_rv_ratio": round(iv_rv_ratio, 2),
                    "vix": vix,
                    "stop_upper": stop_price_up,
                    "stop_lower": stop_price_dn,
                    "target": f"Collect {STRADDLE_TARGET_PCT}% of premium",
                    "confidence": round(confidence, 2),
                    "rationale": (
                        f"IV/RV ratio {iv_rv_ratio:.2f} > {STRADDLE_IV_RV_RATIO} threshold. "
                        f"VIX at {vix} (elevated). Sell ATM {atm_strike} straddle. "
                        f"Stop if {symbol} moves beyond {stop_price_dn}-{stop_price_up}. "
                        f"Target {STRADDLE_TARGET_PCT}% of premium collected."
                    ),
                    "timestamp": iso_now(),
                })
                log(f"  >> STRADDLE SIGNAL: {symbol} ATM={atm_strike}, "
                    f"ratio={iv_rv_ratio:.2f}, conf={confidence:.2f}")
            else:
                log(f"  {symbol}: IV/RV ratio {iv_rv_ratio:.2f} < "
                    f"{STRADDLE_IV_RV_RATIO}, no straddle signal")

        except Exception as e:
            log(f"  Straddle error {symbol}: {e}")
            traceback.print_exc()

    return signals


# ---------------------------------------------------------------------------
# STRATEGY 2: SHORT STRANGLE SCANNER
# ---------------------------------------------------------------------------
# Sell OTM put + OTM call when VIX is moderate (15-25).
# Put strike: 3-5% below current price.
# Call strike: 3-5% above current price.
# Stop: exit if either strike is breached.
# Target: collect 60% of premium.

def scan_short_strangles() -> List[Dict]:
    """Scan for short strangle opportunities."""
    signals = []
    vix = fetch_vix()

    if vix is None:
        log("  Strangle: VIX unavailable, skipping")
        return signals

    log(f"  Strangle scanner: VIX = {vix}")

    if vix < STRANGLE_VIX_LOW or vix > STRANGLE_VIX_HIGH:
        log(f"  Strangle: VIX {vix} outside {STRANGLE_VIX_LOW}-{STRANGLE_VIX_HIGH} range, "
            f"no strangle signals")
        return signals

    for symbol in UNDERLYINGS:
        try:
            df = fetch_history(symbol, period="3mo", interval="1d")
            if df is None or len(df) < RV_WINDOW_LONG + 1:
                continue

            price = round(float(df["close"].iloc[-1]), 2)
            rv_short = calc_realized_vol(df, RV_WINDOW_SHORT)
            rv_long = calc_realized_vol(df, RV_WINDOW_LONG)

            if rv_short is None:
                continue

            # Calculate OTM strikes
            # Use wider wings (5%) when VIX is higher, tighter (3%) when lower
            vix_pct = (vix - STRANGLE_VIX_LOW) / (STRANGLE_VIX_HIGH - STRANGLE_VIX_LOW)
            put_otm_pct = STRANGLE_PUT_OTM_PCT + vix_pct * 2.0      # 3-5%
            call_otm_pct = STRANGLE_CALL_OTM_PCT + vix_pct * 2.0    # 3-5%

            put_strike = round(price * (1 - put_otm_pct / 100))
            call_strike = round(price * (1 + call_otm_pct / 100))

            # Width of strangle as % of price
            width_pct = round((call_strike - put_strike) / price * 100, 1)

            # Confidence: higher when VIX is in sweet spot (18-22) and RV is contained
            vix_sweet = 1.0 - abs(vix - 20) / 10.0
            rv_contained = 1.0 if rv_short < vix else max(0.3, 1.0 - (rv_short - vix) / vix)
            confidence = min(0.85, 0.50 + vix_sweet * 0.15 + rv_contained * 0.10)

            signals.append({
                "strategy": "short_strangle",
                "symbol": symbol,
                "signal": "SELL_STRANGLE",
                "direction": "neutral",
                "price": price,
                "put_strike": put_strike,
                "call_strike": call_strike,
                "put_otm_pct": round(put_otm_pct, 1),
                "call_otm_pct": round(call_otm_pct, 1),
                "width_pct": width_pct,
                "vix": vix,
                "rv_10d": rv_short,
                "rv_20d": rv_long,
                "stop_rule": f"Exit if {symbol} breaches {put_strike} or {call_strike}",
                "target": f"Collect {STRANGLE_TARGET_PCT}% of premium",
                "confidence": round(confidence, 2),
                "rationale": (
                    f"VIX at {vix} (moderate range). Sell {symbol} {put_strike}P / "
                    f"{call_strike}C strangle ({width_pct}% wide). "
                    f"RV_10d={rv_short}%, RV_20d={rv_long}%. "
                    f"Exit if either strike breached. Target {STRANGLE_TARGET_PCT}% of premium."
                ),
                "timestamp": iso_now(),
            })
            log(f"  >> STRANGLE SIGNAL: {symbol} {put_strike}P/{call_strike}C, "
                f"width={width_pct}%, conf={confidence:.2f}")

        except Exception as e:
            log(f"  Strangle error {symbol}: {e}")
            traceback.print_exc()

    return signals


# ---------------------------------------------------------------------------
# STRATEGY 3: THETA DECAY REGIME ANALYSIS
# ---------------------------------------------------------------------------
# Analyze where we are in the theta decay curve.
# Best entries: 30-45 DTE.
# Accelerating decay: <21 DTE = hold existing, don't add new.
# Track VIX term structure (contango = favorable for sellers).

def analyze_theta_regime() -> List[Dict]:
    """Analyze current theta decay regime and VIX term structure."""
    signals = []

    try:
        term_structure = fetch_vix_term_structure()
        vix = term_structure.get("vix")
        vix9d = term_structure.get("vix9d")
        vix3m = term_structure.get("vix3m")
        structure = term_structure.get("structure", "unknown")

        log(f"  Theta regime: VIX={vix}, VIX9D={vix9d}, VIX3M={vix3m}, "
            f"structure={structure}")

        if vix is None:
            log("  Theta: VIX unavailable, skipping")
            return signals

        # Determine regime
        regime = "unknown"
        regime_action = "hold"
        confidence = 0.50

        # Contango = normal, favorable for sellers (vol tends to decrease)
        # Backwardation = fear, vol may spike further
        if structure == "contango":
            regime = "contango_favorable"
            regime_action = "new_entries_ok"
            confidence = 0.70
        elif structure == "backwardation":
            regime = "backwardation_caution"
            regime_action = "reduce_or_hedge"
            confidence = 0.60

        # VIX9D vs VIX gives near-term sentiment
        if vix9d is not None and vix is not None:
            vix9d_ratio = vix9d / vix if vix > 0 else 1.0
            if vix9d_ratio > 1.10:
                # Near-term vol spiking — caution
                regime = "near_term_spike"
                regime_action = "widen_stops"
                confidence = max(confidence, 0.65)
            elif vix9d_ratio < 0.90:
                # Near-term vol collapsing — very favorable for sellers
                regime = "near_term_crush"
                regime_action = "aggressive_new_entries"
                confidence = max(confidence, 0.75)

        # DTE guidance
        now_et = et_now()
        # Monthly opex is 3rd Friday — estimate days to next monthly
        # Simplified: calculate approximate DTE to next standard monthly expiry
        year, month = now_et.year, now_et.month
        # Find 3rd Friday of current month
        first_day = datetime.date(year, month, 1)
        # weekday: 0=Mon, 4=Fri
        first_friday = first_day + datetime.timedelta(days=(4 - first_day.weekday()) % 7)
        third_friday = first_friday + datetime.timedelta(weeks=2)

        today = now_et.date()
        dte_this_month = (third_friday - today).days

        # If past this month's expiry, look at next month
        if dte_this_month < 0:
            if month == 12:
                next_month_first = datetime.date(year + 1, 1, 1)
            else:
                next_month_first = datetime.date(year, month + 1, 1)
            first_friday = next_month_first + datetime.timedelta(
                days=(4 - next_month_first.weekday()) % 7
            )
            third_friday = first_friday + datetime.timedelta(weeks=2)
            dte_this_month = (third_friday - today).days

        # Also check next month for 30-45 DTE entries
        if month == 12:
            next_month_first = datetime.date(year + 1, 1, 1)
        else:
            next_month_first = datetime.date(year, month + 1, 1)
        first_friday_next = next_month_first + datetime.timedelta(
            days=(4 - next_month_first.weekday()) % 7
        )
        third_friday_next = first_friday_next + datetime.timedelta(weeks=2)
        dte_next_month = (third_friday_next - today).days

        # Choose the expiry cycle that falls in the ideal DTE window
        ideal_dte = None
        ideal_expiry = None
        for dte, expiry in [(dte_this_month, third_friday), (dte_next_month, third_friday_next)]:
            if THETA_IDEAL_DTE_LOW <= dte <= THETA_IDEAL_DTE_HIGH:
                ideal_dte = dte
                ideal_expiry = expiry.isoformat()
                break

        dte_guidance = "hold_existing"
        if ideal_dte is not None:
            dte_guidance = "ideal_entry_window"
            confidence = min(0.90, confidence + 0.10)
        elif dte_this_month < THETA_ACCELERATING_DTE:
            dte_guidance = "accelerating_decay_no_new_entries"
        else:
            dte_guidance = "waiting_for_ideal_window"

        # Build per-underlying signals
        for symbol in UNDERLYINGS:
            price = get_current_price(symbol)
            if price is None:
                continue

            sig = {
                "strategy": "theta_regime",
                "symbol": symbol,
                "signal": f"THETA_{regime_action.upper()}",
                "direction": "neutral",
                "price": price,
                "vix": vix,
                "vix9d": vix9d,
                "vix3m": vix3m,
                "term_structure": structure,
                "regime": regime,
                "regime_action": regime_action,
                "dte_this_month_expiry": dte_this_month,
                "dte_next_month_expiry": dte_next_month,
                "dte_guidance": dte_guidance,
                "ideal_dte": ideal_dte,
                "ideal_expiry": ideal_expiry,
                "confidence": round(confidence, 2),
                "rationale": (
                    f"VIX term structure: {structure}. Regime: {regime}. "
                    f"DTE to monthly: {dte_this_month}d (this), {dte_next_month}d (next). "
                    f"Guidance: {dte_guidance}. "
                    f"Action: {regime_action}."
                ),
                "timestamp": iso_now(),
            }

            if ideal_dte is not None:
                sig["rationale"] += (
                    f" Ideal entry at {ideal_dte} DTE (expiry {ideal_expiry})."
                )

            signals.append(sig)
            log(f"  >> THETA SIGNAL: {symbol} regime={regime}, "
                f"action={regime_action}, dte_guide={dte_guidance}, "
                f"conf={confidence:.2f}")

    except Exception as e:
        log(f"  Theta regime error: {e}")
        traceback.print_exc()

    return signals


# ---------------------------------------------------------------------------
# Read existing quantum feed context
# ---------------------------------------------------------------------------

def read_quantum_context() -> Dict:
    """Read shared quantum feed signals for cross-strategy awareness."""
    context = {}
    for fname in ["daily_thesis.json", "congress_trades.json", "cboe_vix_data.json",
                   "scalping_signals.json", "power_market_signals.json"]:
        path = QF / fname
        data = load_json(path)
        if data:
            context[fname.replace(".json", "")] = data
    return context


# ---------------------------------------------------------------------------
# MASTER RUNNER
# ---------------------------------------------------------------------------

def run_systematic_options() -> Dict:
    """Run all three systematic options selling sub-strategies. Returns master output dict."""
    log("Starting systematic options selling analysis (Wadhwa approach)...")
    now = et_now()

    all_signals = []
    strategy_results = {}

    # Read quantum feed context for awareness
    qf_context = read_quantum_context()
    if qf_context:
        log(f"  Loaded quantum context: {list(qf_context.keys())}")

    # 1. Short Straddle Scanner
    try:
        straddle_signals = scan_short_straddles()
        strategy_results["short_straddle"] = {
            "signals": straddle_signals,
            "count": len(straddle_signals),
        }
        all_signals.extend(straddle_signals)
        log(f"  Short Straddles: {len(straddle_signals)} signals")
    except Exception as e:
        log(f"  Short Straddles: ERROR - {e}")
        traceback.print_exc()
        strategy_results["short_straddle"] = {"error": str(e)}

    # 2. Short Strangle Scanner
    try:
        strangle_signals = scan_short_strangles()
        strategy_results["short_strangle"] = {
            "signals": strangle_signals,
            "count": len(strangle_signals),
        }
        all_signals.extend(strangle_signals)
        log(f"  Short Strangles: {len(strangle_signals)} signals")
    except Exception as e:
        log(f"  Short Strangles: ERROR - {e}")
        traceback.print_exc()
        strategy_results["short_strangle"] = {"error": str(e)}

    # 3. Theta Decay Regime
    try:
        theta_signals = analyze_theta_regime()
        strategy_results["theta_regime"] = {
            "signals": theta_signals,
            "count": len(theta_signals),
        }
        all_signals.extend(theta_signals)
        log(f"  Theta Regime: {len(theta_signals)} signals")
    except Exception as e:
        log(f"  Theta Regime: ERROR - {e}")
        traceback.print_exc()
        strategy_results["theta_regime"] = {"error": str(e)}

    master = {
        "timestamp": iso_now(),
        "session_date": now.strftime("%Y-%m-%d"),
        "source": "poojawadhwa.official (Deepak & Pooja Wadhwa, systematic options selling)",
        "method": "systematic_options_selling_3_strategies",
        "approach": "Sell straddles/strangles on SPY/QQQ/IWM, theta collection, strict stops",
        "strategies": strategy_results,
        "total_signals": len(all_signals),
        "all_signals": all_signals,
        "underlyings": UNDERLYINGS,
        "parameters": {
            "straddle_iv_rv_ratio": STRADDLE_IV_RV_RATIO,
            "straddle_vix_min": STRADDLE_VIX_MIN,
            "straddle_stop_pct": STRADDLE_STOP_PCT,
            "strangle_vix_range": [STRANGLE_VIX_LOW, STRANGLE_VIX_HIGH],
            "strangle_otm_pct": [STRANGLE_PUT_OTM_PCT, STRANGLE_CALL_OTM_PCT],
            "theta_ideal_dte": [THETA_IDEAL_DTE_LOW, THETA_IDEAL_DTE_HIGH],
            "theta_accelerating_dte": THETA_ACCELERATING_DTE,
        },
    }

    save_json(OUTPUT_PATH, master)
    append_jsonl(HISTORY_PATH, {
        "timestamp": master["timestamp"],
        "session_date": master["session_date"],
        "total_signals": len(all_signals),
        "straddles": len(strategy_results.get("short_straddle", {}).get("signals", [])),
        "strangles": len(strategy_results.get("short_strangle", {}).get("signals", [])),
        "theta": len(strategy_results.get("theta_regime", {}).get("signals", [])),
    })

    log(f"Systematic options selling complete: {len(all_signals)} total signals")
    return master


if __name__ == "__main__":
    master = run_systematic_options()
    print(f"\n{'='*60}")
    print(f"SYSTEMATIC OPTIONS SELLING — {master['session_date']}")
    print(f"Source: {master['source']}")
    print(f"{'='*60}")
    print(f"Total signals: {master['total_signals']}")
    for sig in master["all_signals"]:
        print(f"  [{sig['strategy']}] {sig['symbol']} {sig['signal']}: "
              f"{sig['rationale'][:90]}...")
