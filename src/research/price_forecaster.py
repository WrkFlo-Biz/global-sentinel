#!/usr/bin/env python3
"""Global Sentinel P2-2 — Time Series Price Forecasting

Uses Chronos (Amazon) for probabilistic 1-5 day price forecasts.
Falls back to simple statistical forecasting if Chronos unavailable.

Runs twice daily: pre-market 8:00 AM ET, mid-day 12:00 PM ET
Writes to data/quantum_feed/price_forecasts.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.price_forecaster")

REPO_ROOT = Path("/opt/global-sentinel")
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "price_forecasts.json"
WATCHLIST_PATH = REPO_ROOT / "config" / "assets_watchlist.yaml"

# Forecast horizons in days
HORIZONS = [1, 2, 3, 5]
# Number of quantile samples for confidence intervals
NUM_SAMPLES = 20
CONTEXT_LENGTH = 60  # trading days of history


def _load_watchlist_symbols(max_symbols: int = 20) -> List[str]:
    """Load top watchlist symbols."""
    try:
        import yaml
        data = yaml.safe_load(WATCHLIST_PATH.read_text()) if WATCHLIST_PATH.exists() else {}
        symbols = []
        if isinstance(data, dict):
            for section in data.values():
                if isinstance(section, list):
                    for item in section:
                        if isinstance(item, str):
                            symbols.append(item)
                        elif isinstance(item, dict) and "symbol" in item:
                            symbols.append(item["symbol"])
        return symbols[:max_symbols] or _default_symbols()
    except Exception:
        return _default_symbols()


def _default_symbols() -> List[str]:
    return ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
            "TSLA", "AMD", "XOM", "GLD", "TLT", "RTX", "LMT", "CVX",
            "BA", "JPM", "XLE", "IWM"]


def _fetch_ohlcv(symbols: List[str], days: int = 120) -> Dict[str, pd.DataFrame]:
    """Fetch OHLCV data for symbols via yfinance."""
    result = {}
    try:
        import yfinance as yf
        data = yf.download(symbols, period=f"{days}d", progress=False, threads=True)
        if data.empty:
            return result
        for sym in symbols:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    df = data.xs(sym, axis=1, level=1) if sym in data.columns.get_level_values(1) else None
                    if df is None:
                        continue
                else:
                    df = data
                if df is not None and len(df) >= CONTEXT_LENGTH:
                    result[sym] = df.dropna()
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Failed to fetch OHLCV: {e}")
    return result


def _forecast_chronos(close_series: pd.Series, horizons: List[int]) -> Optional[Dict[str, Any]]:
    """Generate forecasts using Amazon Chronos foundation model."""
    try:
        import torch
        from chronos import ChronosPipeline

        pipeline = ChronosPipeline.from_pretrained(
            "amazon/chronos-t5-small",
            device_map="cpu",
            torch_dtype=torch.float32,
        )

        context = torch.tensor(close_series.values[-CONTEXT_LENGTH:], dtype=torch.float32)
        max_h = max(horizons)

        forecast = pipeline.predict(context.unsqueeze(0), max_h, num_samples=NUM_SAMPLES)
        # forecast shape: (1, num_samples, max_h)
        samples = forecast[0].numpy()  # (num_samples, max_h)

        last_price = float(close_series.iloc[-1])
        results = {}
        for h in horizons:
            h_samples = samples[:, h - 1]
            median = float(np.median(h_samples))
            lower = float(np.percentile(h_samples, 10))
            upper = float(np.percentile(h_samples, 90))
            results[f"{h}d"] = {
                "forecast_price": round(median, 2),
                "lower_bound_10pct": round(lower, 2),
                "upper_bound_90pct": round(upper, 2),
                "expected_return_pct": round((median - last_price) / last_price * 100, 2),
                "confidence": round(1.0 - (upper - lower) / last_price, 3),
            }
        return results
    except Exception as e:
        logger.warning(f"Chronos forecast failed: {e}")
        return None


def _forecast_statistical(close_series: pd.Series, horizons: List[int]) -> Dict[str, Any]:
    """Simple statistical forecast fallback using drift + volatility cone."""
    returns = close_series.pct_change().dropna()
    mu = float(returns.mean())
    sigma = float(returns.std())
    last_price = float(close_series.iloc[-1])

    # Exponentially weighted for recency bias
    ewm_mu = float(returns.ewm(span=20).mean().iloc[-1])
    ewm_sigma = float(returns.ewm(span=20).std().iloc[-1])

    # Blend historical and EWM
    blend_mu = 0.4 * mu + 0.6 * ewm_mu
    blend_sigma = 0.4 * sigma + 0.6 * ewm_sigma

    results = {}
    for h in horizons:
        drift = blend_mu * h
        vol = blend_sigma * np.sqrt(h)
        forecast = last_price * (1 + drift)
        lower = last_price * (1 + drift - 1.645 * vol)  # 10th percentile
        upper = last_price * (1 + drift + 1.645 * vol)  # 90th percentile
        conf = max(0.0, min(1.0, 1.0 - vol))
        results[f"{h}d"] = {
            "forecast_price": round(forecast, 2),
            "lower_bound_10pct": round(lower, 2),
            "upper_bound_90pct": round(upper, 2),
            "expected_return_pct": round(drift * 100, 2),
            "confidence": round(conf, 3),
        }
    return results


def generate_forecasts() -> Dict[str, Any]:
    """Generate price forecasts for all watchlist symbols."""
    symbols = _load_watchlist_symbols()
    ohlcv = _fetch_ohlcv(symbols)

    if not ohlcv:
        return {"error": "no OHLCV data available", "timestamp": datetime.now(timezone.utc).isoformat()}

    forecasts = {}
    method_used = "unknown"

    for sym, df in ohlcv.items():
        close = df["Close"] if "Close" in df.columns else df.iloc[:, 3]
        if len(close) < CONTEXT_LENGTH:
            continue

        # Try Chronos first, fall back to statistical
        result = _forecast_chronos(close, HORIZONS)
        if result is not None:
            method_used = "chronos-t5-small"
        else:
            result = _forecast_statistical(close, HORIZONS)
            method_used = "statistical-drift-vol"

        last_price = float(close.iloc[-1])
        forecasts[sym] = {
            "last_price": round(last_price, 2),
            "forecasts": result,
            "method": method_used,
        }

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "forecast_method": method_used,
        "horizons_days": HORIZONS,
        "num_symbols": len(forecasts),
        "symbols": forecasts,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Price forecasts generated for {len(forecasts)} symbols using {method_used}")

    return output


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = generate_forecasts()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
