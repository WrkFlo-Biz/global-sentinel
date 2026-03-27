#!/usr/bin/env python3
"""
Global Sentinel — Implied Volatility Surface Constructor

Uses py_vollib for Black-Scholes implied vol calculation on Alpaca
options chain data for SPY, QQQ, TSLA, NVDA.

Constructs IV surface (strike x expiry), detects vol skew anomalies
(unusually cheap/expensive strikes relative to ATM).

Output: data/quantum_feed/iv_surface.json
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("global_sentinel.iv_surface")

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "iv_surface.json"

TICKERS = ["SPY", "QQQ", "TSLA", "NVDA"]
RISK_FREE_RATE = 0.043  # ~4.3% (update periodically)


# ---------------------------------------------------------------------------
# Alpaca options chain fetcher
# ---------------------------------------------------------------------------

def _get_alpaca_client():
    """Initialize Alpaca trading client."""
    try:
        from alpaca.trading.client import TradingClient
        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        return TradingClient(api_key, api_secret, paper=True)
    except Exception as exc:
        logger.warning("Alpaca client init failed: %s", exc)
        return None


def _get_options_data_client():
    """Initialize Alpaca options data client."""
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        return OptionHistoricalDataClient(api_key, api_secret)
    except Exception as exc:
        logger.warning("Alpaca options data client init failed: %s", exc)
        return None


def _fetch_option_chain(ticker: str) -> List[Dict[str, Any]]:
    """Fetch options chain for a ticker via Alpaca API."""
    try:
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import AssetStatus
        client = _get_alpaca_client()
        if not client:
            return []

        now = datetime.now(timezone.utc)
        # Get contracts expiring in next 60 days
        req = GetOptionContractsRequest(
            underlying_symbols=[ticker],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=now.strftime("%Y-%m-%d"),
            expiration_date_lte=(now + timedelta(days=60)).strftime("%Y-%m-%d"),
            limit=500,
        )
        contracts = client.get_option_contracts(req)
        if not contracts or not contracts.option_contracts:
            return []

        chain = []
        for c in contracts.option_contracts:
            chain.append({
                "symbol": c.symbol,
                "strike": float(c.strike_price),
                "expiry": str(c.expiration_date),
                "type": str(c.type).lower(),  # call or put
                "status": str(c.status),
            })
        return chain
    except Exception as exc:
        logger.warning("Option chain fetch failed for %s: %s", ticker, exc)
        return []


def _fetch_option_quotes(symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """Fetch latest option quotes."""
    quotes = {}
    try:
        data_client = _get_options_data_client()
        if not data_client:
            return quotes
        from alpaca.data.requests import OptionLatestQuoteRequest
        req = OptionLatestQuoteRequest(symbol_or_symbols=symbols[:100])
        raw_quotes = data_client.get_option_latest_quote(req)
        for sym, q in raw_quotes.items():
            mid = (float(q.bid_price) + float(q.ask_price)) / 2 if q.bid_price and q.ask_price else None
            quotes[sym] = {
                "bid": float(q.bid_price) if q.bid_price else 0,
                "ask": float(q.ask_price) if q.ask_price else 0,
                "mid": mid,
            }
    except Exception as exc:
        logger.warning("Option quotes fetch failed: %s", exc)
    return quotes


def _fetch_spot_price(ticker: str) -> Optional[float]:
    """Get current spot price."""
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        client = StockHistoricalDataClient(api_key, api_secret)
        req = StockLatestTradeRequest(symbol_or_symbols=[ticker])
        trades = client.get_stock_latest_trade(req)
        if ticker in trades:
            return float(trades[ticker].price)
    except Exception as exc:
        logger.warning("Spot price fetch failed for %s: %s", ticker, exc)
    return None


# ---------------------------------------------------------------------------
# Implied vol computation
# ---------------------------------------------------------------------------

def _compute_iv(option_price: float, spot: float, strike: float,
                t_years: float, option_type: str, r: float = RISK_FREE_RATE) -> Optional[float]:
    """Compute Black-Scholes implied volatility using py_vollib."""
    if option_price <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return None

    try:
        from py_vollib.black_scholes.implied_volatility import implied_volatility
        flag = "c" if option_type == "call" else "p"
        iv = implied_volatility(option_price, spot, strike, t_years, r, flag)
        if 0.01 < iv < 5.0:  # sanity check
            return round(float(iv), 4)
    except Exception:
        pass
    return None


def _time_to_expiry(expiry_str: str) -> float:
    """Years to expiry from date string."""
    try:
        exp = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (exp - now).total_seconds() / 86400
        return max(days / 365.25, 0.001)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Surface construction & anomaly detection
# ---------------------------------------------------------------------------

def build_iv_surface(ticker: str) -> Dict[str, Any]:
    """Build IV surface for a single ticker."""
    logger.info("Building IV surface for %s", ticker)

    spot = _fetch_spot_price(ticker)
    if not spot:
        return {"ticker": ticker, "error": "no_spot_price"}

    chain = _fetch_option_chain(ticker)
    if not chain:
        return {"ticker": ticker, "error": "no_option_chain", "spot": spot}

    # Get quotes for chain
    symbols = [c["symbol"] for c in chain]
    quotes = _fetch_option_quotes(symbols)

    surface_points = []
    for contract in chain:
        sym = contract["symbol"]
        q = quotes.get(sym)
        if not q or not q.get("mid") or q["mid"] <= 0:
            continue

        t_years = _time_to_expiry(contract["expiry"])
        if t_years <= 0.001:
            continue

        iv = _compute_iv(q["mid"], spot, contract["strike"], t_years, contract["type"])
        if iv is None:
            continue

        moneyness = contract["strike"] / spot
        surface_points.append({
            "symbol": sym,
            "strike": contract["strike"],
            "expiry": contract["expiry"],
            "type": contract["type"],
            "mid_price": round(q["mid"], 2),
            "iv": iv,
            "moneyness": round(moneyness, 4),
            "t_years": round(t_years, 4),
        })

    if not surface_points:
        return {"ticker": ticker, "spot": spot, "error": "no_iv_computed", "chain_size": len(chain)}

    # Detect skew anomalies
    anomalies = _detect_skew_anomalies(surface_points, spot)

    # Organize by expiry
    by_expiry = {}
    for pt in surface_points:
        exp = pt["expiry"]
        if exp not in by_expiry:
            by_expiry[exp] = []
        by_expiry[exp].append(pt)

    return {
        "ticker": ticker,
        "spot": spot,
        "surface_points": len(surface_points),
        "expiries": sorted(by_expiry.keys()),
        "surface": by_expiry,
        "anomalies": anomalies,
    }


def _detect_skew_anomalies(points: List[Dict[str, Any]], spot: float) -> List[Dict[str, Any]]:
    """Detect strikes with unusually high or low IV relative to ATM."""
    anomalies = []

    # Group by expiry and type
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for pt in points:
        key = f"{pt['expiry']}_{pt['type']}"
        if key not in groups:
            groups[key] = []
        groups[key].append(pt)

    for key, pts in groups.items():
        if len(pts) < 5:
            continue

        ivs = np.array([p["iv"] for p in pts])
        moneyness = np.array([p["moneyness"] for p in pts])

        # Find ATM IV (moneyness closest to 1.0)
        atm_idx = np.argmin(np.abs(moneyness - 1.0))
        atm_iv = ivs[atm_idx]

        if atm_iv <= 0:
            continue

        # Anomaly: IV deviating > 2 std from mean for that expiry
        mean_iv = np.mean(ivs)
        std_iv = np.std(ivs)
        if std_iv <= 0:
            continue

        for i, pt in enumerate(pts):
            z_score = (pt["iv"] - mean_iv) / std_iv
            iv_ratio = pt["iv"] / atm_iv

            if abs(z_score) > 2.0:
                anomalies.append({
                    "symbol": pt["symbol"],
                    "strike": pt["strike"],
                    "expiry": pt["expiry"],
                    "type": pt["type"],
                    "iv": pt["iv"],
                    "atm_iv": round(atm_iv, 4),
                    "iv_ratio_to_atm": round(iv_ratio, 4),
                    "z_score": round(z_score, 4),
                    "direction": "expensive" if z_score > 0 else "cheap",
                })

    return anomalies


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> Dict[str, Any]:
    """Build IV surfaces for all tracked tickers."""
    logger.info("Starting IV surface construction")

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_free_rate": RISK_FREE_RATE,
        "tickers": {},
        "all_anomalies": [],
    }

    for ticker in TICKERS:
        surface = build_iv_surface(ticker)
        results["tickers"][ticker] = surface
        if "anomalies" in surface:
            for a in surface["anomalies"]:
                a["underlying"] = ticker
            results["all_anomalies"].extend(surface.get("anomalies", []))

    results["total_anomalies"] = len(results["all_anomalies"])

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    logger.info("IV surface saved to %s (%d anomalies)", OUTPUT_PATH, results["total_anomalies"])

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    result = run()
    print(json.dumps({"tickers": list(result["tickers"].keys()), "total_anomalies": result["total_anomalies"]}, indent=2))
