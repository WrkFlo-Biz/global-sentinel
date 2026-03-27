#!/usr/bin/env python3
"""Global Sentinel P2-1 — Riskfolio-Lib Portfolio Optimization

Black-Litterman model with views from regime scoring + quantum signals.
Kelly Criterion position sizing. Outputs optimal allocation weights.

Runs daily at 8:00 AM ET via cron/scheduler.
Writes to data/quantum_feed/optimal_portfolio.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("global_sentinel.portfolio_optimizer")

REPO_ROOT = Path("/opt/global-sentinel")
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "optimal_portfolio.json"
REGIME_PATH = REPO_ROOT / "data" / "quantum_feed" / "hmm_regime.json"
QUANTUM_SIGNAL_PATH = REPO_ROOT / "data" / "quantum_feed" / "latest_signal.json"
WATCHLIST_PATH = REPO_ROOT / "config" / "assets_watchlist.yaml"


def _load_regime() -> Dict[str, Any]:
    """Load current regime state."""
    try:
        return json.loads(REGIME_PATH.read_text()) if REGIME_PATH.exists() else {}
    except Exception:
        return {}


def _load_quantum_signal() -> Dict[str, Any]:
    """Load latest quantum optimization signal."""
    try:
        return json.loads(QUANTUM_SIGNAL_PATH.read_text()) if QUANTUM_SIGNAL_PATH.exists() else {}
    except Exception:
        return {}


def _load_watchlist() -> List[str]:
    """Load watchlist symbols from config."""
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
        return symbols[:30] or ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLE", "XLF", "XLK"]
    except Exception:
        return ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLE", "XLF", "XLK"]


def _fetch_price_data(symbols: List[str], days: int = 252) -> Optional[pd.DataFrame]:
    """Fetch historical price data via yfinance."""
    try:
        import yfinance as yf
        data = yf.download(symbols, period=f"{days}d", progress=False, threads=True)
        if isinstance(data.columns, pd.MultiIndex):
            closes = data["Close"]
        else:
            closes = data[["Close"]]
            closes.columns = symbols
        closes = closes.dropna(axis=1, how="all").dropna()
        if closes.shape[1] < 2 or closes.shape[0] < 60:
            return None
        return closes
    except Exception as e:
        logger.error(f"Failed to fetch price data: {e}")
        return None


def _build_views_from_signals(
    symbols: List[str],
    regime: Dict[str, Any],
    quantum: Dict[str, Any],
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
    """Build Black-Litterman views from regime + quantum signals.

    Returns (P, Q, Omega) matrices or (None, None, None) if no views.
    P = pick matrix (k views x n assets)
    Q = expected returns vector (k x 1)
    Omega = uncertainty diagonal (k x k)
    """
    views = []
    n = len(symbols)
    sym_idx = {s: i for i, s in enumerate(symbols)}

    regime_mode = regime.get("regime", regime.get("mode", "NORMAL"))
    regime_p = float(regime.get("regime_p", regime.get("probability", 0.5)))

    # Regime-based views
    if regime_mode == "CRISIS" and regime_p > 0.6:
        # In crisis: expect defensive outperformance
        for sym in ["GLD", "TLT", "SHY", "VXX"]:
            if sym in sym_idx:
                p_row = np.zeros(n)
                p_row[sym_idx[sym]] = 1.0
                views.append((p_row, 0.05, 0.02))  # +5% expected, 2% uncertainty
        for sym in ["QQQ", "IWM", "XLF"]:
            if sym in sym_idx:
                p_row = np.zeros(n)
                p_row[sym_idx[sym]] = 1.0
                views.append((p_row, -0.03, 0.03))
    elif regime_mode == "ELEVATED" and regime_p > 0.5:
        for sym in ["GLD", "XLE"]:
            if sym in sym_idx:
                p_row = np.zeros(n)
                p_row[sym_idx[sym]] = 1.0
                views.append((p_row, 0.03, 0.025))
    else:
        # Normal: slight equity tilt
        for sym in ["SPY", "QQQ"]:
            if sym in sym_idx:
                p_row = np.zeros(n)
                p_row[sym_idx[sym]] = 1.0
                views.append((p_row, 0.02, 0.03))

    # Quantum signal views
    q_candidates = quantum.get("candidates", quantum.get("ranked_candidates", []))
    for cand in q_candidates[:5]:
        sym = cand.get("symbol", "")
        score = float(cand.get("quantum_score", cand.get("score", 0)))
        if sym in sym_idx and score > 0:
            p_row = np.zeros(n)
            p_row[sym_idx[sym]] = 1.0
            expected_ret = min(score * 0.01, 0.10)  # Cap at 10%
            views.append((p_row, expected_ret, 0.04))

    if not views:
        return None, None, None

    P = np.array([v[0] for v in views])
    Q = np.array([[v[1]] for v in views])
    Omega = np.diag([v[2] ** 2 for v in views])

    return P, Q, Omega


def _kelly_fraction(expected_return: float, variance: float, kelly_factor: float = 0.25) -> float:
    """Half-Kelly (or fractional Kelly) position sizing.

    kelly_factor=0.25 gives quarter-Kelly for conservative sizing.
    """
    if variance <= 0 or expected_return <= 0:
        return 0.0
    full_kelly = expected_return / variance
    return min(full_kelly * kelly_factor, 0.20)  # Cap at 20% per position


def optimize_portfolio(
    current_positions: Optional[Dict[str, float]] = None,
    candidates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run portfolio optimization and return optimal weights.

    Parameters
    ----------
    current_positions : dict mapping symbol -> current weight (0-1)
    candidates : list of candidate symbols to consider
    """
    try:
        import riskfolio as rp
    except ImportError:
        logger.error("riskfolio-lib not installed")
        return {"error": "riskfolio-lib not installed", "timestamp": datetime.now(timezone.utc).isoformat()}

    regime = _load_regime()
    quantum = _load_quantum_signal()
    watchlist = candidates or _load_watchlist()

    prices = _fetch_price_data(watchlist)
    if prices is None:
        return {"error": "insufficient price data", "timestamp": datetime.now(timezone.utc).isoformat()}

    symbols = list(prices.columns)
    returns = prices.pct_change().dropna()

    if returns.shape[0] < 60:
        return {"error": "insufficient return history", "timestamp": datetime.now(timezone.utc).isoformat()}

    # Build portfolio object
    port = rp.Portfolio(returns=returns)

    # Estimate expected returns and covariance
    port.assets_stats(method_mu="hist", method_cov="ledoit")

    # Black-Litterman views
    P, Q, Omega = _build_views_from_signals(symbols, regime, quantum)
    if P is not None:
        try:
            port.blacklitterman_stats(
                P=P, Q=Q, delta=2.5,
                rf=0.05,  # risk-free rate
                eq=True,
            )
            logger.info(f"Applied {P.shape[0]} Black-Litterman views")
        except Exception as e:
            logger.warning(f"Black-Litterman failed, using historical: {e}")

    # Select risk measure based on regime
    regime_mode = regime.get("regime", regime.get("mode", "NORMAL"))
    if regime_mode == "CRISIS":
        primary_rm, primary_model = "CVaR", "Classic"
    elif regime_mode == "ELEVATED":
        primary_rm, primary_model = "CDaR", "Classic"
    else:
        primary_rm, primary_model = "MV", "Classic"

    try:
        weights = port.optimization(
            model=primary_model,
            rm=primary_rm,
            obj="Sharpe",
            rf=0.05,
            hist=True,
        )
        if weights is not None and not weights.empty:
            w = weights.to_dict().get("weights", weights.iloc[:, 0].to_dict())
        else:
            w = {s: 1.0 / len(symbols) for s in symbols}
    except Exception as e:
        logger.warning(f"Optimization failed ({primary_rm}): {e}, using equal weight")
        w = {s: 1.0 / len(symbols) for s in symbols}

    # Apply Kelly Criterion sizing
    mu = returns.mean()
    var = returns.var()
    kelly_weights = {}
    for sym in symbols:
        opt_w = w.get(sym, 0.0)
        if opt_w > 0.01:
            kelly_f = _kelly_fraction(float(mu.get(sym, 0)), float(var.get(sym, 1)))
            kelly_weights[sym] = round(min(opt_w, kelly_f) if kelly_f > 0 else opt_w, 4)
        else:
            kelly_weights[sym] = 0.0

    # Normalize
    total = sum(kelly_weights.values())
    if total > 0:
        kelly_weights = {s: round(v / total, 4) for s, v in kelly_weights.items()}

    # Calculate portfolio metrics
    try:
        port_return = sum(float(mu.get(s, 0)) * kelly_weights.get(s, 0) for s in symbols) * 252
        port_vol = np.sqrt(
            sum(
                kelly_weights.get(si, 0) * kelly_weights.get(sj, 0) * float(returns[si].cov(returns[sj])) * 252
                for si in symbols for sj in symbols
            )
        )
        sharpe = (port_return - 0.05) / port_vol if port_vol > 0 else 0
    except Exception:
        port_return, port_vol, sharpe = 0, 0, 0

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "regime": regime_mode,
        "risk_measure": primary_rm,
        "model": primary_model,
        "num_assets": len([s for s, wt in kelly_weights.items() if wt > 0.001]),
        "optimal_weights": {s: wt for s, wt in sorted(kelly_weights.items(), key=lambda x: -x[1]) if wt > 0.001},
        "portfolio_metrics": {
            "expected_annual_return": round(port_return, 4),
            "annual_volatility": round(port_vol, 4),
            "sharpe_ratio": round(sharpe, 4),
        },
        "kelly_factor": 0.25,
        "black_litterman_views_applied": P is not None,
        "num_views": int(P.shape[0]) if P is not None else 0,
        "data_sources": {
            "regime": bool(regime),
            "quantum_signal": bool(quantum),
            "price_history_days": int(returns.shape[0]),
        },
    }

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    logger.info(f"Portfolio optimization complete: {output['num_assets']} assets, Sharpe={sharpe:.2f}")

    return output


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = optimize_portfolio()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
