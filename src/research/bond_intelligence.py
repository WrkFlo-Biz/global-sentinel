#!/usr/bin/env python3
"""
Global Sentinel — Bond Market Intelligence

Tracks Treasury yield curve from FRED (2Y, 5Y, 10Y, 30Y), computes slope,
steepness, inversion status, credit spreads (HYG vs TLT), and term premium.

Signals:
  - Inverted curve -> recession warning
  - Steepening     -> growth signal
  - Flattening     -> uncertainty

Output: data/quantum_feed/bond_intelligence.json
Runs every 30 min during market hours
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger("global_sentinel.bond_intelligence")

try:
    import yfinance as yf
except ImportError:
    yf = None

REPO_ROOT = Path(__file__).resolve().parents[2]
QUANTUM_FEED_DIR = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QUANTUM_FEED_DIR / "bond_intelligence.json"

# FRED series IDs for Treasury constant maturity yields
FRED_SERIES = {
    "DGS2":  "2Y Treasury",
    "DGS5":  "5Y Treasury",
    "DGS10": "10Y Treasury",
    "DGS30": "30Y Treasury",
}

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# ETF proxies for credit spread calculation
CREDIT_ETFS = {
    "HYG": "iShares iBoxx High Yield Corporate Bond ETF",
    "TLT": "iShares 20+ Year Treasury Bond ETF",
    "LQD": "iShares Investment Grade Corporate Bond ETF",
}


# ---------------------------------------------------------------------------
# FRED data fetching
# ---------------------------------------------------------------------------

def _get_fred_api_key() -> str:
    """Get FRED API key from env or .env file."""
    key = os.environ.get("FRED_API_KEY", "")
    if key and key != "free_key_needed":
        return key
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("FRED_API_KEY="):
                val = line.split("=", 1)[1].strip()
                if val and val != "free_key_needed":
                    return val
    return ""


def _fetch_fred_series(series_id: str, api_key: str, days_back: int = 90) -> pd.Series:
    """Fetch a FRED series and return as pandas Series indexed by date."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days_back)

    try:
        resp = requests.get(
            FRED_BASE_URL,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start_date.isoformat(),
                "observation_end": end_date.isoformat(),
                "sort_order": "desc",
                "limit": 100,
            },
            timeout=15,
        )
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        records = []
        for o in obs:
            if o["value"] != ".":
                records.append({
                    "date": pd.Timestamp(o["date"]),
                    "value": float(o["value"]),
                })
        if not records:
            return pd.Series(dtype=float)
        df = pd.DataFrame(records).set_index("date").sort_index()
        return df["value"]
    except Exception as exc:
        logger.error("Failed to fetch FRED series %s: %s", series_id, exc)
        return pd.Series(dtype=float)


def _fetch_yields_from_yfinance() -> Dict[str, Optional[float]]:
    """Fallback: use Treasury ETF proxies to estimate yields (rough)."""
    # Use ^TNX (10Y), ^FVX (5Y), ^TYX (30Y) if available
    yield_tickers = {
        "^IRX": "3M",   # 13-week T-bill
        "^FVX": "5Y",
        "^TNX": "10Y",
        "^TYX": "30Y",
    }
    results = {}
    for ticker, label in yield_tickers.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if not hist.empty:
                results[label] = round(float(hist["Close"].iloc[-1]), 3)
            else:
                results[label] = None
        except Exception:
            results[label] = None
    return results


# ---------------------------------------------------------------------------
# Yield curve analysis
# ---------------------------------------------------------------------------

def _compute_yield_curve(yields: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """Compute yield curve metrics from fetched yields."""
    y2 = yields.get("2Y")
    y5 = yields.get("5Y")
    y10 = yields.get("10Y")
    y30 = yields.get("30Y")

    curve = {
        "yields": {k: v for k, v in yields.items() if v is not None},
    }

    # 10Y-2Y spread (classic indicator)
    if y10 is not None and y2 is not None:
        spread_10_2 = round(y10 - y2, 3)
        curve["spread_10y_2y"] = spread_10_2
        curve["inverted"] = spread_10_2 < 0
    else:
        curve["spread_10y_2y"] = None
        curve["inverted"] = None

    # 30Y-2Y steepness
    if y30 is not None and y2 is not None:
        curve["spread_30y_2y"] = round(y30 - y2, 3)
    else:
        curve["spread_30y_2y"] = None

    # 10Y-5Y mid-curve
    if y10 is not None and y5 is not None:
        curve["spread_10y_5y"] = round(y10 - y5, 3)
    else:
        curve["spread_10y_5y"] = None

    # Term premium rough estimate: 10Y - avg(2Y, 5Y)
    if y10 is not None and y2 is not None and y5 is not None:
        avg_short = (y2 + y5) / 2
        curve["term_premium_est"] = round(y10 - avg_short, 3)
    else:
        curve["term_premium_est"] = None

    # Signal classification
    spread = curve.get("spread_10y_2y")
    if spread is not None:
        if spread < -0.1:
            curve["signal"] = "recession_warning"
            curve["signal_strength"] = "strong" if spread < -0.5 else "moderate"
        elif spread < 0.2:
            curve["signal"] = "flattening_uncertainty"
            curve["signal_strength"] = "moderate"
        elif spread < 1.0:
            curve["signal"] = "normal_curve"
            curve["signal_strength"] = "neutral"
        else:
            curve["signal"] = "steepening_growth"
            curve["signal_strength"] = "strong" if spread > 1.5 else "moderate"
    else:
        curve["signal"] = "unknown"
        curve["signal_strength"] = "none"

    return curve


def _compute_credit_spreads(repo_root: str) -> Dict[str, Any]:
    """Compute credit spread proxy using HYG vs TLT."""
    if yf is None:
        return {"error": "yfinance not installed"}

    symbols = list(CREDIT_ETFS.keys())
    try:
        data = yf.download(symbols, period="6mo", interval="1d",
                           group_by="ticker", progress=False)
    except Exception as exc:
        return {"error": str(exc)}

    result = {}
    for sym in symbols:
        try:
            close = data[sym]["Close"].dropna()
            if len(close) < 21:
                continue
            ret_1m = round(float((close.iloc[-1] / close.iloc[-21] - 1) * 100), 2)
            ret_3m = round(float((close.iloc[-1] / close.iloc[-63] - 1) * 100), 2) if len(close) >= 63 else None
            result[sym] = {
                "last_price": round(float(close.iloc[-1]), 2),
                "return_1m_pct": ret_1m,
                "return_3m_pct": ret_3m,
            }
        except Exception:
            continue

    # HYG/TLT ratio as credit spread proxy
    try:
        hyg_close = data["HYG"]["Close"].dropna()
        tlt_close = data["TLT"]["Close"].dropna()
        aligned = pd.DataFrame({"hyg": hyg_close, "tlt": tlt_close}).dropna()
        if len(aligned) >= 21:
            ratio = aligned["hyg"] / aligned["tlt"]
            current_ratio = float(ratio.iloc[-1])
            ratio_1m_ago = float(ratio.iloc[-21])
            result["hyg_tlt_ratio"] = round(current_ratio, 4)
            result["hyg_tlt_ratio_change_1m"] = round(current_ratio - ratio_1m_ago, 4)
            # Falling ratio = widening spreads (risk-off)
            if current_ratio < ratio_1m_ago:
                result["credit_signal"] = "widening_risk_off"
            else:
                result["credit_signal"] = "tightening_risk_on"
    except Exception:
        result["credit_signal"] = "unknown"

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def analyze_bonds(repo_root: str = "/opt/global-sentinel") -> Dict[str, Any]:
    """Run bond market intelligence analysis."""
    repo = Path(repo_root)
    output_path = repo / "data" / "quantum_feed" / "bond_intelligence.json"

    # Try FRED first
    api_key = _get_fred_api_key()
    yields: Dict[str, Optional[float]] = {}

    if api_key:
        logger.info("Fetching yields from FRED")
        for series_id, label in FRED_SERIES.items():
            s = _fetch_fred_series(series_id, api_key)
            if not s.empty:
                maturity = label.split()[0]  # "2Y", "5Y", etc.
                yields[maturity] = round(float(s.iloc[-1]), 3)
    
    # Fallback to yfinance yield indices
    if not yields or len(yields) < 2:
        logger.info("Falling back to yfinance yield proxies")
        yf_yields = _fetch_yields_from_yfinance()
        for k, v in yf_yields.items():
            if v is not None and k not in yields:
                yields[k] = v

    # Yield curve analysis
    curve_analysis = _compute_yield_curve(yields)

    # Credit spreads
    credit_spreads = _compute_credit_spreads(repo_root)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "yield_curve": curve_analysis,
        "credit_spreads": credit_spreads,
        "composite_signal": _composite_signal(curve_analysis, credit_spreads),
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str))
        logger.info("Wrote bond intelligence to %s", output_path)
    except Exception as exc:
        logger.error("Failed to write output: %s", exc)

    return result


def _composite_signal(curve: Dict[str, Any], credit: Dict[str, Any]) -> Dict[str, Any]:
    """Derive composite bond market signal."""
    curve_sig = curve.get("signal", "unknown")
    credit_sig = credit.get("credit_signal", "unknown")

    # Risk matrix
    if curve_sig == "recession_warning" and credit_sig == "widening_risk_off":
        composite = "strongly_bearish"
        action = "reduce equity exposure, favor defensive/bonds"
    elif curve_sig == "recession_warning":
        composite = "bearish"
        action = "caution on cyclicals, monitor credit"
    elif curve_sig == "steepening_growth" and credit_sig == "tightening_risk_on":
        composite = "strongly_bullish"
        action = "favor cyclicals, growth equities"
    elif curve_sig == "steepening_growth":
        composite = "bullish"
        action = "constructive on equities"
    elif curve_sig == "flattening_uncertainty":
        composite = "cautious"
        action = "reduce position sizes, favor quality"
    else:
        composite = "neutral"
        action = "maintain balanced allocation"

    return {
        "signal": composite,
        "curve_component": curve_sig,
        "credit_component": credit_sig,
        "suggested_action": action,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = analyze_bonds()
    print(json.dumps(result, indent=2, default=str))
