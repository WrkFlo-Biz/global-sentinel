#!/usr/bin/env python3
"""Multi-Agent RL Ensemble — 5 specialized agents with meta-classifier."""
import json, os, datetime, warnings, traceback
import numpy as np
from pathlib import Path
warnings.filterwarnings("ignore")

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/ensemble_signals.json"
MODELS_DIR = REPO_ROOT / "data/quantum_feed/rl_models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

WATCHLIST = ["SPY","QQQ","NVDA","TSLA","AMD","META","AMZN","AAPL","XLE","PLTR"]

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] ENSEMBLE: {msg}", flush=True)

class BaseAgent:
    def __init__(self, name, features_fn):
        self.name = name
        self.features_fn = features_fn
        self.model = None
        self.recent_pnl = []

    def get_signal(self, market_data):
        features = self.features_fn(market_data)
        if features is None:
            return 0.0
        if self.model is not None:
            try:
                action, _ = self.model.predict(np.array(features, dtype=np.float32), deterministic=True)
                return float(action[0]) if hasattr(action, '__len__') else float(action)
            except:
                pass
        return self._heuristic_signal(features)

    def _heuristic_signal(self, features):
        return 0.0

    def update_pnl(self, pnl):
        self.recent_pnl.append(pnl)
        if len(self.recent_pnl) > 20:
            self.recent_pnl = self.recent_pnl[-20:]

    def avg_pnl(self):
        if not self.recent_pnl:
            return 0.0
        weights = np.exp(np.linspace(-1, 0, len(self.recent_pnl)))
        return float(np.average(self.recent_pnl, weights=weights))

def momentum_features(data):
    try:
        ret_1d = data.get("ret_1d", 0)
        ret_5d = data.get("ret_5d", 0)
        rsi = data.get("rsi", 50) / 100
        macd = data.get("macd_signal", 0)
        vol = data.get("volatility", 0.02) * 10
        trend = data.get("trend_strength", 0)
        return [ret_1d * 10, ret_5d * 5, rsi, macd, vol, trend, 0]
    except:
        return None

def mean_rev_features(data):
    try:
        zscore = data.get("zscore_20d", 0)
        bb_pct = data.get("bollinger_pct", 0.5)
        dist_ma20 = data.get("dist_ma20", 0) * 10
        vol_ratio = data.get("vol_ratio", 1)
        rsi = data.get("rsi", 50) / 100
        return [zscore, bb_pct, dist_ma20, vol_ratio, rsi, 0, 0]
    except:
        return None

def event_features(data):
    try:
        earnings_surprise = data.get("earnings_surprise", 0)
        news_sentiment = data.get("news_sentiment", 0)
        analyst_change = data.get("analyst_change", 0)
        insider_buy = data.get("insider_signal", 0)
        catalyst = data.get("catalyst_score", 0)
        return [earnings_surprise, news_sentiment, analyst_change, insider_buy, catalyst, 0, 0]
    except:
        return None

def vol_features(data):
    try:
        vix = data.get("vix", 20) / 40
        vix_change = data.get("vix_change", 0) * 5
        term_structure = data.get("vix_term_structure", 1)
        uvxy_change = data.get("uvxy_change", 0) * 3
        put_call = data.get("put_call_ratio", 1)
        return [vix, vix_change, term_structure, uvxy_change, put_call, 0, 0]
    except:
        return None

def sentiment_features(data):
    try:
        finbert = data.get("finbert_score", 0)
        stocktwits = data.get("stocktwits_bull_ratio", 0.5)
        reddit = data.get("reddit_sentiment", 0)
        news_velocity = data.get("news_velocity", 0)
        social_volume = data.get("social_volume", 0) / 100
        return [finbert, stocktwits, reddit, news_velocity, social_volume, 0, 0]
    except:
        return None

class MomentumAgent(BaseAgent):
    def __init__(self):
        super().__init__("momentum", momentum_features)
    def _heuristic_signal(self, f):
        return np.tanh(f[0] * 0.5 + f[1] * 0.3 + (f[2] - 0.5) * 0.2)

class MeanReversionAgent(BaseAgent):
    def __init__(self):
        super().__init__("mean_reversion", mean_rev_features)
    def _heuristic_signal(self, f):
        return np.tanh(-f[0] * 0.5 - (f[1] - 0.5) * 0.3 + f[2] * 0.2)

class EventDrivenAgent(BaseAgent):
    def __init__(self):
        super().__init__("event_driven", event_features)
    def _heuristic_signal(self, f):
        return np.tanh(f[0] * 0.3 + f[1] * 0.3 + f[2] * 0.2 + f[3] * 0.2)

class VolatilityAgent(BaseAgent):
    def __init__(self):
        super().__init__("volatility", vol_features)
    def _heuristic_signal(self, f):
        return np.tanh(-f[1] * 0.4 - (f[0] - 0.5) * 0.3 + (1 - f[4]) * 0.3)

class SentimentAgent(BaseAgent):
    def __init__(self):
        super().__init__("sentiment", sentiment_features)
    def _heuristic_signal(self, f):
        return np.tanh(f[0] * 0.3 + (f[1] - 0.5) * 0.3 + f[2] * 0.2 + f[3] * 0.2)

class MetaClassifier:
    def __init__(self, agents):
        self.agents = agents
        n = len(agents)
        self.weights = {a.name: 1.0 / n for a in agents}

    def update_weights(self):
        pnls = {a.name: a.avg_pnl() for a in self.agents}
        total = sum(max(0, p) for p in pnls.values()) + 1e-10
        for a in self.agents:
            self.weights[a.name] = max(0.05, max(0, pnls[a.name]) / total)
        w_sum = sum(self.weights.values())
        self.weights = {k: v / w_sum for k, v in self.weights.items()}

    def get_ensemble_signal(self, market_data):
        signals = {}
        for agent in self.agents:
            sig = agent.get_signal(market_data)
            signals[agent.name] = {"signal": sig, "weight": self.weights[agent.name]}
        weighted_signal = sum(s["signal"] * s["weight"] for s in signals.values())
        return weighted_signal, signals

def gather_market_data(sym):
    """Gather features for a symbol from all available sources."""
    data = {"symbol": sym}
    for fname in ["qlib_alpha_scores.json", "topo_arb_signals.json", "session_intelligence.json",
                   "hmm_regime.json", "latest_signal.json", "ensemble_forecasts.json"]:
        try:
            fpath = REPO_ROOT / "data/quantum_feed" / fname
            if fpath.exists():
                d = json.loads(fpath.read_text())
                data[fname.replace(".json", "")] = d
        except:
            pass

    try:
        import yfinance as yf
        ticker = yf.Ticker(sym)
        hist = ticker.history(period="30d")
        if not hist.empty:
            data["ret_1d"] = float(hist['Close'].pct_change().iloc[-1])
            data["ret_5d"] = float(hist['Close'].iloc[-1] / hist['Close'].iloc[-6] - 1) if len(hist) >= 6 else 0
            data["rsi"] = float(compute_rsi_simple(hist['Close']))
            data["volatility"] = float(hist['Close'].pct_change().std())
            ma20 = hist['Close'].rolling(20).mean().iloc[-1]
            data["dist_ma20"] = float(hist['Close'].iloc[-1] / ma20 - 1) if ma20 > 0 else 0
            data["zscore_20d"] = float((hist['Close'].iloc[-1] - hist['Close'].mean()) / (hist['Close'].std() + 1e-10))
            # Bollinger %B
            bb_mid = hist['Close'].rolling(20).mean()
            bb_std = hist['Close'].rolling(20).std()
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std
            if bb_upper.iloc[-1] != bb_lower.iloc[-1]:
                data["bollinger_pct"] = float((hist['Close'].iloc[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]))
            data["vol_ratio"] = float(hist['Close'].pct_change().tail(5).std() / (hist['Close'].pct_change().std() + 1e-10))
            # MACD signal
            ema12 = hist['Close'].ewm(span=12).mean()
            ema26 = hist['Close'].ewm(span=26).mean()
            data["macd_signal"] = float(ema12.iloc[-1] - ema26.iloc[-1])
            data["trend_strength"] = float(data["ret_5d"] * 2)
    except:
        pass

    # Enrich from news impact data
    try:
        news_path = REPO_ROOT / "data/quantum_feed/news_impact.json"
        if news_path.exists():
            news = json.loads(news_path.read_text())
            for t in news.get("ticker_impact_scores", []):
                if t["ticker"] == sym:
                    data["news_sentiment"] = t.get("avg_impact_score", 0) / 5.0
                    data["news_velocity"] = t.get("headline_count", 0) / 10.0
                    break
    except:
        pass

    # VIX data
    try:
        vix_path = REPO_ROOT / "data/quantum_feed/cboe_vix_data.json"
        if vix_path.exists():
            vix = json.loads(vix_path.read_text())
            data["vix"] = vix.get("data", {}).get("vix_current", vix.get("vix", 20))
            data["vix_change"] = vix.get("data", {}).get("vix_change_pct", 0) / 100.0
    except:
        pass

    return data

def compute_rsi_simple(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return (100 - 100 / (1 + rs)).iloc[-1]

def load_ensemble_state(agents, meta):
    """Load persisted PnL history and model references from ensemble_state.json."""
    state_path = REPO_ROOT / "data/quantum_feed/ensemble_state.json"
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text())
        pnl_history = state.get("meta", {}).get("pnl_history", {})
        for agent in agents:
            if agent.name in pnl_history and pnl_history[agent.name]:
                agent.recent_pnl = pnl_history[agent.name]
        meta.update_weights()
        log(f"Loaded ensemble state: weights={meta.weights}")
    except Exception as e:
        log(f"Could not load ensemble state: {e}")


def load_agent_models(agents):
    """Try to load saved RL models for each agent."""
    backup_dir = REPO_ROOT / "data/quantum_feed/rl_backups/ensemble_models"
    if not backup_dir.exists():
        return
    try:
        from stable_baselines3 import PPO
        for agent in agents:
            model_path = backup_dir / f"{agent.name}_agent.zip"
            if model_path.exists():
                try:
                    agent.model = PPO.load(str(model_path))
                    log(f"Loaded model for {agent.name} from {model_path}")
                except Exception as e:
                    log(f"Could not load model for {agent.name}: {e}")
    except ImportError:
        log("stable_baselines3 not installed, using heuristic signals only")


def save_ensemble_state(agents, meta):
    """Persist ensemble state for next run."""
    state_path = REPO_ROOT / "data/quantum_feed/ensemble_state.json"
    state = {
        "meta": {
            "ewma_pnl": {a.name: a.avg_pnl() for a in agents},
            "pnl_history": {a.name: a.recent_pnl for a in agents},
        },
        "last_run": iso_now(),
        "agent_models": {
            a.name: str(REPO_ROOT / f"data/quantum_feed/rl_backups/ensemble_models/{a.name}_agent.zip")
            for a in agents
        },
    }
    state_path.write_text(json.dumps(state, indent=2))


def seed_synthetic_pnl(agents):
    """Seed agents with synthetic trade PnL from the synthetic simulator if available."""
    sim_path = REPO_ROOT / "data/quantum_feed/synthetic_trade_results.json"
    if not sim_path.exists():
        return
    try:
        data = json.loads(sim_path.read_text())
        trades = data.get("trades", data.get("results", []))
        if not trades:
            return
        # Distribute synthetic PnL across agents based on trade characteristics
        for trade in trades[-20:]:
            pnl = trade.get("pnl", trade.get("return_pct", 0))
            if isinstance(pnl, (int, float)):
                # Assign to agents based on trade type
                for agent in agents:
                    noise = float(np.random.normal(0, 0.001))
                    agent.update_pnl(pnl + noise)
        log(f"Seeded {len(trades[-20:])} synthetic PnL entries to agents")
    except Exception as e:
        log(f"Could not seed synthetic PnL: {e}")


def run():
    log("Running multi-agent ensemble...")
    agents = [MomentumAgent(), MeanReversionAgent(), EventDrivenAgent(), VolatilityAgent(), SentimentAgent()]
    meta = MetaClassifier(agents)

    # Load persisted state and models
    load_ensemble_state(agents, meta)
    load_agent_models(agents)

    # If agents have no PnL history, seed from synthetic data
    if all(len(a.recent_pnl) == 0 for a in agents):
        seed_synthetic_pnl(agents)
        meta.update_weights()

    results = []
    for sym in WATCHLIST:
        try:
            data = gather_market_data(sym)
            ensemble_signal, agent_signals = meta.get_ensemble_signal(data)
            direction = "long" if ensemble_signal > 0.05 else ("short" if ensemble_signal < -0.05 else "neutral")
            results.append({
                "symbol": sym,
                "ensemble_signal": round(ensemble_signal, 4),
                "direction": direction,
                "confidence": round(abs(ensemble_signal), 4),
                "agent_signals": {k: round(v["signal"], 4) for k, v in agent_signals.items()},
                "agent_weights": {k: round(v["weight"], 4) for k, v in agent_signals.items()},
            })
        except Exception as e:
            log(f"Error on {sym}: {e}")

    results.sort(key=lambda x: abs(x["ensemble_signal"]), reverse=True)
    output = {
        "timestamp": iso_now(),
        "meta_weights": {k: round(v, 4) for k, v in meta.weights.items()},
        "signals": results,
        "top_longs": [r for r in results if r["direction"] == "long"][:5],
        "top_shorts": [r for r in results if r["direction"] == "short"][:5],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))

    # Persist state for next run
    save_ensemble_state(agents, meta)

    log(f"Ensemble complete. {len(results)} symbols scored.")
    for r in results[:5]:
        log(f"  {r['direction'].upper():6s} {r['symbol']:6s} signal={r['ensemble_signal']:+.4f}")

if __name__ == "__main__":
    run()
