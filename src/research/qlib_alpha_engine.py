#!/usr/bin/env python3
"""Qlib Alpha Engine — AI-driven alpha scoring using LightGBM."""
import json, os, datetime, warnings, traceback
import numpy as np
from pathlib import Path
warnings.filterwarnings("ignore")

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/qlib_alpha_scores.json"

WATCHLIST = [
    "SPY","QQQ","AAPL","MSFT","NVDA","AMD","META","GOOGL","AMZN","TSLA",
    "XLE","XOM","CVX","OXY","DAL","UAL","LMT","RTX","BA","PLTR",
    "COIN","CCL","SOXL","ARM","SMCI","GLD","TLT","IWM","SOFI","MU"
]

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] QLIB_ALPHA: {msg}", flush=True)

def compute_features(prices):
    """Compute alpha factors from price data."""
    features = {}
    for sym in prices.columns:
        p = prices[sym].dropna()
        if len(p) < 20:
            continue
        ret_1d = p.pct_change(1).iloc[-1]
        ret_5d = p.pct_change(5).iloc[-1] if len(p) >= 6 else 0
        ret_20d = p.pct_change(20).iloc[-1] if len(p) >= 21 else 0
        vol_5d = p.pct_change().iloc[-5:].std() if len(p) >= 6 else 0
        vol_20d = p.pct_change().iloc[-20:].std() if len(p) >= 21 else 0
        ma_5 = p.iloc[-5:].mean() / p.iloc[-1] - 1 if len(p) >= 5 else 0
        ma_20 = p.iloc[-20:].mean() / p.iloc[-1] - 1 if len(p) >= 20 else 0
        rsi_14 = compute_rsi(p, 14)
        high_20 = p.iloc[-20:].max() / p.iloc[-1] - 1 if len(p) >= 20 else 0
        low_20 = p.iloc[-20:].min() / p.iloc[-1] - 1 if len(p) >= 20 else 0
        features[sym] = {
            "ret_1d": float(ret_1d), "ret_5d": float(ret_5d), "ret_20d": float(ret_20d),
            "vol_5d": float(vol_5d), "vol_20d": float(vol_20d),
            "ma_5_dist": float(ma_5), "ma_20_dist": float(ma_20),
            "rsi_14": float(rsi_14), "high_20_dist": float(high_20), "low_20_dist": float(low_20),
        }
    return features

def compute_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if len(rsi) > 0 else 50

def train_and_predict(features, returns_forward):
    """Train LightGBM model and predict alpha scores."""
    try:
        from sklearn.ensemble import GradientBoostingRegressor
        symbols = sorted(set(features.keys()) & set(returns_forward.keys()))
        if len(symbols) < 10:
            return {}
        X, y, syms = [], [], []
        for s in symbols:
            f = features[s]
            X.append([f["ret_1d"], f["ret_5d"], f["ret_20d"], f["vol_5d"], f["vol_20d"],
                       f["ma_5_dist"], f["ma_20_dist"], f["rsi_14"], f["high_20_dist"], f["low_20_dist"]])
            y.append(returns_forward[s])
            syms.append(s)
        X = np.array(X)
        y = np.array(y)
        model = GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42)
        model.fit(X, y)
        predictions = model.predict(X)
        scores = {}
        for i, s in enumerate(syms):
            scores[s] = float(predictions[i])
        return scores
    except Exception as e:
        log(f"Model error: {e}")
        return {}

def run():
    log("Starting alpha scoring...")
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        log("yfinance/pandas not available")
        return

    end = datetime.date.today()
    start = end - datetime.timedelta(days=120)
    data = yf.download(WATCHLIST, start=start, end=end, progress=False, auto_adjust=True)
    if data.empty:
        log("No data")
        return

    prices = data['Close'] if 'Close' in data.columns.get_level_values(0) else data
    features = compute_features(prices.iloc[:-5])
    returns_5d = {}
    for sym in prices.columns:
        p = prices[sym].dropna()
        if len(p) >= 6:
            returns_5d[sym] = float(p.iloc[-1] / p.iloc[-6] - 1)

    scores = train_and_predict(features, returns_5d)
    if not scores:
        log("No scores generated, using momentum fallback")
        for sym in WATCHLIST:
            if sym in prices.columns:
                p = prices[sym].dropna()
                if len(p) >= 6:
                    scores[sym] = float(p.iloc[-1] / p.iloc[-6] - 1)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    output = {
        "timestamp": iso_now(),
        "model": "gradient_boosting_alpha",
        "symbols_scored": len(ranked),
        "scores": [
            {"symbol": s, "alpha_score": round(score, 6),
             "rank": i + 1, "signal_strength": round(abs(score) * 100, 2),
             "direction": "long" if score > 0 else "short"}
            for i, (s, score) in enumerate(ranked)
        ]
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    log(f"Scored {len(ranked)} symbols. Top 5:")
    for s in output["scores"][:5]:
        log(f"  #{s['rank']}: {s['direction'].upper()} {s['symbol']} alpha={s['alpha_score']:.4f}")

if __name__ == "__main__":
    run()
