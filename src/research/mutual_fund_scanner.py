#!/usr/bin/env python3
"""
Global Sentinel — Mutual Fund Scanner

Tracks top-performing mutual funds via yfinance, compares to SPY benchmark,
and computes alpha, beta, Sharpe ratio, and max drawdown for each.

Output: data/quantum_feed/mutual_fund_scores.json
Runs daily at 18:00 UTC (after market close)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.mutual_fund_scanner")

try:
    import yfinance as yf
except ImportError:
    yf = None

REPO_ROOT = Path(__file__).resolve().parents[2]
QUANTUM_FEED_DIR = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_FILE = QUANTUM_FEED_DIR / "mutual_fund_scores.json"

TRACKED_FUNDS = {
    "VFIAX": "Vanguard 500 Index Admiral",
    "FXAIX": "Fidelity 500 Index",
    "FCNTX": "Fidelity Contrafund",
    "VTSAX": "Vanguard Total Stock Market Admiral",
    "ARKK":  "ARK Innovation ETF",
    "SCHD":  "Schwab US Dividend Equity ETF",
}

BENCHMARK = "SPY"
RISK_FREE_RATE = 0.05  # annualised, ~current T-bill
LOOKBACK_PERIOD = "1y"
LOOKBACK_DAYS = 252


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct_change(series: pd.Series, days: int) -> Optional[float]:
    if len(series) < days + 1:
        return None
    curr = float(series.iloc[-1])
    prev = float(series.iloc[-(days + 1)])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 4)


def _max_drawdown(prices: pd.Series) -> float:
    """Compute max drawdown as a percentage (negative number)."""
    if len(prices) < 2:
        return 0.0
    cummax = prices.cummax()
    drawdowns = (prices - cummax) / cummax
    return round(float(drawdowns.min()) * 100, 2)


def _compute_metrics(fund_close: pd.Series, bench_close: pd.Series) -> Dict[str, Any]:
    """Compute alpha, beta, Sharpe, max drawdown for a fund vs benchmark."""
    # Align dates
    aligned = pd.DataFrame({"fund": fund_close, "bench": bench_close}).dropna()
    if len(aligned) < 30:
        return {"error": "insufficient data"}

    fund_ret = aligned["fund"].pct_change().dropna()
    bench_ret = aligned["bench"].pct_change().dropna()

    # Beta and Alpha (CAPM)
    cov_matrix = np.cov(fund_ret, bench_ret)
    beta = float(cov_matrix[0, 1] / cov_matrix[1, 1]) if cov_matrix[1, 1] != 0 else 0.0

    ann_fund_ret = float(fund_ret.mean()) * 252
    ann_bench_ret = float(bench_ret.mean()) * 252
    daily_rf = RISK_FREE_RATE / 252

    alpha = ann_fund_ret - (RISK_FREE_RATE + beta * (ann_bench_ret - RISK_FREE_RATE))

    # Sharpe ratio
    excess_ret = fund_ret - daily_rf
    sharpe = float(excess_ret.mean() / excess_ret.std() * np.sqrt(252)) if excess_ret.std() > 0 else 0.0

    # Max drawdown
    mdd = _max_drawdown(aligned["fund"])

    # Period returns
    ret_1m = _pct_change(aligned["fund"], 21)
    ret_3m = _pct_change(aligned["fund"], 63)
    ret_6m = _pct_change(aligned["fund"], 126)
    ret_1y = _pct_change(aligned["fund"], 252)

    bench_1m = _pct_change(aligned["bench"], 21)
    bench_3m = _pct_change(aligned["bench"], 63)

    return {
        "alpha": round(alpha * 100, 2),          # annualised alpha in bps*100 -> pct
        "beta": round(beta, 3),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": mdd,
        "return_1m_pct": ret_1m,
        "return_3m_pct": ret_3m,
        "return_6m_pct": ret_6m,
        "return_1y_pct": ret_1y,
        "benchmark_1m_pct": bench_1m,
        "benchmark_3m_pct": bench_3m,
        "outperforming_1m": (ret_1m > bench_1m) if ret_1m is not None and bench_1m is not None else None,
        "outperforming_3m": (ret_3m > bench_3m) if ret_3m is not None and bench_3m is not None else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_mutual_funds(repo_root: str = "/opt/global-sentinel") -> Dict[str, Any]:
    """Run the mutual fund scanner and write results."""
    if yf is None:
        return {"error": "yfinance not installed"}

    repo = Path(repo_root)
    output_path = repo / "data" / "quantum_feed" / "mutual_fund_scores.json"

    all_symbols = list(TRACKED_FUNDS.keys()) + [BENCHMARK]

    try:
        data = yf.download(all_symbols, period=LOOKBACK_PERIOD, interval="1d",
                           group_by="ticker", progress=False)
    except Exception as exc:
        logger.error("Failed to download mutual fund data: %s", exc)
        return {"error": str(exc)}

    # Extract benchmark
    try:
        bench_close = data[BENCHMARK]["Close"].dropna()
    except Exception:
        bench_close = pd.Series(dtype=float)

    funds: List[Dict[str, Any]] = []
    outperformers: List[str] = []

    for symbol, name in TRACKED_FUNDS.items():
        try:
            fund_close = data[symbol]["Close"].dropna()
        except Exception:
            funds.append({"symbol": symbol, "name": name, "error": "no data"})
            continue

        metrics = _compute_metrics(fund_close, bench_close)
        entry = {"symbol": symbol, "name": name, **metrics}

        if metrics.get("outperforming_1m") or metrics.get("outperforming_3m"):
            entry["regime_status"] = "outperforming"
            outperformers.append(symbol)
        elif metrics.get("alpha", 0) > 0 and metrics.get("sharpe_ratio", 0) > 0.5:
            entry["regime_status"] = "positive_alpha"
            outperformers.append(symbol)
        else:
            entry["regime_status"] = "underperforming"

        funds.append(entry)

    # Sort by Sharpe descending
    funds.sort(key=lambda x: x.get("sharpe_ratio", -999), reverse=True)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark": BENCHMARK,
        "lookback": LOOKBACK_PERIOD,
        "risk_free_rate": RISK_FREE_RATE,
        "funds": funds,
        "outperformers": outperformers,
        "summary": {
            "total_tracked": len(TRACKED_FUNDS),
            "outperforming_count": len(outperformers),
            "best_sharpe": funds[0]["symbol"] if funds and "sharpe_ratio" in funds[0] else None,
        },
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str))
        logger.info("Wrote mutual fund scores to %s", output_path)
    except Exception as exc:
        logger.error("Failed to write output: %s", exc)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = scan_mutual_funds()
    print(json.dumps(result, indent=2, default=str))
