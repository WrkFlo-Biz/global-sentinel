#!/usr/bin/env python3
"""
Global Sentinel — Technical Analysis Bridge

Computes RSI, MACD, Bollinger Bands, VWAP, SMA crossovers,
support/resistance levels, ATR, and an overall technical score
for top 20 watchlist symbols via yfinance.

Output: data/quantum_feed/technical_analysis.json
Tier 2, trust 0.7, TTL 15 min
"""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.technical_analysis_bridge")

try:
    import yaml
except ImportError:
    yaml = None

try:
    import yfinance as yf
except ImportError:
    yf = None

DEFAULT_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMZN", "META", "MSFT",
    "AMD", "GOOGL", "NFLX", "JPM", "V", "UNH", "XOM", "LLY",
    "AVGO", "MA", "COST", "HD",
]


def _load_watchlist(repo_root: Path) -> List[str]:
    """Load top 20 equity symbols from expanded watchlist."""
    wl_path = repo_root / "config" / "expanded_watchlist.yaml"
    if yaml is None or not wl_path.exists():
        return DEFAULT_SYMBOLS[:20]
    try:
        data = yaml.safe_load(wl_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return DEFAULT_SYMBOLS[:20]
    symbols = []
    skip = {"USD", "XAU", "UST", "VIX", "BRN", "BZ=F", "^", "GC=F"}
    for cat_data in (data.get("categories") or {}).values():
        for sym in (cat_data.get("symbols") or []):
            s = str(sym).strip()
            if s and not any(p in s for p in skip):
                symbols.append(s)
    seen = set()
    out = []
    for s in (symbols if symbols else DEFAULT_SYMBOLS):
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= 20:
            break
    return out or DEFAULT_SYMBOLS[:20]


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    pct_b = (series - lower) / (upper - lower).replace(0, np.nan)
    return upper, sma, lower, pct_b


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def _support_resistance(high: pd.Series, low: pd.Series, lookback: int = 20):
    """Find recent swing highs/lows as support/resistance."""
    recent_high = high.tail(lookback)
    recent_low = low.tail(lookback)
    resistance = float(recent_high.max())
    support = float(recent_low.min())
    # Also look at second-highest/lowest for intermediate levels
    sorted_highs = recent_high.nlargest(3)
    sorted_lows = recent_low.nsmallest(3)
    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "support_levels": [round(float(v), 2) for v in sorted_lows.values],
        "resistance_levels": [round(float(v), 2) for v in sorted_highs.values],
    }


def _compute_technical_score(rsi_val, macd_hist, pct_b, sma20, sma50, sma200, price):
    """Compute overall score from -10 (extremely bearish) to +10 (extremely bullish)."""
    score = 0.0

    # RSI contribution: -2 to +2
    if rsi_val is not None and not np.isnan(rsi_val):
        if rsi_val > 70:
            score -= min(2, (rsi_val - 70) / 15)
        elif rsi_val < 30:
            score += min(2, (30 - rsi_val) / 15)
        elif rsi_val > 50:
            score += 0.5
        else:
            score -= 0.5

    # MACD histogram: -2 to +2
    if macd_hist is not None and not np.isnan(macd_hist):
        score += max(-2, min(2, macd_hist * 10))

    # Bollinger %B: -2 to +2
    if pct_b is not None and not np.isnan(pct_b):
        if pct_b > 1.0:
            score -= 1.5
        elif pct_b < 0.0:
            score += 1.5
        else:
            score += (pct_b - 0.5) * 2

    # SMA crossovers: -2 to +2
    sma_score = 0
    if price is not None and sma20 is not None and not np.isnan(sma20):
        sma_score += 0.5 if price > sma20 else -0.5
    if price is not None and sma50 is not None and not np.isnan(sma50):
        sma_score += 0.5 if price > sma50 else -0.5
    if price is not None and sma200 is not None and not np.isnan(sma200):
        sma_score += 1.0 if price > sma200 else -1.0
    # Golden/death cross
    if sma50 is not None and sma200 is not None and not (np.isnan(sma50) or np.isnan(sma200)):
        if sma50 > sma200:
            sma_score += 0.5
        else:
            sma_score -= 0.5
    score += max(-2, min(2, sma_score))

    return round(max(-10, min(10, score)), 1)


def _detect_crossovers(sma50_series: pd.Series, sma200_series: pd.Series):
    """Detect golden cross / death cross in last 5 days."""
    if len(sma50_series) < 6 or len(sma200_series) < 6:
        return None
    recent_50 = sma50_series.tail(5)
    recent_200 = sma200_series.tail(5)
    diff = recent_50 - recent_200
    for i in range(1, len(diff)):
        if diff.iloc[i-1] < 0 and diff.iloc[i] > 0:
            return "golden_cross"
        if diff.iloc[i-1] > 0 and diff.iloc[i] < 0:
            return "death_cross"
    return None


def analyze_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Run full technical analysis on one symbol."""
    if yf is None:
        return None
    try:
        ticker = yf.Ticker(symbol)
        # Daily data for SMA/MACD/RSI/BB/ATR
        daily = ticker.history(period="1y", interval="1d")
        if daily.empty or len(daily) < 50:
            return None

        close = daily["Close"]
        high = daily["High"]
        low = daily["Low"]
        volume = daily["Volume"]
        price = float(close.iloc[-1])

        # RSI
        rsi_series = _rsi(close, 14)
        rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else None

        # MACD
        macd_line, signal_line, histogram = _macd(close)
        macd_val = float(macd_line.iloc[-1]) if not macd_line.empty else None
        signal_val = float(signal_line.iloc[-1]) if not signal_line.empty else None
        hist_val = float(histogram.iloc[-1]) if not histogram.empty else None

        # Bollinger Bands
        bb_upper, bb_mid, bb_lower, pct_b = _bollinger(close)
        bb_upper_val = float(bb_upper.iloc[-1]) if not bb_upper.empty else None
        bb_lower_val = float(bb_lower.iloc[-1]) if not bb_lower.empty else None
        pct_b_val = float(pct_b.iloc[-1]) if not pct_b.empty else None

        # SMAs
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        sma20_val = float(sma20.iloc[-1]) if not sma20.empty and not np.isnan(sma20.iloc[-1]) else None
        sma50_val = float(sma50.iloc[-1]) if not sma50.empty and not np.isnan(sma50.iloc[-1]) else None
        sma200_val = float(sma200.iloc[-1]) if len(close) >= 200 and not np.isnan(sma200.iloc[-1]) else None

        # Crossover detection
        crossover = _detect_crossovers(sma50, sma200) if len(close) >= 200 else None

        # ATR
        atr_series = _atr(high, low, close)
        atr_val = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else None

        # Support/Resistance
        sr = _support_resistance(high, low)

        # VWAP (intraday — use today only)
        try:
            intraday = ticker.history(period="1d", interval="5m")
            if not intraday.empty and "Volume" in intraday.columns:
                typical_price = (intraday["High"] + intraday["Low"] + intraday["Close"]) / 3
                cum_tp_vol = (typical_price * intraday["Volume"]).cumsum()
                cum_vol = intraday["Volume"].cumsum()
                vwap_series = cum_tp_vol / cum_vol.replace(0, np.nan)
                vwap_val = round(float(vwap_series.iloc[-1]), 2) if not vwap_series.empty else None
            else:
                vwap_val = None
        except Exception:
            vwap_val = None

        # Technical score
        tech_score = _compute_technical_score(
            rsi_val, hist_val, pct_b_val, sma20_val, sma50_val, sma200_val, price
        )

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "rsi_14": round(rsi_val, 2) if rsi_val is not None else None,
            "macd": {
                "macd_line": round(macd_val, 4) if macd_val else None,
                "signal_line": round(signal_val, 4) if signal_val else None,
                "histogram": round(hist_val, 4) if hist_val else None,
            },
            "bollinger_bands": {
                "upper": round(bb_upper_val, 2) if bb_upper_val else None,
                "lower": round(bb_lower_val, 2) if bb_lower_val else None,
                "pct_b": round(pct_b_val, 4) if pct_b_val else None,
            },
            "vwap": vwap_val,
            "sma": {
                "sma_20": round(sma20_val, 2) if sma20_val else None,
                "sma_50": round(sma50_val, 2) if sma50_val else None,
                "sma_200": round(sma200_val, 2) if sma200_val else None,
                "crossover_signal": crossover,
            },
            "support_resistance": sr,
            "atr_14": round(atr_val, 2) if atr_val else None,
            "technical_score": tech_score,
        }
    except Exception as exc:
        logger.warning("Technical analysis failed for %s: %s", symbol, exc)
        return None


class TechnicalAnalysisBridge:
    """Bridge wrapper for technical analysis."""

    DISPLAY_NAME = "technical_analysis"
    CATEGORY = "technical"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "technical_analysis.json"
        self.symbols = _load_watchlist(self.repo_root)

    def poll(self) -> Dict[str, Any]:
        results = []
        errors = []
        for sym in self.symbols:
            try:
                r = analyze_symbol(sym)
                if r:
                    results.append(r)
            except Exception as exc:
                errors.append({"symbol": sym, "error": str(exc)})

        bullish = [r for r in results if r.get("technical_score", 0) > 3]
        bearish = [r for r in results if r.get("technical_score", 0) < -3]

        output = {
            "source": "technical_analysis_bridge",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "symbols_analyzed": len(results),
            "top_bullish": sorted(bullish, key=lambda x: x["technical_score"], reverse=True)[:5],
            "top_bearish": sorted(bearish, key=lambda x: x["technical_score"])[:5],
            "all_scores": results,
            "errors": errors,
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(output, indent=2, default=str))
        logger.info("Technical analysis: %d symbols scored, %d bullish, %d bearish",
                     len(results), len(bullish), len(bearish))

        return {
            "source": "technical_analysis_bridge",
            "source_tier": "tier_2_operational",
            "trust_weight": 0.7,
            "timestamp_utc": output["timestamp_utc"],
            "fresh": True,
            "data": output,
        }


def main():
    logging.basicConfig(level=logging.INFO)
    bridge = TechnicalAnalysisBridge()
    result = bridge.poll()
    print(json.dumps(result, indent=2, default=str)[:3000])


if __name__ == "__main__":
    main()
