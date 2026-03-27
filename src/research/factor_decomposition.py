#!/usr/bin/env python3
"""Fama-French 5-Factor Decomposition for Global Sentinel.

Decomposes paper account returns into Fama-French factor exposures:
  Mkt-RF (Market), SMB (Size), HML (Value), RMW (Profitability), CMA (Investment)

Downloads factor data from Ken French Data Library and regresses
portfolio returns against the 5 factors.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy import stats as sp_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

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

FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "factor_exposure.json"
CACHE_PATH = REPO_ROOT / "data" / "cache" / "ff5_factors.csv"
LOOKBACK_DAYS = 252  # 1 year of trading days


def _alpaca_headers(acct: dict) -> dict:
    return {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["secret_key"],
    }


def get_positions(acct: dict) -> List[Dict[str, Any]]:
    url = f"{acct['base_url']}/v2/positions"
    resp = requests.get(url, headers=_alpaca_headers(acct), timeout=15)
    resp.raise_for_status()
    return [
        {
            "symbol": p["symbol"],
            "qty": float(p["qty"]),
            "market_value": float(p["market_value"]),
            "current_price": float(p.get("current_price", 0)),
        }
        for p in resp.json()
    ]


def get_portfolio_history(acct: dict, period: str = "1A") -> pd.Series:
    """Get daily portfolio equity from Alpaca and compute returns."""
    url = f"{acct['base_url']}/v2/account/portfolio/history"
    params = {"period": period, "timeframe": "1D", "extended_hours": "false"}
    resp = requests.get(url, headers=_alpaca_headers(acct), params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    timestamps = data.get("timestamp", [])
    equity_vals = data.get("equity", [])
    if not timestamps or not equity_vals:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(timestamps, unit="s").normalize()
    equity = pd.Series(equity_vals, index=dates, name="equity", dtype=float)
    returns = equity.pct_change().dropna()
    returns.index = returns.index.strftime("%Y%m%d").astype(int)
    return returns


def download_ff5_factors() -> pd.DataFrame:
    """Download Fama-French 5 factors (daily) from Ken French Data Library."""
    # Try cache first (refresh weekly)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_PATH.stat().st_mtime)
        if cache_age < timedelta(days=7):
            logger.info("Using cached FF5 factor data")
            df = pd.read_csv(CACHE_PATH, index_col=0)
            return df

    logger.info("Downloading FF5 factor data from Ken French website...")
    try:
        resp = requests.get(FF5_URL, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to download FF5 data: {e}")
        if CACHE_PATH.exists():
            return pd.read_csv(CACHE_PATH, index_col=0)
        raise

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = [n for n in zf.namelist() if n.endswith(".CSV") or n.endswith(".csv")][0]
        raw = zf.read(csv_name).decode("utf-8")

    # Parse the CSV (skip header rows, find data start)
    lines = raw.strip().split("\n")
    data_start = None
    data_end = None
    for i, line in enumerate(lines):
        parts = line.strip().split(",")
        if len(parts) >= 6:
            try:
                int(parts[0].strip())
                if data_start is None:
                    data_start = i
                data_end = i
            except ValueError:
                if data_start is not None:
                    break

    if data_start is None:
        raise ValueError("Could not parse FF5 CSV")

    header_line = None
    for i in range(data_start - 1, -1, -1):
        if "Mkt-RF" in lines[i] or "Mkt" in lines[i]:
            header_line = i
            break

    if header_line is not None:
        csv_text = "\n".join([lines[header_line]] + lines[data_start:data_end + 1])
    else:
        csv_text = "\n".join(
            ["Date,Mkt-RF,SMB,HML,RMW,CMA,RF"]
            + lines[data_start:data_end + 1]
        )

    df = pd.read_csv(io.StringIO(csv_text))
    first_col = df.columns[0]
    df = df.rename(columns={first_col: "Date"})
    df["Date"] = df["Date"].astype(int)
    df = df.set_index("Date")

    # FF data is in percentages, convert to decimals
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0

    df.to_csv(CACHE_PATH)
    logger.info(f"FF5 data cached: {len(df)} days, columns={list(df.columns)}")
    return df


def compute_factor_exposures(
    portfolio_returns: pd.Series, ff_factors: pd.DataFrame
) -> Dict[str, Any]:
    """Run OLS regression of portfolio returns on FF5 factors."""
    factors = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]

    # Align dates
    common_idx = portfolio_returns.index.intersection(ff_factors.index)
    if len(common_idx) < 30:
        return {"error": f"Insufficient overlapping data: {len(common_idx)} days (need 30+)"}

    y = portfolio_returns.loc[common_idx].values
    rf = ff_factors.loc[common_idx, "RF"].values if "RF" in ff_factors.columns else np.zeros(len(common_idx))
    y_excess = y - rf  # excess returns

    X = ff_factors.loc[common_idx, factors].values
    # Add intercept (alpha)
    X_with_intercept = np.column_stack([np.ones(len(X)), X])

    # OLS regression
    try:
        betas, residuals, rank, sv = np.linalg.lstsq(X_with_intercept, y_excess, rcond=None)
    except Exception as e:
        return {"error": f"Regression failed: {e}"}

    alpha = betas[0]
    factor_betas = betas[1:]
    y_hat = X_with_intercept @ betas
    resid = y_excess - y_hat

    n = len(y_excess)
    k = len(factors) + 1  # factors + intercept
    dof = n - k

    # R-squared
    ss_res = np.sum(resid ** 2)
    ss_tot = np.sum((y_excess - np.mean(y_excess)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    adj_r_squared = 1 - (1 - r_squared) * (n - 1) / dof if dof > 0 else 0

    # Standard errors and t-stats
    mse = ss_res / dof if dof > 0 else 0
    try:
        var_betas = mse * np.linalg.inv(X_with_intercept.T @ X_with_intercept).diagonal()
        se_betas = np.sqrt(np.maximum(var_betas, 0))
    except Exception:
        se_betas = np.zeros(k)

    t_stats = betas / se_betas
    p_values = [2 * (1 - sp_stats.t.cdf(abs(t), dof)) for t in t_stats]

    # Annualized alpha
    alpha_annualized = alpha * 252

    # Factor contribution to return
    mean_factors = ff_factors.loc[common_idx, factors].mean().values
    factor_contributions = factor_betas * mean_factors * 252  # annualized

    result = {
        "num_observations": n,
        "r_squared": round(float(r_squared), 4),
        "adj_r_squared": round(float(adj_r_squared), 4),
        "alpha_daily": round(float(alpha), 6),
        "alpha_annualized": round(float(alpha_annualized) * 100, 2),  # in %
        "alpha_t_stat": round(float(t_stats[0]), 3),
        "alpha_p_value": round(float(p_values[0]), 4),
        "alpha_significant": bool(p_values[0] < 0.05),
        "factors": {},
    }

    for i, factor in enumerate(factors):
        is_sig = p_values[i + 1] < 0.05
        result["factors"][factor] = {
            "beta": round(float(factor_betas[i]), 4),
            "t_stat": round(float(t_stats[i + 1]), 3),
            "p_value": round(float(p_values[i + 1]), 4),
            "significant": bool(is_sig),
            "annualized_contribution_pct": round(float(factor_contributions[i]) * 100, 2),
        }

    # Interpretation
    interpretations = []
    if result["alpha_significant"]:
        direction = "positive" if alpha_annualized > 0 else "negative"
        interpretations.append(
            f"Statistically significant {direction} alpha of {result[alpha_annualized]:.2f}% annualized"
        )

    mkt_beta = result["factors"]["Mkt-RF"]["beta"]
    if mkt_beta > 1.1:
        interpretations.append(f"Aggressive market exposure (beta={mkt_beta:.2f})")
    elif mkt_beta < 0.9:
        interpretations.append(f"Defensive market exposure (beta={mkt_beta:.2f})")

    smb_beta = result["factors"]["SMB"]["beta"]
    if result["factors"]["SMB"]["significant"]:
        tilt = "small-cap" if smb_beta > 0 else "large-cap"
        interpretations.append(f"Significant {tilt} tilt (SMB beta={smb_beta:.2f})")

    hml_beta = result["factors"]["HML"]["beta"]
    if result["factors"]["HML"]["significant"]:
        tilt = "value" if hml_beta > 0 else "growth"
        interpretations.append(f"Significant {tilt} tilt (HML beta={hml_beta:.2f})")

    rmw_beta = result["factors"]["RMW"]["beta"]
    if result["factors"]["RMW"]["significant"]:
        tilt = "profitable" if rmw_beta > 0 else "unprofitable"
        interpretations.append(f"Tilted toward {tilt} firms (RMW beta={rmw_beta:.2f})")

    cma_beta = result["factors"]["CMA"]["beta"]
    if result["factors"]["CMA"]["significant"]:
        tilt = "conservative" if cma_beta > 0 else "aggressive"
        interpretations.append(f"Tilted toward {tilt} investing firms (CMA beta={cma_beta:.2f})")

    # Factor crowding warning
    sig_factors = [f for f in factors if result["factors"][f]["significant"] and f != "Mkt-RF"]
    if len(sig_factors) >= 3:
        interpretations.append(
            f"WARNING: Factor crowding detected — significant exposure to {len(sig_factors)} non-market factors"
        )

    result["interpretations"] = interpretations

    return result


def analyze_account(account_name: str, acct_config: dict, ff_factors: pd.DataFrame) -> Dict[str, Any]:
    """Run factor decomposition for one account."""
    logger.info(f"Factor decomposition for: {account_name}")

    try:
        portfolio_returns = get_portfolio_history(acct_config)
    except Exception as e:
        return {"account": account_name, "error": f"Failed to get portfolio history: {e}"}

    if portfolio_returns.empty or len(portfolio_returns) < 30:
        return {
            "account": account_name,
            "error": f"Insufficient portfolio history ({len(portfolio_returns)} days, need 30+)",
        }

    # Current positions for context
    try:
        positions = get_positions(acct_config)
    except Exception:
        positions = []

    exposure = compute_factor_exposures(portfolio_returns, ff_factors)

    return {
        "account": account_name,
        "analysis_window_days": len(portfolio_returns),
        "current_positions": len(positions),
        "top_holdings": sorted(positions, key=lambda p: abs(p["market_value"]), reverse=True)[:10],
        "factor_decomposition": exposure,
    }


def run() -> Dict[str, Any]:
    """Run factor decomposition for all accounts."""
    timestamp = datetime.now(timezone.utc).isoformat()

    # Download FF5 factors
    try:
        ff_factors = download_ff5_factors()
    except Exception as e:
        logger.error(f"Failed to load FF5 factors: {e}")
        return {"source": "factor_decomposition", "timestamp_utc": timestamp, "error": str(e)}

    output = {
        "source": "factor_decomposition",
        "timestamp_utc": timestamp,
        "methodology": "Fama-French 5-Factor Model (OLS regression)",
        "factors_explained": {
            "Mkt-RF": "Market risk premium (equity market excess return over risk-free rate)",
            "SMB": "Small Minus Big (small-cap vs large-cap premium)",
            "HML": "High Minus Low (value vs growth premium)",
            "RMW": "Robust Minus Weak (profitability premium)",
            "CMA": "Conservative Minus Aggressive (investment premium)",
        },
        "ff_data_range": f"{ff_factors.index.min()} to {ff_factors.index.max()}",
        "accounts": {},
    }

    for name, config in ACCOUNTS.items():
        if not config["api_key"]:
            continue
        try:
            output["accounts"][name] = analyze_account(name, config, ff_factors)
        except Exception as e:
            logger.error(f"Error analyzing {name}: {e}")
            output["accounts"][name] = {"account": name, "error": str(e)}

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"Factor decomposition written to {OUTPUT_PATH}")
    return output


if __name__ == "__main__":
    results = run()
    for acct_name, acct_data in results.get("accounts", {}).items():
        fd = acct_data.get("factor_decomposition", {})
        if "error" in fd:
            logger.info(f"  {acct_name}: {fd['error']}")
        elif "error" in acct_data:
            logger.info(f"  {acct_name}: {acct_data['error']}")
        else:
            logger.info(f"  {acct_name}: R²={fd.get('r_squared', 0):.3f}, Alpha={fd.get('alpha_annualized', 0):.2f}%")
            for interp in fd.get("interpretations", []):
                logger.info(f"    → {interp}")
    print(json.dumps(results, indent=2, default=str)[:500])
