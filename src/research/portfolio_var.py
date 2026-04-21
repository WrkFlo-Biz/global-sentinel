#!/usr/bin/env python3
"""Portfolio Value at Risk (VaR) Calculator for Global Sentinel.

Computes daily VaR for both paper trading accounts using:
- Parametric VaR (95% and 99% confidence)
- Historical VaR (rolling 252-day window)
- Monte Carlo VaR (10,000 simulations)
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
import requests
import yfinance as yf
from scipy import stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

# Account configurations
ACCOUNTS = {
    "daytrade": {
        "api_key": os.getenv("ALPACA_API_KEY_DAYTRADE", ""),
        "secret_key": os.getenv("ALPACA_SECRET_KEY_DAYTRADE", ""),
        "base_url": os.getenv("ALPACA_BASE_URL_DAYTRADE", "https://paper-api.alpaca.markets"),
    },
    "medlong": {
        "api_key": os.getenv("ALPACA_API_KEY_MEDLONG", ""),
        "secret_key": os.getenv("ALPACA_SECRET_KEY_MEDLONG", ""),
        "base_url": os.getenv("ALPACA_BASE_URL_MEDLONG", "https://paper-api.alpaca.markets"),
    },
}

MC_SIMULATIONS = 10_000
ROLLING_WINDOW = 252
CONFIDENCE_LEVELS = [0.95, 0.99]
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "portfolio_var.json"


def _alpaca_headers(acct: dict) -> dict:
    return {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["secret_key"],
    }


def get_account_info(acct: dict) -> Dict[str, Any]:
    """Get account equity and buying power."""
    url = f"{acct['base_url']}/v2/account"
    resp = requests.get(url, headers=_alpaca_headers(acct), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {
        "equity": float(data.get("equity", 0)),
        "buying_power": float(data.get("buying_power", 0)),
        "portfolio_value": float(data.get("portfolio_value", 0)),
    }


def get_positions(acct: dict) -> List[Dict[str, Any]]:
    """Get current positions from Alpaca."""
    url = f"{acct['base_url']}/v2/positions"
    resp = requests.get(url, headers=_alpaca_headers(acct), timeout=15)
    resp.raise_for_status()
    positions = []
    for p in resp.json():
        positions.append({
            "symbol": p["symbol"],
            "qty": float(p["qty"]),
            "market_value": float(p["market_value"]),
            "cost_basis": float(p.get("cost_basis", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "current_price": float(p.get("current_price", 0)),
            "side": p.get("side", "long"),
        })
    return positions


def fetch_historical_returns(symbols: List[str], window: int = ROLLING_WINDOW) -> pd.DataFrame:
    """Download daily returns for a list of symbols using yfinance."""
    end = datetime.now()
    start = end - timedelta(days=int(window * 1.5))  # extra buffer for non-trading days
    if not symbols:
        return pd.DataFrame()
    try:
        data = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            prices = data["Close"]
        else:
            prices = data[["Close"]].rename(columns={"Close": symbols[0]}) if len(symbols) == 1 else data["Close"]
    except Exception as e:
        logger.error(f"yfinance download failed: {e}")
        return pd.DataFrame()

    returns = prices.pct_change().dropna()
    # Trim to rolling window
    if len(returns) > window:
        returns = returns.iloc[-window:]
    return returns


def compute_parametric_var(
    weights: np.ndarray,
    returns: pd.DataFrame,
    portfolio_value: float,
    confidence: float = 0.95,
) -> Dict[str, float]:
    """Parametric (variance-covariance) VaR assuming normal distribution."""
    cov_matrix = returns.cov().values * 252  # annualized
    daily_cov = cov_matrix / 252
    port_variance = weights @ daily_cov @ weights
    port_std = np.sqrt(port_variance)
    z_score = stats.norm.ppf(confidence)
    var_pct = z_score * port_std
    var_dollar = var_pct * portfolio_value
    return {"var_pct": round(float(var_pct) * 100, 4), "var_dollar": round(float(var_dollar), 2)}


def compute_historical_var(
    weights: np.ndarray,
    returns: pd.DataFrame,
    portfolio_value: float,
    confidence: float = 0.95,
) -> Dict[str, float]:
    """Historical simulation VaR using actual past returns."""
    port_returns = (returns.values @ weights)
    cutoff = np.percentile(port_returns, (1 - confidence) * 100)
    var_pct = abs(cutoff)
    var_dollar = var_pct * portfolio_value
    return {"var_pct": round(float(var_pct) * 100, 4), "var_dollar": round(float(var_dollar), 2)}


def compute_monte_carlo_var(
    weights: np.ndarray,
    returns: pd.DataFrame,
    portfolio_value: float,
    confidence: float = 0.95,
    n_simulations: int = MC_SIMULATIONS,
) -> Dict[str, float]:
    """Monte Carlo VaR using correlated random simulations."""
    mean_returns = returns.mean().values
    cov_matrix = returns.cov().values
    # Cholesky decomposition for correlated random draws
    try:
        L = np.linalg.cholesky(cov_matrix)
    except np.linalg.LinAlgError:
        # If not positive definite, add small diagonal
        cov_matrix += np.eye(len(cov_matrix)) * 1e-8
        L = np.linalg.cholesky(cov_matrix)

    np.random.seed(42)
    z = np.random.standard_normal((n_simulations, len(weights)))
    correlated_returns = z @ L.T + mean_returns
    port_returns = correlated_returns @ weights
    cutoff = np.percentile(port_returns, (1 - confidence) * 100)
    var_pct = abs(cutoff)
    var_dollar = var_pct * portfolio_value
    return {"var_pct": round(float(var_pct) * 100, 4), "var_dollar": round(float(var_dollar), 2)}


def compute_component_var(
    weights: np.ndarray,
    returns: pd.DataFrame,
    portfolio_value: float,
    symbols: List[str],
    confidence: float = 0.99,
) -> List[Dict[str, Any]]:
    """Component VaR: marginal contribution of each position to total VaR."""
    cov_matrix = returns.cov().values
    port_variance = weights @ cov_matrix @ weights
    port_std = np.sqrt(port_variance)
    z_score = stats.norm.ppf(confidence)

    marginal_var = (cov_matrix @ weights) / port_std
    component_var = weights * marginal_var * z_score

    results = []
    for i, sym in enumerate(symbols):
        results.append({
            "symbol": sym,
            "weight_pct": round(float(weights[i]) * 100, 2),
            "component_var_pct": round(float(component_var[i]) * 100, 4),
            "component_var_dollar": round(float(component_var[i]) * portfolio_value, 2),
            "pct_of_total_var": round(float(component_var[i] / (z_score * port_std)) * 100, 2) if port_std > 0 else 0,
        })
    return results


def analyze_account(account_name: str, acct_config: dict) -> Dict[str, Any]:
    """Run full VaR analysis for a single account."""
    logger.info(f"Analyzing account: {account_name}")

    try:
        acct_info = get_account_info(acct_config)
    except Exception as e:
        logger.error(f"Failed to get account info for {account_name}: {e}")
        return {"account": account_name, "error": str(e)}

    portfolio_value = acct_info["equity"]
    if portfolio_value <= 0:
        return {"account": account_name, "equity": 0, "positions": 0, "message": "No equity"}

    try:
        positions = get_positions(acct_config)
    except Exception as e:
        logger.error(f"Failed to get positions for {account_name}: {e}")
        return {"account": account_name, "error": str(e)}

    if not positions:
        return {
            "account": account_name,
            "equity": portfolio_value,
            "positions": 0,
            "message": "No open positions — VaR is zero",
            "var_95_parametric": {"var_pct": 0, "var_dollar": 0},
            "var_99_parametric": {"var_pct": 0, "var_dollar": 0},
        }

    symbols = [p["symbol"] for p in positions]
    market_values = np.array([p["market_value"] for p in positions])
    total_mv = np.sum(np.abs(market_values))
    if total_mv == 0:
        weights = np.ones(len(symbols)) / len(symbols)
    else:
        weights = market_values / total_mv

    # Fetch historical returns
    returns = fetch_historical_returns(symbols)
    if returns.empty or len(returns) < 20:
        return {
            "account": account_name,
            "equity": portfolio_value,
            "positions": len(positions),
            "error": "Insufficient historical data for VaR calculation",
        }

    # Align columns with symbols (handle missing)
    available = [s for s in symbols if s in returns.columns]
    if len(available) < len(symbols):
        missing = set(symbols) - set(available)
        logger.warning(f"Missing historical data for: {missing}")

    if not available:
        return {"account": account_name, "error": "No historical data available for any position"}

    # Recompute weights for available symbols only
    avail_idx = [symbols.index(s) for s in available]
    avail_weights = market_values[avail_idx]
    total_avail = np.sum(np.abs(avail_weights))
    if total_avail > 0:
        avail_weights = avail_weights / total_avail
    else:
        avail_weights = np.ones(len(available)) / len(available)

    returns = returns[available]

    # Fill any remaining NaN with 0
    returns = returns.fillna(0)

    result = {
        "account": account_name,
        "equity": round(portfolio_value, 2),
        "num_positions": len(positions),
        "analyzed_positions": len(available),
        "position_details": positions,
        "correlation_matrix": {
            s1: {s2: round(v, 4) for s2, v in zip(available, row)}
            for s1, row in zip(available, returns.corr().values)
        },
    }

    # Parametric VaR
    for cl in CONFIDENCE_LEVELS:
        pct_label = int(cl * 100)
        result[f"parametric_var_{pct_label}"] = compute_parametric_var(avail_weights, returns, portfolio_value, cl)

    # Historical VaR
    for cl in CONFIDENCE_LEVELS:
        pct_label = int(cl * 100)
        result[f"historical_var_{pct_label}"] = compute_historical_var(avail_weights, returns, portfolio_value, cl)

    # Monte Carlo VaR
    for cl in CONFIDENCE_LEVELS:
        pct_label = int(cl * 100)
        result[f"monte_carlo_var_{pct_label}"] = compute_monte_carlo_var(avail_weights, returns, portfolio_value, cl)

    # Component VaR (99%)
    result["component_var_99"] = compute_component_var(avail_weights, returns, portfolio_value, available, 0.99)

    return result


def run() -> Dict[str, Any]:
    """Run VaR analysis for all accounts."""
    timestamp = datetime.now(timezone.utc).isoformat()
    output = {
        "source": "portfolio_var",
        "timestamp_utc": timestamp,
        "accounts": {},
    }

    for name, config in ACCOUNTS.items():
        if not config["api_key"]:
            logger.warning(f"Skipping account {name}: no API key configured")
            continue
        try:
            output["accounts"][name] = analyze_account(name, config)
        except Exception as e:
            logger.error(f"Error analyzing {name}: {e}")
            output["accounts"][name] = {"account": name, "error": str(e)}

    # Summary across accounts
    total_equity = sum(
        a.get("equity", 0) for a in output["accounts"].values() if isinstance(a, dict)
    )
    worst_var_99_dollar = max(
        (a.get("parametric_var_99", {}).get("var_dollar", 0) for a in output["accounts"].values() if isinstance(a, dict)),
        default=0,
    )
    output["summary"] = {
        "total_equity": round(total_equity, 2),
        "worst_case_var_99_dollar": round(worst_var_99_dollar, 2),
        "worst_case_var_99_pct": round(worst_var_99_dollar / total_equity * 100, 4) if total_equity > 0 else 0,
    }

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"VaR results written to {OUTPUT_PATH}")

    return output


if __name__ == "__main__":
    results = run()
    for acct_name, acct_data in results.get("accounts", {}).items():
        if "error" in acct_data:
            logger.info(f"  {acct_name}: ERROR — {acct_data['error']}")
        else:
            p99 = acct_data.get("parametric_var_99", {})
            logger.info(
                f"  {acct_name}: Equity=${acct_data.get('equity', 0):,.0f} | "
                f"Parametric VaR(99%)=${p99.get('var_dollar', 0):,.0f} ({p99.get('var_pct', 0):.2f}%)"
            )
    print(json.dumps(results.get("summary", {}), indent=2))
