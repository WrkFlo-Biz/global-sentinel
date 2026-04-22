#!/usr/bin/env python3
"""Closed-Loop Feedback Pipeline — wires trade outcomes back to signal quality scoring."""
import json, os, datetime, glob, traceback
import numpy as np
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
DATASET_PATH = REPO_ROOT / "data/quantum_feed/trade_feedback_dataset.jsonl"
WEIGHTS_PATH = REPO_ROOT / "data/quantum_feed/signal_quality_weights.json"
PARAMS_PATH = REPO_ROOT / "data/quantum_feed/optimized_params.json"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] FEEDBACK: {msg}", flush=True)

def collect_trade_outcomes():
    """Collect all trade outcomes from paper trades and live trades."""
    outcomes = []
    # Paper day trades
    for f in sorted(glob.glob(str(REPO_ROOT / "reports/paper_trades/day_trade_*.json"))):
        try:
            data = json.loads(Path(f).read_text())
            for pos in data.get("positions", []):
                outcomes.append({
                    "source": "paper_day",
                    "date": data.get("date"),
                    "symbol": pos.get("underlying", pos.get("symbol", "")),
                    "direction": pos.get("direction", "unknown"),
                    "pnl": pos.get("realized_pnl", 0),
                    "profitable": pos.get("realized_pnl", 0) > 0,
                })
        except Exception:
            pass
    # Paper medlong
    for f in sorted(glob.glob(str(REPO_ROOT / "reports/paper_trades/medlong_*.json"))):
        try:
            data = json.loads(Path(f).read_text())
            outcomes.append({"source": "paper_medlong", "data": data})
        except Exception:
            pass
    # Live trades via Alpaca
    try:
        import urllib.request
        env = {}
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
        key = env.get("ALPACA_API_KEY_LIVE", "")
        secret = env.get("ALPACA_SECRET_KEY_LIVE", "")
        if key and secret:
            url = "https://api.alpaca.markets/v2/orders?status=closed&limit=50"
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", key)
            req.add_header("APCA-API-SECRET-KEY", secret)
            with urllib.request.urlopen(req, timeout=15) as resp:
                orders = json.loads(resp.read())
            buys = {}
            sells = {}
            for o in orders:
                if not o.get("filled_avg_price"): continue
                sym = o.get("symbol", "")
                price = float(o["filled_avg_price"])
                qty = float(o.get("filled_qty", 0))
                side = o.get("side", "")
                if side == "buy":
                    buys.setdefault(sym, []).append({"price": price, "qty": qty, "date": o.get("filled_at", "")[:10]})
                else:
                    sells.setdefault(sym, []).append({"price": price, "qty": qty, "date": o.get("filled_at", "")[:10]})
            for sym in set(list(buys.keys()) + list(sells.keys())):
                b_list = buys.get(sym, [])
                s_list = sells.get(sym, [])
                if b_list and s_list:
                    avg_buy = sum(x["price"] * x["qty"] for x in b_list) / max(1, sum(x["qty"] for x in b_list))
                    avg_sell = sum(x["price"] * x["qty"] for x in s_list) / max(1, sum(x["qty"] for x in s_list))
                    pnl = (avg_sell - avg_buy) * sum(x["qty"] for x in s_list)
                    outcomes.append({
                        "source": "live", "symbol": sym,
                        "avg_buy": round(avg_buy, 4), "avg_sell": round(avg_sell, 4),
                        "pnl": round(pnl, 2), "profitable": pnl > 0,
                        "date": s_list[0].get("date", ""),
                    })
    except Exception as e:
        log(f"Live trade fetch error: {e}")
    return outcomes

def collect_signals_at_time(date_str):
    """Collect what signals were active at a given date."""
    signals = {}
    signal_files = [
        "qlib_alpha_scores.json", "topo_arb_signals.json", "ensemble_signals.json",
        "hmm_regime.json", "latest_signal.json", "session_intelligence.json",
        "price_forecasts.json", "polymarket_geopolitical.json",
    ]
    for fname in signal_files:
        try:
            fpath = REPO_ROOT / "data/quantum_feed" / fname
            if fpath.exists():
                signals[fname.replace(".json", "")] = True
        except Exception:
            pass
    return signals

def compute_signal_quality(outcomes):
    """Compute quality score for each signal source based on trade outcomes."""
    if not outcomes:
        log("No trade outcomes to analyze")
        return {}

    profitable = [o for o in outcomes if o.get("profitable", False)]
    unprofitable = [o for o in outcomes if not o.get("profitable", True)]

    total = len(outcomes)
    win_rate = len(profitable) / max(1, total)

    quality = {
        "timestamp": iso_now(),
        "total_trades": total,
        "win_rate": round(win_rate, 3),
        "profitable_trades": len(profitable),
        "unprofitable_trades": len(unprofitable),
        "signal_scores": {
            "momentum": round(0.5 + win_rate * 0.3, 3),
            "mean_reversion": round(0.5 + (1 - win_rate) * 0.2, 3),
            "regime_scoring": round(0.6 + win_rate * 0.2, 3),
            "quantum_signals": round(0.4 + win_rate * 0.3, 3),
            "topological_arb": round(0.5 + win_rate * 0.2, 3),
            "sentiment": round(0.4 + win_rate * 0.2, 3),
            "session_timing": round(0.5 + win_rate * 0.3, 3),
        },
        "recommendations": [],
    }

    if win_rate > 0.6:
        quality["recommendations"].append("Increase position sizes — win rate is strong")
    elif win_rate < 0.4:
        quality["recommendations"].append("Reduce position sizes — win rate is weak")
        quality["recommendations"].append("Tighten stop losses")

    return quality

def optimize_hyperparams(outcomes):
    """Use Optuna to optimize trading parameters."""
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        log("Optuna not installed, skipping hyperparameter optimization")
        return None

    if len(outcomes) < 10:
        log(f"Only {len(outcomes)} trades, need 10+ for optimization")
        return None

    def objective(trial):
        stop_loss = trial.suggest_float("stop_loss_pct", 3, 20)
        take_profit = trial.suggest_float("take_profit_pct", 20, 200)
        confidence_threshold = trial.suggest_float("confidence_threshold", 0.3, 0.8)
        position_size_pct = trial.suggest_float("position_size_pct", 3, 15)

        simulated_pnl = []
        for o in outcomes:
            pnl = o.get("pnl", 0)
            if abs(pnl) > take_profit * position_size_pct / 100:
                simulated_pnl.append(take_profit * position_size_pct / 100)
            elif abs(pnl) > stop_loss * position_size_pct / 100:
                simulated_pnl.append(-stop_loss * position_size_pct / 100)
            else:
                simulated_pnl.append(pnl * position_size_pct / 10)

        if not simulated_pnl:
            return 0
        mean_ret = np.mean(simulated_pnl)
        std_ret = np.std(simulated_pnl) + 1e-10
        return mean_ret / std_ret  # Sharpe-like

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=100, show_progress_bar=False)

    best = study.best_params
    best["best_sharpe"] = round(study.best_value, 4)
    best["n_trials"] = 100
    best["timestamp"] = iso_now()

    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(best, indent=2))
    log(f"Optimized params: stop={best['stop_loss_pct']:.1f}% take={best['take_profit_pct']:.0f}% "
        f"conf={best['confidence_threshold']:.2f} size={best['position_size_pct']:.1f}% sharpe={best['best_sharpe']:.3f}")
    return best

def run():
    log("Starting feedback loop...")
    outcomes = collect_trade_outcomes()
    log(f"Collected {len(outcomes)} trade outcomes")

    # Build feedback dataset
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATASET_PATH, "a") as f:
        for o in outcomes:
            o["collected_at"] = iso_now()
            f.write(json.dumps(o) + "\n")

    # Compute signal quality
    quality = compute_signal_quality(outcomes)
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(quality, indent=2))
    log(f"Signal quality updated: win_rate={quality.get('win_rate', 0):.1%}")

    # Optimize hyperparameters
    params = optimize_hyperparams(outcomes)
    if params:
        log("Hyperparameter optimization complete")

    log("Feedback loop complete")

if __name__ == "__main__":
    run()
