#!/usr/bin/env python3
"""Hidden Markov Model (HMM) Regime Detector.

Fits a 3-state Gaussian HMM on daily VIX, SPY returns, oil returns,
and yield curve slope to detect market regimes (calm, transition, crisis).

P1-4 enhancement — deployed 2026-03-25.
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# Regime labels
REGIME_LABELS = {0: "calm", 1: "transition", 2: "crisis"}
OUTPUT_PATH = PROJECT_ROOT / "data" / "quantum_feed" / "hmm_regime.json"


def _fetch_daily_data(symbol: str, days: int = 120) -> Optional[List[float]]:
    """Fetch daily closing prices from Yahoo Finance or Alpaca."""
    try:
        import requests
        # Try Alpaca first (we have credentials)
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if api_key:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days + 10)
            resp = requests.get(
                "https://data.alpaca.markets/v2/stocks/bars",
                params={
                    "symbols": symbol,
                    "timeframe": "1Day",
                    "start": start.strftime("%Y-%m-%dT00:00:00Z"),
                    "end": end.strftime("%Y-%m-%dT00:00:00Z"),
                    "limit": str(days + 10),
                },
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                bars = data.get("bars", {}).get(symbol, [])
                if bars:
                    return [float(b["c"]) for b in bars]
    except Exception as exc:
        logger.debug("Alpaca fetch for %s failed: %s", symbol, exc)

    # Fallback: try Yahoo Finance
    try:
        import requests
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": "1d", "range": f"{days}d"}
        resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code == 200:
            result = resp.json().get("chart", {}).get("result", [])
            if result:
                closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                return [float(c) for c in closes if c is not None]
    except Exception as exc:
        logger.debug("Yahoo fetch for %s failed: %s", symbol, exc)

    return None


def _compute_returns(prices: List[float]) -> np.ndarray:
    """Compute daily log returns from price series."""
    arr = np.array(prices)
    returns = np.diff(np.log(arr))
    return returns


def _get_yield_slope() -> Optional[List[float]]:
    """Approximate yield curve slope (10Y - 2Y) from FRED or cached data."""
    # Try to read from cached FRED data
    fred_path = PROJECT_ROOT / "data" / "quantum_feed"
    for f in fred_path.glob("fred*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Look for yield curve data
            spread = data.get("data", {}).get("yield_curve_slope")
            if spread is not None:
                return [float(spread)]
        except Exception:
            continue

    # Fetch TLT vs SHY as a proxy
    tlt = _fetch_daily_data("TLT", days=120)
    shy = _fetch_daily_data("SHY", days=120)
    if tlt and shy:
        min_len = min(len(tlt), len(shy))
        tlt_r = _compute_returns(tlt[-min_len:])
        shy_r = _compute_returns(shy[-min_len:])
        # Slope proxy = TLT returns - SHY returns (long end vs short end)
        return list(tlt_r - shy_r)
    return None


def fit_hmm_regime(n_states: int = 3, lookback_days: int = 120) -> Dict[str, Any]:
    """Fit a 3-state Gaussian HMM and return current regime probabilities."""
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        logger.error("hmmlearn not installed. Run: pip3 install hmmlearn")
        return {"error": "hmmlearn not installed"}

    logger.info("Fetching market data for HMM regime detection...")

    # Fetch data
    spy_prices = _fetch_daily_data("SPY", lookback_days)
    vix_prices = _fetch_daily_data("VIXY", lookback_days)  # VIX ETF proxy
    oil_prices = _fetch_daily_data("USO", lookback_days)

    if not spy_prices or len(spy_prices) < 30:
        # Fallback: try UVXY for VIX proxy
        logger.warning("SPY data insufficient, attempting fallback")
        return {"error": "Insufficient market data for HMM fitting"}

    spy_returns = _compute_returns(spy_prices)

    # Build feature matrix
    features = [spy_returns]
    feature_names = ["spy_returns"]

    if vix_prices and len(vix_prices) > 30:
        vix_returns = _compute_returns(vix_prices)
        min_len = min(len(spy_returns), len(vix_returns))
        features = [spy_returns[-min_len:]]
        features.append(vix_returns[-min_len:])
        feature_names.append("vix_returns")
    else:
        # Use SPY volatility as proxy for VIX
        spy_vol = np.array([abs(r) for r in spy_returns])
        features.append(spy_vol)
        feature_names.append("spy_volatility_proxy")

    if oil_prices and len(oil_prices) > 30:
        oil_returns = _compute_returns(oil_prices)
        min_len = min(len(features[0]), len(oil_returns))
        features = [f[-min_len:] for f in features]
        features.append(oil_returns[-min_len:])
        feature_names.append("oil_returns")

    # Yield curve slope
    slope_data = _get_yield_slope()
    if slope_data and len(slope_data) > 1:
        min_len = min(len(features[0]), len(slope_data))
        features = [f[-min_len:] for f in features]
        features.append(np.array(slope_data[-min_len:]))
        feature_names.append("yield_curve_slope")

    # Stack features
    min_len = min(len(f) for f in features)
    X = np.column_stack([f[-min_len:] for f in features])

    if len(X) < 30:
        return {"error": f"Only {len(X)} data points, need at least 30"}

    logger.info("Fitting %d-state HMM on %d observations x %d features: %s",
                n_states, len(X), X.shape[1], feature_names)

    # Fit HMM with multiple random starts for robustness
    best_model = None
    best_score = -np.inf

    for seed in range(10):
        try:
            model = GaussianHMM(
                n_components=n_states,
                covariance_type="full",
                n_iter=200,
                random_state=seed,
                verbose=False,
            )
            model.fit(X)
            score = model.score(X)
            if score > best_score:
                best_score = score
                best_model = model
        except Exception as exc:
            logger.debug("HMM fit seed=%d failed: %s", seed, exc)
            continue

    if best_model is None:
        return {"error": "HMM fitting failed across all random seeds"}

    # Get current regime
    hidden_states = best_model.predict(X)
    current_state = int(hidden_states[-1])

    # Get probability distribution for latest observation
    state_probs = best_model.predict_proba(X)
    current_probs = state_probs[-1].tolist()

    # Label states by volatility (lowest mean abs return = calm, highest = crisis)
    state_volatilities = []
    for s in range(n_states):
        mask = hidden_states == s
        if mask.any():
            vol = float(np.mean(np.abs(X[mask, 0])))  # SPY return volatility
        else:
            vol = 0.0
        state_volatilities.append((s, vol))

    # Sort by volatility to assign labels
    sorted_states = sorted(state_volatilities, key=lambda x: x[1])
    state_label_map = {}
    for rank, (state_idx, vol) in enumerate(sorted_states):
        state_label_map[state_idx] = REGIME_LABELS[rank]

    current_regime = state_label_map.get(current_state, "unknown")

    # Map probabilities to labeled regimes
    regime_probs = {}
    for state_idx, label in state_label_map.items():
        regime_probs[label] = round(current_probs[state_idx], 4)

    # State transition matrix
    transmat = best_model.transmat_.tolist()
    labeled_transmat = {}
    for i in range(n_states):
        from_label = state_label_map[i]
        labeled_transmat[from_label] = {}
        for j in range(n_states):
            to_label = state_label_map[j]
            labeled_transmat[from_label][to_label] = round(transmat[i][j], 4)

    # Recent state history (last 5 days)
    recent_states = [state_label_map.get(int(s), "unknown") for s in hidden_states[-5:]]

    # Regime duration: how many consecutive days in current regime
    regime_duration = 0
    for s in reversed(hidden_states):
        if int(s) == current_state:
            regime_duration += 1
        else:
            break

    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_info": {
            "n_states": n_states,
            "n_features": X.shape[1],
            "feature_names": feature_names,
            "n_observations": len(X),
            "log_likelihood": round(float(best_score), 2),
        },
        "current_regime": current_regime,
        "regime_probabilities": regime_probs,
        "regime_duration_days": regime_duration,
        "recent_regime_history": recent_states,
        "transition_matrix": labeled_transmat,
        "state_volatilities": {
            state_label_map[s]: round(v, 6)
            for s, v in state_volatilities
        },
        "crisis_probability": round(regime_probs.get("crisis", 0.0), 4),
        "calm_probability": round(regime_probs.get("calm", 0.0), 4),
    }

    return result


def run_daily():
    """Run daily HMM regime detection and write output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logger.info("Running HMM regime detector...")
    result = fit_hmm_regime(n_states=3, lookback_days=120)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if "error" in result:
        logger.error("HMM regime detection failed: %s", result["error"])
    else:
        logger.info("HMM regime: %s (crisis_prob=%.2f, calm_prob=%.2f, duration=%d days)",
                     result["current_regime"],
                     result["crisis_probability"],
                     result["calm_probability"],
                     result["regime_duration_days"])
        logger.info("Output written to %s", OUTPUT_PATH)

    return result


if __name__ == "__main__":
    run_daily()
