#!/usr/bin/env python3
"""
Global Sentinel — Alpha Signal Validator

Uses Alphalens to validate alpha signals before deployment.
Reads signals from quantum_feed JSON files, computes information
coefficient (IC), IC t-stat, quantile returns, and turnover.

Runs weekly on Sunday at 8:00 UTC.
Output: data/quantum_feed/alpha_validation.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.alpha_validator")

REPO_ROOT = Path(__file__).resolve().parents[2]
QUANTUM_FEED_DIR = REPO_ROOT / "data" / "quantum_feed"

SIGNAL_FILES = [
    "qlib_alpha_scores.json",
    "ensemble_signals.json",
    "topo_arb_signals.json",
]

# ---------------------------------------------------------------------------
# Signal loading
# ---------------------------------------------------------------------------

def _load_signal_file(name: str) -> Optional[Dict[str, Any]]:
    """Load a signal JSON from quantum_feed."""
    path = QUANTUM_FEED_DIR / name
    if not path.exists():
        logger.warning("Signal file not found: %s", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", name, exc)
        return None


def _extract_signals(data: Dict[str, Any], source_name: str) -> pd.DataFrame:
    """
    Extract a DataFrame with columns [date, asset, signal] from various
    signal file formats.
    """
    rows = []

    # Format 1: {"signals": [{"ticker": ..., "score": ..., "timestamp": ...}]}
    if "signals" in data and isinstance(data["signals"], list):
        for s in data["signals"]:
            ticker = s.get("ticker") or s.get("symbol") or s.get("asset")
            score = s.get("score") or s.get("alpha") or s.get("signal") or s.get("value")
            ts = s.get("timestamp") or s.get("date") or s.get("generated_at")
            if ticker and score is not None:
                rows.append({"asset": str(ticker), "signal": float(score), "date": str(ts) if ts else None})

    # Format 2: {"AAPL": {"score": 0.8}, "MSFT": ...}
    elif all(isinstance(v, dict) for v in data.values() if isinstance(v, dict)):
        ts = data.get("timestamp") or data.get("generated_at") or data.get("date")
        for key, val in data.items():
            if isinstance(val, dict) and ("score" in val or "alpha" in val or "signal" in val):
                score = val.get("score") or val.get("alpha") or val.get("signal") or 0.0
                rows.append({"asset": str(key), "signal": float(score), "date": str(ts) if ts else None})

    # Format 3: list at top level
    elif isinstance(data, list):
        for s in data:
            if isinstance(s, dict):
                ticker = s.get("ticker") or s.get("symbol") or s.get("asset")
                score = s.get("score") or s.get("alpha") or s.get("signal")
                ts = s.get("timestamp") or s.get("date")
                if ticker and score is not None:
                    rows.append({"asset": str(ticker), "signal": float(score), "date": str(ts) if ts else None})

    if not rows:
        logger.warning("No signals extracted from %s", source_name)
        return pd.DataFrame(columns=["date", "asset", "signal"])

    df = pd.DataFrame(rows)
    df["source"] = source_name
    return df


# ---------------------------------------------------------------------------
# Fetch price data for validation
# ---------------------------------------------------------------------------

def _fetch_prices(tickers: List[str], lookback_days: int = 90) -> pd.DataFrame:
    """Fetch historical prices via Alpaca or yfinance for IC computation."""
    try:
        import yfinance as yf
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        data = yf.download(tickers, start=start.strftime("%Y-%m-%d"),
                           end=end.strftime("%Y-%m-%d"), progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            prices = data["Close"]
        else:
            prices = data[["Close"]]
            prices.columns = tickers[:1]
        return prices
    except Exception as exc:
        logger.warning("yfinance price fetch failed: %s", exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Validation metrics (lightweight, no full alphalens pipeline dependency)
# ---------------------------------------------------------------------------

def _compute_ic(signals_df: pd.DataFrame, prices: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute Information Coefficient (rank correlation of signal vs forward returns)
    and related statistics.
    """
    if signals_df.empty or prices.empty:
        return {"ic_mean": None, "ic_std": None, "ic_tstat": None, "note": "insufficient data"}

    # Forward returns (1-day, 5-day)
    fwd_1d = prices.pct_change().shift(-1)
    fwd_5d = prices.pct_change(5).shift(-5)

    results = {}
    for horizon_name, fwd_ret in [("1d", fwd_1d), ("5d", fwd_5d)]:
        ic_values = []
        for date_str, grp in signals_df.groupby("date"):
            try:
                dt = pd.Timestamp(date_str)
                if dt not in fwd_ret.index:
                    continue
                day_returns = fwd_ret.loc[dt]
                common = set(grp["asset"].values) & set(day_returns.dropna().index)
                if len(common) < 3:
                    continue
                common = sorted(common)
                sig_vals = grp.set_index("asset").loc[common, "signal"].values.astype(float)
                ret_vals = day_returns[common].values.astype(float)
                from scipy.stats import spearmanr
                ic, _ = spearmanr(sig_vals, ret_vals)
                if not np.isnan(ic):
                    ic_values.append(ic)
            except Exception:
                continue

        if ic_values:
            ic_arr = np.array(ic_values)
            ic_mean = float(np.mean(ic_arr))
            ic_std = float(np.std(ic_arr))
            ic_tstat = ic_mean / (ic_std / np.sqrt(len(ic_arr))) if ic_std > 0 else 0.0
            results[horizon_name] = {
                "ic_mean": round(ic_mean, 4),
                "ic_std": round(ic_std, 4),
                "ic_tstat": round(ic_tstat, 4),
                "n_periods": len(ic_values),
                "predictive": abs(ic_tstat) > 2.0,
            }
        else:
            results[horizon_name] = {"ic_mean": None, "ic_tstat": None, "note": "no overlapping data"}

    return results


def _compute_turnover(signals_df: pd.DataFrame) -> float:
    """Estimate signal turnover as fraction of portfolio changing per period."""
    dates = sorted(signals_df["date"].unique())
    if len(dates) < 2:
        return 0.0

    turnovers = []
    for i in range(1, len(dates)):
        prev = set(signals_df[signals_df["date"] == dates[i-1]]["asset"].values)
        curr = set(signals_df[signals_df["date"] == dates[i]]["asset"].values)
        if prev or curr:
            union = prev | curr
            changed = prev.symmetric_difference(curr)
            turnovers.append(len(changed) / len(union) if union else 0.0)
    return round(float(np.mean(turnovers)), 4) if turnovers else 0.0


def _compute_quantile_returns(signals_df: pd.DataFrame, prices: pd.DataFrame,
                               n_quantiles: int = 5) -> Dict[str, Any]:
    """Compute mean forward returns by signal quantile."""
    if signals_df.empty or prices.empty:
        return {}

    fwd_5d = prices.pct_change(5).shift(-5)
    all_rows = []

    for date_str, grp in signals_df.groupby("date"):
        try:
            dt = pd.Timestamp(date_str)
            if dt not in fwd_5d.index:
                continue
            day_returns = fwd_5d.loc[dt]
            common = sorted(set(grp["asset"].values) & set(day_returns.dropna().index))
            if len(common) < n_quantiles:
                continue
            sub = grp.set_index("asset").loc[common].copy()
            sub["fwd_ret"] = day_returns[common].values
            sub["quantile"] = pd.qcut(sub["signal"], n_quantiles, labels=False, duplicates="drop")
            all_rows.append(sub)
        except Exception:
            continue

    if not all_rows:
        return {}

    combined = pd.concat(all_rows)
    q_returns = combined.groupby("quantile")["fwd_ret"].mean()
    return {f"Q{int(k)+1}": round(float(v), 6) for k, v in q_returns.items()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> Dict[str, Any]:
    """Run alpha validation for all signal sources."""
    logger.info("Starting alpha signal validation")
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signals": {},
    }

    all_tickers = set()
    source_signals = {}

    for fname in SIGNAL_FILES:
        data = _load_signal_file(fname)
        if data is None:
            results["signals"][fname] = {"status": "file_not_found"}
            continue
        df = _extract_signals(data, fname)
        if df.empty:
            results["signals"][fname] = {"status": "no_signals_extracted"}
            continue
        source_signals[fname] = df
        all_tickers.update(df["asset"].unique())

    if not all_tickers:
        results["status"] = "no_signals_available"
        _save_results(results)
        return results

    # Fetch prices for all tickers
    tickers = sorted(all_tickers)[:100]  # cap at 100
    prices = _fetch_prices(tickers)

    for fname, df in source_signals.items():
        logger.info("Validating %s (%d signals)", fname, len(df))
        ic_results = _compute_ic(df, prices)
        turnover = _compute_turnover(df)
        quantile_rets = _compute_quantile_returns(df, prices)

        noise_vs_signal = "signal"
        if ic_results.get("5d", {}).get("ic_tstat") is not None:
            if abs(ic_results["5d"]["ic_tstat"]) < 1.5:
                noise_vs_signal = "likely_noise"
            elif abs(ic_results["5d"]["ic_tstat"]) < 2.0:
                noise_vs_signal = "weak_signal"

        results["signals"][fname] = {
            "status": "validated",
            "n_signals": len(df),
            "n_unique_assets": df["asset"].nunique(),
            "information_coefficient": ic_results,
            "turnover": turnover,
            "quantile_returns_5d": quantile_rets,
            "assessment": noise_vs_signal,
        }

    # Summary
    n_predictive = sum(
        1 for v in results["signals"].values()
        if isinstance(v, dict) and v.get("assessment") == "signal"
    )
    results["summary"] = {
        "total_sources": len(SIGNAL_FILES),
        "validated": len(source_signals),
        "predictive_signals": n_predictive,
    }

    _save_results(results)
    return results


def _save_results(results: Dict[str, Any]) -> None:
    output_path = QUANTUM_FEED_DIR / "alpha_validation.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    logger.info("Alpha validation saved to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = run()
    print(json.dumps(result, indent=2, default=str))
