#!/usr/bin/env python3
"""Karpathy-Style AutoResearch Strategy Optimizer.

Inspired by Andrej Karpathy's AutoResearch concept and Nunchi-trade's
implementation (7.6x Sharpe improvement over 103 iterations).

Self-improving loop: mutate one parameter -> backtest -> keep if better, revert otherwise.
Runs daily at 07:00 UTC (3 AM ET), after the auto-researcher at 06:00 UTC.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import random
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.auto_research_optimizer")

REPO_ROOT = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
DATA_DIR = REPO_ROOT / "data" / "quantum_feed"

PARAMS_PATH = DATA_DIR / "optimized_params.json"
WEIGHTS_PATH = DATA_DIR / "strategy_correlation_weights.json"
BACKUP_PARAMS_PATH = DATA_DIR / "optimized_params.backup.json"
BACKUP_WEIGHTS_PATH = DATA_DIR / "strategy_correlation_weights.backup.json"
EXPERIMENTS_LOG = DATA_DIR / "auto_research_experiments.jsonl"
SUMMARY_PATH = DATA_DIR / "auto_research_optimizer.json"

# Backtest symbols
BACKTEST_SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "AMD"]
LOOKBACK_DAYS = 90

# Default iterations per session
DEFAULT_ITERATIONS = 50

# Safety bounds
BOUNDS = {
    "stop_loss_pct": (1.0, 30.0),
    "take_profit_pct": (2.0, 200.0),
    "confidence_threshold": (0.1, 0.9),
    "position_size_pct": (1.0, 15.0),
    "kelly_multiplier": (0.05, 1.0),
    "regime_size_multiplier": (0.1, 2.0),
    "max_strategy_allocation": 0.50,
}

# Mutation ranges
MUTATION_RANGES = {
    "stop_loss_pct": (0.5, 2.0),
    "take_profit_pct": (1.0, 10.0),
    "confidence_threshold": (0.02, 0.10),
    "position_size_pct": (0.5, 2.0),
    "kelly_multiplier": (0.05, 0.15),
    "regime_size_multiplier": (0.05, 0.15),
}

# All strategies that can be in allocation weights
ALL_STRATEGIES = ["orb", "ict_smc", "momentum", "scalping", "overnight_gap", "ensemble_rl"]


def _load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON file, returning empty dict on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically save JSON (write to tmp, then rename)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.rename(path)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Market data fetching
# ---------------------------------------------------------------------------

def fetch_daily_bars(symbols: List[str], days: int = LOOKBACK_DAYS) -> Dict[str, pd.DataFrame]:
    """Fetch daily OHLCV bars using yfinance (same pattern as walk_forward_optimizer)."""
    result: Dict[str, pd.DataFrame] = {}
    try:
        import yfinance as yf
        data = yf.download(symbols, period=f"{days}d", progress=False, threads=True)
        if data.empty:
            logger.warning("yfinance returned empty data")
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
                    df = data[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df) >= 20:
                    result[sym] = df
            except Exception as exc:
                logger.warning("Failed to extract %s: %s", sym, exc)
    except ImportError:
        logger.error("yfinance not installed — cannot fetch market data")
    except Exception as exc:
        logger.error("Data fetch failed: %s", exc)
    return result


# ---------------------------------------------------------------------------
# Simple momentum backtest simulator
# ---------------------------------------------------------------------------

def compute_momentum_signal(prices: pd.DataFrame, lookback: int = 10) -> pd.Series:
    """Simple momentum: rate of change over lookback period, normalized."""
    close = prices["Close"]
    roc = close.pct_change(lookback)
    # Normalize to 0-1 range using rolling percentile
    rolling_min = roc.rolling(lookback * 2, min_periods=lookback).min()
    rolling_max = roc.rolling(lookback * 2, min_periods=lookback).max()
    span = rolling_max - rolling_min
    signal = (roc - rolling_min) / span.replace(0, np.nan)
    return signal.fillna(0.5)


def simulate_trades(
    prices: pd.DataFrame,
    params: Dict[str, Any],
    weights: Dict[str, Any],
) -> Dict[str, Any]:
    """Run simple momentum backtest with given parameters.

    For each day:
      - Compute momentum signal
      - If signal > confidence_threshold: enter long at close
      - Exit at stop_loss, take_profit, or end of next day
    Returns dict with sharpe, num_trades, max_drawdown, win_rate, profit_factor.
    """
    stop_loss = params.get("stop_loss_pct", 5.0) / 100.0
    take_profit = params.get("take_profit_pct", 50.0) / 100.0
    confidence = params.get("confidence_threshold", 0.4)
    position_pct = params.get("position_size_pct", 5.0) / 100.0

    # Regime multiplier from weights
    regime_mult = 1.0
    if weights and "regime_adjustments" in weights:
        regime_mult = weights["regime_adjustments"].get("regime_size_multiplier", 1.0)

    signal = compute_momentum_signal(prices)
    close = prices["Close"].values
    high = prices["High"].values
    low = prices["Low"].values

    trades: List[float] = []
    equity = 100000.0
    peak_equity = equity
    max_dd = 0.0

    i = 0
    while i < len(close) - 1:
        sig = signal.iloc[i]
        if sig > confidence:
            entry = close[i]
            size = equity * position_pct * regime_mult
            shares = size / entry if entry > 0 else 0
            if shares <= 0:
                i += 1
                continue

            # Check next day for stop/target
            j = i + 1
            exit_price = close[j]  # default: exit at next close

            # Intraday check using high/low
            day_high = high[j]
            day_low = low[j]

            stop_price = entry * (1.0 - stop_loss)
            target_price = entry * (1.0 + take_profit)

            if day_low <= stop_price:
                exit_price = stop_price
            elif day_high >= target_price:
                exit_price = target_price

            pnl = (exit_price - entry) * shares
            trades.append(pnl)
            equity += pnl
            peak_equity = max(peak_equity, equity)
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
            max_dd = max(max_dd, dd)

            i = j + 1  # skip past exit day
        else:
            i += 1

    if not trades:
        return {
            "sharpe": 0.0,
            "num_trades": 0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "score": 0.0,
        }

    trade_arr = np.array(trades)
    mean_ret = np.mean(trade_arr)
    std_ret = np.std(trade_arr) if len(trade_arr) > 1 else 1.0
    sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

    wins = trade_arr[trade_arr > 0]
    losses = trade_arr[trade_arr < 0]
    win_rate = len(wins) / len(trade_arr) if len(trade_arr) > 0 else 0.0
    gross_profit = np.sum(wins) if len(wins) > 0 else 0.0
    gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 1.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Score = Sharpe * sqrt(num_trades) — rewards performance AND activity
    n = len(trade_arr)
    score = sharpe * np.sqrt(n)

    return {
        "sharpe": round(float(sharpe), 4),
        "num_trades": n,
        "max_drawdown": round(float(max_dd), 4),
        "win_rate": round(float(win_rate), 4),
        "profit_factor": round(float(profit_factor), 4),
        "total_pnl": round(float(np.sum(trade_arr)), 2),
        "score": round(float(score), 4),
    }


def run_backtest(
    params: Dict[str, Any],
    weights: Dict[str, Any],
    market_data: Dict[str, pd.DataFrame],
) -> Dict[str, Any]:
    """Run backtest across all symbols and aggregate results."""
    all_scores = []
    all_trades = 0
    all_pnl = 0.0
    max_dd = 0.0

    for sym, df in market_data.items():
        result = simulate_trades(df, params, weights)
        if result["num_trades"] > 0:
            all_scores.append(result["score"])
            all_trades += result["num_trades"]
            all_pnl += result["total_pnl"]
            max_dd = max(max_dd, result["max_drawdown"])

    if not all_scores:
        return {"score": 0.0, "sharpe": 0.0, "num_trades": 0, "max_drawdown": 0.0, "total_pnl": 0.0}

    avg_score = float(np.mean(all_scores))
    avg_sharpe = avg_score / np.sqrt(max(all_trades, 1))

    return {
        "score": round(avg_score, 4),
        "sharpe": round(avg_sharpe, 4),
        "num_trades": all_trades,
        "max_drawdown": round(max_dd, 4),
        "total_pnl": round(all_pnl, 2),
    }


# ---------------------------------------------------------------------------
# Mutation engine
# ---------------------------------------------------------------------------

def mutate_params(
    params: Dict[str, Any],
    weights: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Randomly pick ONE parameter to mutate. Returns (new_params, new_weights, mutation_desc)."""
    params = copy.deepcopy(params)
    weights = copy.deepcopy(weights)

    mutation_types = [
        "stop_loss_pct",
        "take_profit_pct",
        "confidence_threshold",
        "position_size_pct",
        "kelly_multiplier",
        "regime_size_multiplier",
        "allocation_shift",
        "symbol_restriction",
    ]

    chosen = random.choice(mutation_types)

    if chosen in MUTATION_RANGES:
        lo, hi = MUTATION_RANGES[chosen]
        delta = random.uniform(lo, hi) * random.choice([-1, 1])

        if chosen in ("kelly_multiplier", "regime_size_multiplier"):
            # These live in weights under regime_adjustments
            ra = weights.get("regime_adjustments", {})
            old_val = ra.get(chosen, 0.5)
            new_val = _clamp(old_val + delta, *BOUNDS[chosen])
            ra[chosen] = round(new_val, 4)
            weights["regime_adjustments"] = ra
            desc = f"{chosen}: {old_val:.4f} -> {new_val:.4f} (delta={delta:+.4f})"
        else:
            old_val = params.get(chosen, 5.0)
            new_val = _clamp(old_val + delta, *BOUNDS[chosen])
            params[chosen] = round(new_val, 6)
            desc = f"{chosen}: {old_val:.4f} -> {new_val:.4f} (delta={delta:+.4f})"

    elif chosen == "allocation_shift":
        alloc = weights.get("allocation_weights", {})
        strategies = list(alloc.keys())
        if len(strategies) >= 2:
            src, dst = random.sample(strategies, 2)
            shift = 0.05  # 5% shift
            old_src = alloc[src]
            old_dst = alloc[dst]
            new_src = max(0.0, alloc[src] - shift)
            new_dst = min(BOUNDS["max_strategy_allocation"], alloc[dst] + shift)
            alloc[src] = round(new_src, 4)
            alloc[dst] = round(new_dst, 4)
            # Renormalize to sum to 1.0
            total = sum(alloc.values())
            if total > 0:
                for k in alloc:
                    alloc[k] = round(alloc[k] / total, 4)
            weights["allocation_weights"] = alloc
            desc = f"allocation_shift: {src} ({old_src:.2f}->{alloc[src]:.2f}), {dst} ({old_dst:.2f}->{alloc[dst]:.2f})"
        else:
            desc = "allocation_shift: skipped (< 2 strategies)"

    elif chosen == "symbol_restriction":
        restrictions = weights.get("symbol_restrictions", {})
        strategies_with_symbols = [s for s in restrictions if restrictions[s]]
        all_syms = BACKTEST_SYMBOLS
        if strategies_with_symbols:
            strat = random.choice(strategies_with_symbols)
            current = restrictions[strat]
            if random.random() < 0.5 and len(current) > 1:
                # Remove one
                removed = random.choice(current)
                current.remove(removed)
                desc = f"symbol_restriction: removed {removed} from {strat}"
            else:
                # Add one
                available = [s for s in all_syms if s not in current]
                if available:
                    added = random.choice(available)
                    current.append(added)
                    desc = f"symbol_restriction: added {added} to {strat}"
                else:
                    desc = f"symbol_restriction: {strat} already has all symbols"
            restrictions[strat] = current
            weights["symbol_restrictions"] = restrictions
        else:
            desc = "symbol_restriction: no strategies with symbols found"
    else:
        desc = f"unknown mutation type: {chosen}"

    return params, weights, desc


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    """Send result summary to Telegram research topic."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier
        notifier = TelegramTopicNotifier(topic="research")
        result = notifier.send_message(message)
        if result.ok:
            logger.info("Telegram sent to research topic")
        else:
            logger.warning("Telegram send failed: %s", result.reason)
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)


# ---------------------------------------------------------------------------
# Main optimization loop
# ---------------------------------------------------------------------------

def run_optimization(iterations: int = DEFAULT_ITERATIONS) -> Dict[str, Any]:
    """Execute the self-improving optimization loop."""
    logger.info("=== AutoResearch Optimizer starting (%d iterations) ===", iterations)

    # Load current params
    params = _load_json(PARAMS_PATH)
    weights = _load_json(WEIGHTS_PATH)

    if not params:
        logger.error("No optimized_params.json found at %s — cannot proceed", PARAMS_PATH)
        return {"error": "missing_params"}

    if not weights:
        logger.warning("No strategy_correlation_weights.json — using empty weights")
        weights = {"allocation_weights": {}, "regime_adjustments": {"regime_size_multiplier": 1.0}}

    # Backup originals
    _save_json(BACKUP_PARAMS_PATH, params)
    if weights:
        _save_json(BACKUP_WEIGHTS_PATH, weights)
    logger.info("Backed up original params to %s", BACKUP_PARAMS_PATH)

    before_params = copy.deepcopy(params)
    before_weights = copy.deepcopy(weights)

    # Fetch market data once
    logger.info("Fetching %d days of daily bars for %s", LOOKBACK_DAYS, BACKTEST_SYMBOLS)
    market_data = fetch_daily_bars(BACKTEST_SYMBOLS, LOOKBACK_DAYS)
    if not market_data:
        msg = "No market data fetched — aborting optimization"
        logger.error(msg)
        return {"error": msg}
    logger.info("Loaded data for %d symbols: %s", len(market_data), list(market_data.keys()))

    # Baseline score
    baseline = run_backtest(params, weights, market_data)
    best_score = baseline["score"]
    best_sharpe = baseline["sharpe"]
    logger.info("Baseline score=%.4f sharpe=%.4f trades=%d", best_score, best_sharpe, baseline["num_trades"])

    # Iteration tracking
    improvements = 0
    kept_mutations: List[str] = []
    score_history: List[float] = [best_score]
    start_time = time.time()

    for i in range(1, iterations + 1):
        # Mutate
        new_params, new_weights, mutation_desc = mutate_params(params, weights)
        logger.info("Iter %d/%d: %s", i, iterations, mutation_desc)

        # Backtest mutated params
        result = run_backtest(new_params, new_weights, market_data)
        new_score = result["score"]

        # Commit/Revert gate: must improve by at least 1% of absolute value
        # For negative scores, new_score must be closer to zero (less negative)
        improvement_threshold = abs(best_score) * 0.01 if best_score != 0 else 0.01
        improved = new_score > best_score + improvement_threshold

        log_entry = {
            "iteration": i,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mutation": mutation_desc,
            "old_score": round(best_score, 4),
            "new_score": round(new_score, 4),
            "kept": improved,
            "backtest": result,
        }

        # Append to experiments log
        with open(EXPERIMENTS_LOG, "a") as f:
            f.write(json.dumps(log_entry, default=str) + "\n")

        if improved:
            improvements += 1
            best_score = new_score
            best_sharpe = result["sharpe"]
            params = new_params
            weights = new_weights
            kept_mutations.append(mutation_desc)
            logger.info("  KEPT: score %.4f -> %.4f (+%.1f%%)", log_entry["old_score"], new_score,
                        (new_score - log_entry["old_score"]) / max(abs(log_entry["old_score"]), 0.0001) * 100)
        else:
            logger.info("  REVERTED: score %.4f vs best %.4f", new_score, best_score)

        score_history.append(best_score)

    elapsed = time.time() - start_time

    # Save improved params
    params["best_sharpe"] = best_sharpe
    params["timestamp"] = datetime.now(timezone.utc).isoformat()
    params["n_trials"] = params.get("n_trials", 0) + iterations
    _save_json(PARAMS_PATH, params)

    weights["updated"] = datetime.now(timezone.utc).isoformat()
    _save_json(WEIGHTS_PATH, weights)

    # Save summary
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iterations_run": iterations,
        "improvements_found": improvements,
        "best_score": round(best_score, 4),
        "best_sharpe": round(best_sharpe, 4),
        "baseline_score": round(baseline["score"], 4),
        "baseline_sharpe": round(baseline["sharpe"], 4),
        "elapsed_seconds": round(elapsed, 1),
        "parameter_changes": kept_mutations,
        "before_params": {
            "stop_loss_pct": before_params.get("stop_loss_pct"),
            "take_profit_pct": before_params.get("take_profit_pct"),
            "confidence_threshold": before_params.get("confidence_threshold"),
            "position_size_pct": before_params.get("position_size_pct"),
        },
        "after_params": {
            "stop_loss_pct": params.get("stop_loss_pct"),
            "take_profit_pct": params.get("take_profit_pct"),
            "confidence_threshold": params.get("confidence_threshold"),
            "position_size_pct": params.get("position_size_pct"),
        },
        "before_allocations": before_weights.get("allocation_weights", {}),
        "after_allocations": weights.get("allocation_weights", {}),
        "score_history": [round(s, 4) for s in score_history],
    }
    _save_json(SUMMARY_PATH, summary)
    logger.info("Summary saved to %s", SUMMARY_PATH)

    # Telegram notification
    old_sharpe_str = f"{baseline['sharpe']:.4f}"
    new_sharpe_str = f"{best_sharpe:.4f}"
    changes_str = "\n".join(f"  - {m}" for m in kept_mutations) if kept_mutations else "  (none)"
    tg_msg = (
        f"AUTO-RESEARCH OPTIMIZER\n"
        f"{improvements} improvements in {iterations} iterations ({elapsed:.0f}s)\n"
        f"Sharpe: {old_sharpe_str} -> {new_sharpe_str}\n"
        f"Score: {baseline['score']:.4f} -> {best_score:.4f}\n"
        f"Changes:\n{changes_str}"
    )
    send_telegram(tg_msg)

    logger.info("=== AutoResearch Optimizer complete: %d improvements, sharpe %.4f -> %.4f ===",
                improvements, baseline["sharpe"], best_sharpe)

    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    iterations = DEFAULT_ITERATIONS
    if len(sys.argv) > 1:
        try:
            iterations = int(sys.argv[1])
        except ValueError:
            pass

    result = run_optimization(iterations)

    if "error" in result:
        logger.error("Optimization failed: %s", result["error"])
        sys.exit(1)

    # Print summary to stdout
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
