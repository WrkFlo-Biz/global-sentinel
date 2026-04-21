#!/usr/bin/env python3
"""Global Sentinel P2-3 — VectorBT Walk-Forward Optimization

Backtests the momentum/regime strategy on historical data using walk-forward.
60-day training window, 30-day validation, rolls forward.
Tests parameters: holding period, stop loss, take profit.
Outputs optimal parameters per regime (NORMAL, ELEVATED, CRISIS).

Runs weekly on Saturdays.
Writes to data/quantum_feed/optimal_parameters.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.walk_forward_optimizer")

REPO_ROOT = Path("/opt/global-sentinel")
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "optimal_parameters.json"
REGIME_PATH = REPO_ROOT / "data" / "quantum_feed" / "hmm_regime.json"

# Walk-forward parameters
TRAIN_WINDOW = 60   # trading days
VAL_WINDOW = 30     # trading days
MIN_HISTORY = 252   # 1 year minimum

# Parameter grid
HOLDING_PERIODS = [1, 2, 4, 8]           # hours (mapped to bars)
STOP_LOSSES = [0.05, 0.10, 0.15]         # 5%, 10%, 15%
TAKE_PROFITS = [0.50, 1.00, 2.00]        # 50%, 100%, 200% (of entry)

# Test symbols
TEST_SYMBOLS = ["SPY", "QQQ", "IWM", "XLE", "GLD", "TLT", "RTX", "XOM"]


def _fetch_daily_data(symbols: List[str], days: int = 504) -> Dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for backtesting."""
    result = {}
    try:
        import yfinance as yf
        data = yf.download(symbols, period=f"{days}d", progress=False, threads=True)
        if data.empty:
            return result
        for sym in symbols:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    df = pd.DataFrame({
                        "Open": data[("Open", sym)],
                        "High": data[("High", sym)],
                        "Low": data[("Low", sym)],
                        "Close": data[("Close", sym)],
                        "Volume": data[("Volume", sym)],
                    }).dropna()
                else:
                    df = data.dropna()
                if len(df) >= MIN_HISTORY:
                    result[sym] = df
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Failed to fetch data: {e}")
    return result


def _simulate_momentum_strategy(
    prices: pd.DataFrame,
    holding_days: int,
    stop_loss: float,
    take_profit: float,
) -> Dict[str, float]:
    """Simulate a simple momentum strategy with given parameters.

    Entry: buy when 5-day return > 0 (momentum positive)
    Exit: after holding_days, or stop_loss/take_profit hit
    """
    close = prices["Close"].values
    high = prices["High"].values
    low = prices["Low"].values
    n = len(close)

    if n < 10:
        return {"total_return": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "num_trades": 0}

    trades = []
    i = 5  # start after lookback

    while i < n - 1:
        # Momentum signal: 5-day return positive
        mom = (close[i] - close[i - 5]) / close[i - 5]
        if mom > 0:
            entry_price = close[i]
            exit_price = entry_price
            exit_day = min(i + holding_days, n - 1)

            # Check stop/take within holding period
            for j in range(i + 1, exit_day + 1):
                # Check stop loss (intraday low)
                if (entry_price - low[j]) / entry_price >= stop_loss:
                    exit_price = entry_price * (1 - stop_loss)
                    exit_day = j
                    break
                # Check take profit (intraday high)
                if (high[j] - entry_price) / entry_price >= take_profit:
                    exit_price = entry_price * (1 + take_profit)
                    exit_day = j
                    break
                exit_price = close[j]

            ret = (exit_price - entry_price) / entry_price
            trades.append(ret)
            i = exit_day + 1
        else:
            i += 1

    if not trades:
        return {"total_return": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "num_trades": 0}

    trades_arr = np.array(trades)
    total_return = float(np.prod(1 + trades_arr) - 1)
    sharpe = float(np.mean(trades_arr) / np.std(trades_arr)) * np.sqrt(252 / max(holding_days, 1)) if np.std(trades_arr) > 0 else 0
    win_rate = float(np.mean(trades_arr > 0))

    # Max drawdown from cumulative returns
    cum = np.cumprod(1 + trades_arr)
    peak = np.maximum.accumulate(cum)
    dd = (peak - cum) / peak
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0

    return {
        "total_return": round(total_return, 4),
        "sharpe": round(sharpe, 4),
        "max_dd": round(max_dd, 4),
        "win_rate": round(win_rate, 4),
        "num_trades": len(trades),
    }


def _classify_regime_for_window(prices: pd.DataFrame) -> str:
    """Simple regime classification based on volatility and trend."""
    close = prices["Close"]
    returns = close.pct_change().dropna()
    vol = float(returns.std()) * np.sqrt(252)
    trend = float((close.iloc[-1] - close.iloc[0]) / close.iloc[0])

    if vol > 0.30 or trend < -0.10:
        return "CRISIS"
    elif vol > 0.20 or trend < -0.03:
        return "ELEVATED"
    else:
        return "NORMAL"


def run_walk_forward_optimization() -> Dict[str, Any]:
    """Run walk-forward optimization across parameter grid."""
    try:
        import vectorbt as vbt
        has_vbt = True
    except ImportError:
        has_vbt = False
        logger.warning("vectorbt not available, using built-in backtester")

    data = _fetch_daily_data(TEST_SYMBOLS)
    if not data:
        return {"error": "no data available", "timestamp": datetime.now(timezone.utc).isoformat()}

    # Collect results per regime
    regime_results: Dict[str, List[Dict[str, Any]]] = {
        "NORMAL": [],
        "ELEVATED": [],
        "CRISIS": [],
    }

    param_grid = list(product(HOLDING_PERIODS, STOP_LOSSES, TAKE_PROFITS))
    logger.info(f"Testing {len(param_grid)} parameter combinations across {len(data)} symbols")

    for sym, df in data.items():
        n = len(df)
        if n < TRAIN_WINDOW + VAL_WINDOW:
            continue

        # Walk forward
        start = 0
        while start + TRAIN_WINDOW + VAL_WINDOW <= n:
            train_df = df.iloc[start:start + TRAIN_WINDOW]
            val_df = df.iloc[start + TRAIN_WINDOW:start + TRAIN_WINDOW + VAL_WINDOW]
            regime = _classify_regime_for_window(train_df)

            # Find best params on training window
            best_sharpe = -999
            best_params = None
            for hold, sl, tp in param_grid:
                result = _simulate_momentum_strategy(train_df, hold, sl, tp)
                if result["sharpe"] > best_sharpe and result["num_trades"] >= 3:
                    best_sharpe = result["sharpe"]
                    best_params = (hold, sl, tp)

            if best_params is None:
                start += VAL_WINDOW
                continue

            # Validate on out-of-sample
            hold, sl, tp = best_params
            val_result = _simulate_momentum_strategy(val_df, hold, sl, tp)
            val_result["holding_days"] = hold
            val_result["stop_loss"] = sl
            val_result["take_profit"] = tp
            val_result["symbol"] = sym
            val_result["train_sharpe"] = best_sharpe

            regime_results[regime].append(val_result)
            start += VAL_WINDOW

    # Aggregate best parameters per regime
    optimal_params = {}
    for regime, results in regime_results.items():
        if not results:
            optimal_params[regime] = {
                "holding_days": 2,
                "stop_loss": 0.10,
                "take_profit": 1.00,
                "note": "default (insufficient data)",
                "sample_size": 0,
            }
            continue

        # Weight by validation Sharpe
        df_results = pd.DataFrame(results)
        # Group by parameter combo and average
        grouped = df_results.groupby(["holding_days", "stop_loss", "take_profit"]).agg({
            "sharpe": "mean",
            "win_rate": "mean",
            "max_dd": "mean",
            "total_return": "mean",
            "num_trades": "sum",
        }).reset_index()

        if grouped.empty:
            optimal_params[regime] = {
                "holding_days": 2,
                "stop_loss": 0.10,
                "take_profit": 1.00,
                "note": "default (no valid combos)",
                "sample_size": 0,
            }
            continue

        # Select by best risk-adjusted: Sharpe * (1 - max_dd)
        grouped["score"] = grouped["sharpe"] * (1 - grouped["max_dd"])
        best = grouped.loc[grouped["score"].idxmax()]

        optimal_params[regime] = {
            "holding_days": int(best["holding_days"]),
            "stop_loss": round(float(best["stop_loss"]), 2),
            "take_profit": round(float(best["take_profit"]), 2),
            "avg_sharpe": round(float(best["sharpe"]), 4),
            "avg_win_rate": round(float(best["win_rate"]), 4),
            "avg_max_dd": round(float(best["max_dd"]), 4),
            "avg_total_return": round(float(best["total_return"]), 4),
            "total_trades": int(best["num_trades"]),
            "sample_size": len(results),
        }

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "train_window_days": TRAIN_WINDOW,
        "validation_window_days": VAL_WINDOW,
        "parameter_grid": {
            "holding_periods": HOLDING_PERIODS,
            "stop_losses": STOP_LOSSES,
            "take_profits": TAKE_PROFITS,
            "total_combinations": len(param_grid),
        },
        "symbols_tested": list(data.keys()),
        "optimal_parameters": optimal_params,
        "vectorbt_available": has_vbt,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Walk-forward optimization complete: {len(data)} symbols, {sum(len(v) for v in regime_results.values())} windows")

    return output


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = run_walk_forward_optimization()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
