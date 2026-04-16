#!/usr/bin/env python3
"""Quantum Continuous Learning & Self-Improvement System for Global Sentinel.

Runs 24/7 as a daemon, continuously ingesting ALL system data, running Monte Carlo
scenario simulations, training quantum models (PennyLane VQC + Qiskit QAOA),
tracking prediction accuracy, and feeding improved weights back into the trade engine.

Heavy computation (scenarios, QAOA) runs off-market hours (4:30 PM - 8:30 AM ET).
During market hours, only lightweight anomaly scoring runs.

systemd service: gs-quantum-learner.service
"""
from __future__ import annotations

import gc
import glob
import hashlib
import json
import logging
import math
import os
import random
import resource
import signal
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path("/opt/global-sentinel")
sys.path.insert(0, str(REPO_ROOT))

ENV_PATH = REPO_ROOT / ".env"
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
QUANTUM_FEED = DATA_DIR / "quantum_feed"
WHATIF_DIR = DATA_DIR / "whatif"
WHATIF_LEARNING = DATA_DIR / "whatif_learning"
REPORTS_DIR = REPO_ROOT / "reports"
RESEARCH_DIR = REPORTS_DIR / "research"
LEARNING_METRICS_DIR = RESEARCH_DIR / "learning_metrics"
SELF_IMPROVEMENT_DIR = RESEARCH_DIR / "self_improvement"
PAPER_TRADES_DIR = REPORTS_DIR / "paper_trades"
COMPARISONS_DIR = RESEARCH_DIR / "comparisons"
EXPERIMENT_LOG = RESEARCH_DIR / "experiment_log.jsonl"
WEIGHTS_PATH = CONFIG_DIR / "anomaly_detector_weights.json"
REGIME_CAL_PATH = CONFIG_DIR / "regime_scorer_calibration.json"
LATEST_SIGNAL_PATH = QUANTUM_FEED / "latest_signal.json"
STRATEGY_RECS_PATH = QUANTUM_FEED / "strategy_recommendations.json"
LEARNER_STATE_PATH = QUANTUM_FEED / "learner_state.json"
LOG_DIR = REPO_ROOT / "logs" / "research"

ET = timezone(timedelta(hours=-4))  # EDT
MAX_MEMORY_MB = 1500  # hard cap

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("quantum-continuous-learner")

# ---------------------------------------------------------------------------
# Env loader
# ---------------------------------------------------------------------------
def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

load_env()

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_SHUTDOWN = False

def _signal_handler(signum, frame):
    global _SHUTDOWN
    logger.info("Received signal %s -- shutting down gracefully", signum)
    _SHUTDOWN = True

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def et_now() -> datetime:
    return datetime.now(ET)

def is_market_hours() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 30) <= t <= dtime(16, 0)

def is_heavy_compute_window() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return True
    t = now.time()
    return t >= dtime(16, 30) or t < dtime(8, 30)

def memory_mb() -> float:
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return ru.ru_maxrss / 1024  # Linux returns KB
    except Exception:
        return 0.0

def check_memory_guard() -> bool:
    mb = memory_mb()
    if mb > MAX_MEMORY_MB:
        logger.warning("Memory usage %.1f MB exceeds cap %d MB -- forcing GC", mb, MAX_MEMORY_MB)
        gc.collect()
        return False
    return True

def safe_json_read(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def safe_jsonl_read(path: Path, max_lines: int = 500) -> List[Dict]:
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return results

def safe_json_write(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.rename(path)
        return True
    except Exception as e:
        logger.error("Failed to write %s: %s", path, e)
        return False

def http_get_json(url: str, headers: Optional[Dict] = None, timeout: int = 15) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        import ssl
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("HTTP GET %s failed: %s", url, e)
        return None

def _truncate_dict(d: Any, max_keys: int = 20) -> Any:
    if isinstance(d, dict):
        items = list(d.items())[:max_keys]
        return {k: (v if not isinstance(v, (dict, list)) else "<{} len={}>".format(type(v).__name__, len(v))) for k, v in items}
    if isinstance(d, list) and len(d) > max_keys:
        return d[:max_keys]
    return d


# ===================================================================
# 1. DATA INGESTION
# ===================================================================
class DataIngestion:

    def __init__(self):
        self.last_ingest_ts = None
        self.alpaca_live_key = os.environ.get("ALPACA_API_KEY_LIVE", "AKXM6W3IPXYEJUVO67ELCTEAHX")
        self.alpaca_live_secret = os.environ.get("ALPACA_SECRET_KEY_LIVE", "C3tYHmaesMGmiRRdA1QunvVpivp1S8GBwyB2YjwnXt2d")

    def collect_all(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"timestamp": utc_now(), "sources": {}}

        collectors = [
            ("bridge_results", self._collect_bridge_results),
            ("paper_trades_daytrade", self._collect_paper_trades_daytrade),
            ("paper_trades_medlong", self._collect_paper_trades_medlong),
            ("live_trade_history", self._collect_live_trades),
            ("whatif_picks", self._collect_whatif),
            ("regime_scores", self._collect_regime_scores),
            ("quantum_experiments", self._collect_quantum_experiments),
            ("latest_signal", self._collect_latest_signal),
            ("vol_trader_log", self._collect_vol_trader),
            ("stop_loss_events", self._collect_stop_loss_events),
            ("insider_signals", self._collect_insider_signals),
            ("stocktwits_sentiment", self._collect_stocktwits_sentiment),
            ("earnings_surprise", self._collect_earnings_surprise),
            ("analyst_ratings", self._collect_analyst_ratings),
            ("options_activity", self._collect_options_activity),
            ("comparison_artifacts", self._collect_comparison_artifacts),
        ]

        for name, fn in collectors:
            try:
                result = fn()
                count = len(result) if isinstance(result, (list, dict)) else 0
                data["sources"][name] = {"status": "ok", "count": count, "data": result}
            except Exception as e:
                data["sources"][name] = {"status": "error", "error": str(e), "data": []}
                logger.debug("Ingestion source %s failed: %s", name, e)

        data["sources_collected"] = sum(1 for v in data["sources"].values() if v.get("status") == "ok")
        data["sources_failed"] = sum(1 for v in data["sources"].values() if v.get("status") == "error")
        self.last_ingest_ts = utc_now()
        return data

    def _collect_bridge_results(self) -> List[Dict]:
        results = []
        for pattern in [
            str(DATA_DIR / "pit" / "*.json"),
            str(DATA_DIR / "bridge_cache_*.json"),
            str(DATA_DIR / "*.json"),
        ]:
            for path in sorted(glob.glob(pattern))[-50:]:
                p = Path(path)
                try:
                    if p.stat().st_size > 500_000:
                        continue
                except OSError:
                    continue
                doc = safe_json_read(p)
                if doc:
                    results.append({"file": p.name, "mtime": p.stat().st_mtime, "snippet": _truncate_dict(doc, 20)})
        return results[-50:]

    def _collect_paper_trades_daytrade(self) -> List[Dict]:
        files = sorted(glob.glob(str(PAPER_TRADES_DIR / "day_trade_*.json")))[-20:]
        return [safe_json_read(Path(f)) or {} for f in files]

    def _collect_paper_trades_medlong(self) -> List[Dict]:
        files = sorted(glob.glob(str(PAPER_TRADES_DIR / "medlong_week_*.json")))[-20:]
        return [safe_json_read(Path(f)) or {} for f in files]

    def _collect_live_trades(self) -> List[Dict]:
        url = "https://api.alpaca.markets/v2/orders?status=all&limit=50&direction=desc"
        headers = {
            "APCA-API-KEY-ID": self.alpaca_live_key,
            "APCA-API-SECRET-KEY": self.alpaca_live_secret,
        }
        result = http_get_json(url, headers=headers, timeout=10)
        if isinstance(result, list):
            return result[:50]
        return []

    def _collect_whatif(self) -> List[Dict]:
        results = []
        for f in sorted(glob.glob(str(WHATIF_DIR / "*.json")))[-10:]:
            doc = safe_json_read(Path(f))
            if doc:
                results.append(doc)
        for f in sorted(glob.glob(str(WHATIF_DIR / "*.jsonl")))[-5:]:
            results.extend(safe_jsonl_read(Path(f), max_lines=50))
        return results

    def _collect_regime_scores(self) -> Dict:
        sig = safe_json_read(LATEST_SIGNAL_PATH) or {}
        cal = safe_json_read(REGIME_CAL_PATH) or {}
        regime_state_path = RESEARCH_DIR / "state" / "regime_state.json"
        regime_state = safe_json_read(regime_state_path) if regime_state_path.exists() else {}
        # Include quantum reservoir regime prediction as additional evidence
        qr_prediction = safe_json_read(QUANTUM_FEED / "quantum_regime_prediction.json") or {}
        hmm_regime = safe_json_read(QUANTUM_FEED / "hmm_regime.json") or {}
        return {
            "signal_buckets": sig.get("bucket_scores", {}),
            "calibration_events": len(cal) if isinstance(cal, list) else 0,
            "regime_state": regime_state or {},
            "hmm_regime": hmm_regime.get("current_regime"),
            "quantum_reservoir_regime": qr_prediction.get("quantum_prediction", {}).get("regime"),
            "quantum_reservoir_confidence": qr_prediction.get("quantum_prediction", {}).get("confidence"),
            "quantum_reservoir_ensemble": qr_prediction.get("ensemble_probabilities"),
        }

    def _collect_quantum_experiments(self) -> List[Dict]:
        return safe_jsonl_read(EXPERIMENT_LOG, max_lines=100)[-50:]

    def _collect_latest_signal(self) -> Dict:
        return safe_json_read(LATEST_SIGNAL_PATH) or {}

    def _collect_vol_trader(self) -> List[Dict]:
        log_path = REPO_ROOT / "logs" / "vol_trader.jsonl"
        if log_path.exists():
            return safe_jsonl_read(log_path, max_lines=100)[-50:]
        return []

    def _collect_stop_loss_events(self) -> List[Dict]:
        log_path = REPO_ROOT / "logs" / "stop_loss.jsonl"
        if log_path.exists():
            return safe_jsonl_read(log_path, max_lines=100)[-50:]
        for p in sorted(glob.glob(str(DATA_DIR / "stop_loss_*.json")))[-5:]:
            doc = safe_json_read(Path(p))
            if doc:
                return [doc]
        return []

    def _collect_insider_signals(self) -> List[Dict]:
        results = []
        for p in sorted(glob.glob(str(DATA_DIR / "pit" / "insider_*.json")))[-10:]:
            doc = safe_json_read(Path(p))
            if doc:
                results.append(doc)
        for p in sorted(glob.glob(str(DATA_DIR / "insider_*.json")))[-10:]:
            doc = safe_json_read(Path(p))
            if doc:
                results.append(doc)
        return results

    def _collect_stocktwits_sentiment(self) -> List[Dict]:
        for p in sorted(glob.glob(str(DATA_DIR / "pit" / "stocktwits_*.json")))[-5:]:
            doc = safe_json_read(Path(p))
            if doc:
                return [doc]
        return []

    def _collect_earnings_surprise(self) -> List[Dict]:
        for p in sorted(glob.glob(str(DATA_DIR / "pit" / "earnings_*.json")))[-5:]:
            doc = safe_json_read(Path(p))
            if doc:
                return [doc]
        return []

    def _collect_analyst_ratings(self) -> List[Dict]:
        for p in sorted(glob.glob(str(DATA_DIR / "pit" / "analyst_*.json")))[-5:]:
            doc = safe_json_read(Path(p))
            if doc:
                return [doc]
        return []

    def _collect_options_activity(self) -> List[Dict]:
        results = []
        for p in sorted(glob.glob(str(DATA_DIR / "pit" / "options_*.json")))[-5:]:
            doc = safe_json_read(Path(p))
            if doc:
                results.append(doc)
        return results

    def _collect_comparison_artifacts(self) -> List[Dict]:
        files = sorted(glob.glob(str(COMPARISONS_DIR / "comparison_*.json")))[-10:]
        results = []
        for f in files:
            doc = safe_json_read(Path(f))
            if doc:
                results.append(_truncate_dict(doc, 30))
        return results


# ===================================================================
# 2. SCENARIO SIMULATION ENGINE
# ===================================================================
class ScenarioSimulator:

    SCENARIO_CONFIGS = {
        "normal_to_elevated":        {"mu": -0.001, "sigma": 0.025, "shock_bps": -200},
        "elevated_to_crisis":        {"mu": -0.005, "sigma": 0.05,  "shock_bps": -500},
        "crisis_to_normal":          {"mu":  0.003, "sigma": 0.03,  "shock_bps":  300},
        "tech_to_energy":            {"mu": -0.002, "sigma": 0.03,  "shock_bps": -150},
        "energy_to_defense":         {"mu":  0.001, "sigma": 0.025, "shock_bps":  100},
        "defense_to_consumer":       {"mu":  0.002, "sigma": 0.02,  "shock_bps":   50},
        "earnings_surprise_pos":     {"mu":  0.005, "sigma": 0.04,  "shock_bps":  400},
        "earnings_surprise_neg":     {"mu": -0.005, "sigma": 0.04,  "shock_bps": -400},
        "fed_rate_hike":             {"mu": -0.002, "sigma": 0.03,  "shock_bps": -250},
        "fed_rate_cut":              {"mu":  0.003, "sigma": 0.025, "shock_bps":  200},
        "geopolitical_escalation":   {"mu": -0.004, "sigma": 0.045, "shock_bps": -350},
        "peace_talks":               {"mu":  0.002, "sigma": 0.02,  "shock_bps":  150},
        "tariff_announcement":       {"mu": -0.003, "sigma": 0.035, "shock_bps": -300},
        "flash_crash":               {"mu": -0.01,  "sigma": 0.08,  "shock_bps": -1000},
        "vix_spike_40":              {"mu": -0.008, "sigma": 0.06,  "shock_bps": -800},
        "oil_shock_10pct":           {"mu": -0.005, "sigma": 0.04,  "shock_bps": -500},
        "currency_crisis":           {"mu": -0.006, "sigma": 0.05,  "shock_bps": -600},
        "covid_crash_2020":          {"mu": -0.015, "sigma": 0.1,   "shock_bps": -1200},
        "rate_hike_cycle_2022":      {"mu": -0.003, "sigma": 0.035, "shock_bps": -250},
        "ai_rally_2024":             {"mu":  0.008, "sigma": 0.04,  "shock_bps":  500},
        "iran_escalation":           {"mu": -0.004, "sigma": 0.045, "shock_bps": -400},
        # --- Expanded scenario suite (2026-03-25) ---
        "correlated_crash":          {"mu": -0.012, "sigma": 0.09,  "shock_bps": -1000},
        "liquidity_crisis":          {"mu": -0.008, "sigma": 0.07,  "shock_bps": -700},
        "flash_rally":               {"mu":  0.010, "sigma": 0.06,  "shock_bps":  800},
        "fed_surprise_cut":          {"mu":  0.006, "sigma": 0.04,  "shock_bps":  500},
        "fed_surprise_hike":         {"mu": -0.006, "sigma": 0.04,  "shock_bps": -500},
        "earnings_cascade":          {"mu": -0.007, "sigma": 0.055, "shock_bps": -600},
        "oil_shock_hormuz":          {"mu": -0.009, "sigma": 0.07,  "shock_bps": -900},
        "currency_crisis_dxy":       {"mu": -0.007, "sigma": 0.06,  "shock_bps": -650},
        "crypto_contagion":          {"mu": -0.006, "sigma": 0.055, "shock_bps": -500},
    }

    STRATEGY_PARAMS = [
        {"name": "aggressive_momentum", "weight_equity": 0.8, "weight_safe_haven": 0.1, "weight_defense": 0.1},
        {"name": "balanced_defensive",  "weight_equity": 0.4, "weight_safe_haven": 0.35, "weight_defense": 0.25},
        {"name": "crisis_hedge",        "weight_equity": 0.1, "weight_safe_haven": 0.5, "weight_defense": 0.4},
        {"name": "sector_rotation",     "weight_equity": 0.6, "weight_safe_haven": 0.2, "weight_defense": 0.2},
        {"name": "vol_harvest",         "weight_equity": 0.5, "weight_safe_haven": 0.3, "weight_defense": 0.2},
    ]

    def __init__(self):
        self.scenario_history: List[Dict] = []
        self.strategy_wins: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._load_history()

    def _load_history(self):
        hist_path = LEARNING_METRICS_DIR / "scenario_history.json"
        if hist_path.exists():
            data = safe_json_read(hist_path)
            if data:
                self.scenario_history = data.get("history", [])[-500:]
                self.strategy_wins = defaultdict(lambda: defaultdict(int), {
                    k: defaultdict(int, v) for k, v in data.get("strategy_wins", {}).items()
                })

    def _save_history(self):
        safe_json_write(LEARNING_METRICS_DIR / "scenario_history.json", {
            "saved_at": utc_now(),
            "history": self.scenario_history[-500:],
            "strategy_wins": dict(self.strategy_wins),
        })

    def run_scenario_batch(self, n_paths: int = 10000, lightweight: bool = False) -> Dict[str, Any]:
        if lightweight:
            n_paths = 500
            scenarios_to_run = random.sample(
                list(self.SCENARIO_CONFIGS.keys()),
                min(10, len(self.SCENARIO_CONFIGS)),
            )
        else:
            scenarios_to_run = list(self.SCENARIO_CONFIGS.keys())

        batch_results: Dict[str, Any] = {
            "timestamp": utc_now(),
            "n_paths": n_paths,
            "scenarios": {},
            "best_strategies": {},
        }

        try:
            from src.research.monte_carlo_scenario_engine import MonteCarloScenarioEngine
            engine = MonteCarloScenarioEngine()
        except ImportError:
            engine = None

        for scenario_name in scenarios_to_run:
            if _SHUTDOWN:
                break
            if not check_memory_guard():
                break

            config = self.SCENARIO_CONFIGS[scenario_name]
            seed = int(time.time() * 1000) % (2**31)

            if engine:
                paths = engine.generate_paths(
                    n_paths=n_paths, n_steps=20,
                    mu=config["mu"], sigma=config["sigma"],
                    shock_bps=config["shock_bps"], seed=seed,
                )
                summary = engine.summarize(paths)
            else:
                summary = self._inline_monte_carlo(config, n_paths, seed)

            strategy_pnls = {}
            for strat in self.STRATEGY_PARAMS:
                pnl = self._simulate_strategy_pnl(summary, strat, config)
                strategy_pnls[strat["name"]] = pnl

            best_strat = max(strategy_pnls, key=strategy_pnls.get) if strategy_pnls else "balanced_defensive"
            self.strategy_wins[scenario_name][best_strat] += 1

            batch_results["scenarios"][scenario_name] = {
                "summary": summary if isinstance(summary, dict) else {},
                "strategy_pnls": strategy_pnls,
                "best_strategy": best_strat,
            }
            batch_results["best_strategies"][scenario_name] = best_strat

        self.scenario_history.append({"timestamp": utc_now(), "batch_size": len(batch_results["scenarios"])})
        self._save_history()
        return batch_results

    def _inline_monte_carlo(self, config: Dict, n_paths: int, seed: int) -> Dict:
        rng = random.Random(seed)
        terminals = []
        for _ in range(n_paths):
            total = 1.0
            for step in range(20):
                ret = rng.gauss(config["mu"], config["sigma"])
                if step == 0:
                    ret += config["shock_bps"] / 10000.0
                total *= (1.0 + ret)
            terminals.append(total - 1.0)
        terminals.sort()
        n = len(terminals)
        return {
            "count": n,
            "mean_terminal_return": sum(terminals) / n,
            "p05_terminal_return": terminals[max(0, int(0.05 * n) - 1)],
            "p50_terminal_return": terminals[max(0, int(0.50 * n) - 1)],
            "p95_terminal_return": terminals[max(0, int(0.95 * n) - 1)],
        }

    def _simulate_strategy_pnl(self, summary: Dict, strategy: Dict, config: Dict) -> float:
        base_return = summary.get("mean_terminal_return", 0.0)
        shock = config.get("shock_bps", 0) / 10000.0
        equity_ret = base_return * strategy["weight_equity"]
        safe_haven_ret = (-shock * 0.3) * strategy["weight_safe_haven"]
        defense_ret = (shock * -0.15 + 0.002) * strategy["weight_defense"]
        return equity_ret + safe_haven_ret + defense_ret

    def get_strategy_rankings(self) -> Dict[str, float]:
        totals: Dict[str, int] = defaultdict(int)
        for scenario_wins in self.strategy_wins.values():
            for strat, count in scenario_wins.items():
                totals[strat] += count
        total_all = sum(totals.values()) or 1
        return {k: v / total_all for k, v in sorted(totals.items(), key=lambda x: -x[1])}


# ===================================================================
# 3. RECURSIVE TRAINING LOOP
# ===================================================================
class QuantumTrainer:

    def __init__(self):
        self.cycle_count = 0
        self.quantum_weight = 0.5
        self.training_history: List[Dict] = []
        self._load_state()

    def _load_state(self):
        state = safe_json_read(LEARNER_STATE_PATH)
        if state:
            self.cycle_count = state.get("cycle_count", 0)
            self.quantum_weight = state.get("quantum_weight", 0.5)
            self.training_history = state.get("training_history", [])[-200:]

    def _save_state(self):
        safe_json_write(LEARNER_STATE_PATH, {
            "cycle_count": self.cycle_count,
            "quantum_weight": self.quantum_weight,
            "training_history": self.training_history[-200:],
            "last_updated": utc_now(),
        })

    def extract_features(self, ingested_data: Dict) -> List[List[float]]:
        features = []
        sources = ingested_data.get("sources", {})

        # From latest signal bucket scores
        signal_data = sources.get("latest_signal", {}).get("data", {})
        buckets = signal_data.get("bucket_scores", {})
        if buckets:
            bucket_vals = [float(v) / 10.0 for v in buckets.values()]
            while len(bucket_vals) < 10:
                bucket_vals.append(0.0)
            features.append(bucket_vals[:10])

        # From comparison artifacts
        comparisons = sources.get("comparison_artifacts", {}).get("data", [])
        for comp in comparisons[:20]:
            if isinstance(comp, dict):
                vals = []
                for key in ["preopt_score", "expected_return", "volatility", "quantum_anomaly_score"]:
                    v = comp.get(key)
                    if v is not None:
                        try:
                            vals.append(float(v))
                        except (TypeError, ValueError):
                            vals.append(0.0)
                if len(vals) >= 2:
                    while len(vals) < 8:
                        vals.append(0.0)
                    features.append(vals[:8])

        # From quantum experiments
        experiments = sources.get("quantum_experiments", {}).get("data", [])
        for exp in experiments[-20:]:
            if isinstance(exp, dict):
                report = exp.get("report", exp)
                results = report.get("results", {})
                if isinstance(results, dict):
                    for backend_name, result in results.items():
                        if isinstance(result, dict):
                            obj_val = result.get("objective_value")
                            n_selected = result.get("num_assets_selected", 0)
                            feasibility = result.get("feasibility", 0)
                            if obj_val is not None:
                                features.append([
                                    float(obj_val or 0), float(n_selected) / 20.0,
                                    float(feasibility), 0.0, 0.0, 0.0, 0.0, 0.0
                                ])

        # From whatif picks
        whatif = sources.get("whatif_picks", {}).get("data", [])
        for pick in whatif[:10]:
            if isinstance(pick, dict):
                score = pick.get("score", pick.get("quantum_score", 0))
                conf = pick.get("confidence", 0.5)
                features.append([float(score or 0), float(conf), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        if not features:
            features = [[0.5, 0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.5]]

        return features

    def run_pennylane_vqc(self, features: List[List[float]], epochs: int = 30) -> Dict[str, Any]:
        try:
            from src.research.backends.pennylane_anomaly_detector import PennyLaneAnomalyDetector
            detector = PennyLaneAnomalyDetector({
                "n_qubits": 4,
                "n_layers": 2,
                "anomaly_threshold": 0.3,
                "weights_path": str(WEIGHTS_PATH),
            })

            normalized = []
            for fv in features:
                fv = list(fv)
                while len(fv) < 8:
                    fv.append(0.0)
                normalized.append(fv[:8])

            if hasattr(detector, "train"):
                result = detector.train(normalized, epochs=epochs, learning_rate=0.01)
            elif hasattr(detector, "fit"):
                result = detector.fit(normalized)
            else:
                scores = []
                for fv in normalized[:20]:
                    try:
                        score_result = detector.score(fv)
                        scores.append(score_result)
                    except Exception:
                        scores.append({"score": 0.5})
                result = {"status": "scored", "n_scored": len(scores), "scores": scores[:10]}

            if hasattr(detector, "save_weights"):
                detector.save_weights(str(WEIGHTS_PATH))

            return {"status": "success", "backend": "pennylane_vqc", "result": result or {}, "n_features": len(normalized)}

        except ImportError as e:
            return {"status": "import_error", "error": str(e)}
        except Exception as e:
            logger.warning("PennyLane VQC training error: %s", e)
            return {"status": "error", "error": str(e)}

    def run_qiskit_qaoa(self, features: List[List[float]]) -> Dict[str, Any]:
        try:
            from src.research.backends.qiskit_portfolio_optimizer import QiskitPortfolioOptimizer

            candidates = []
            for i, fv in enumerate(features[:12]):
                candidates.append({
                    "ticker": "CAND_{:03d}".format(i),
                    "expected_return": fv[0] if len(fv) > 0 else 0.05,
                    "volatility": fv[2] if len(fv) > 2 else 0.2,
                    "weight": fv[3] if len(fv) > 3 else 0.0,
                    "score": fv[0] if len(fv) > 0 else 0.5,
                    "features": fv,
                })

            request = {
                "candidates": candidates,
                "max_positions": min(5, len(candidates)),
                "risk_budget": 0.15,
                "regime": "research",
            }

            optimizer = QiskitPortfolioOptimizer()
            if hasattr(optimizer, "optimize"):
                result = optimizer.optimize(request)
            elif hasattr(optimizer, "run"):
                result = optimizer.run(request)
            else:
                result = {"status": "no_method", "error": "QiskitPortfolioOptimizer has no optimize/run method"}

            return {"status": "success", "backend": "qiskit_qaoa", "result": result or {}, "n_candidates": len(candidates)}

        except ImportError as e:
            return {"status": "import_error", "error": str(e)}
        except Exception as e:
            logger.warning("Qiskit QAOA error: %s", e)
            return {"status": "error", "error": str(e)}

    def run_classical_baseline(self, features: List[List[float]]) -> Dict[str, Any]:
        try:
            if not features:
                return {"status": "no_data", "score": 0.0}
            sorted_features = sorted(enumerate(features), key=lambda x: -x[1][0])
            selected = sorted_features[:min(5, len(sorted_features))]
            avg_score = sum(f[0] for _, f in selected) / max(len(selected), 1)
            return {
                "status": "success",
                "backend": "classical_greedy",
                "avg_score": avg_score,
                "selected_indices": [i for i, _ in selected],
                "n_features": len(features),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def training_cycle(self, ingested_data: Dict, heavy: bool = False) -> Dict[str, Any]:
        self.cycle_count += 1
        cycle_report: Dict[str, Any] = {
            "cycle": self.cycle_count,
            "timestamp": utc_now(),
            "heavy_mode": heavy,
            "steps": {},
        }

        # a) Extract features
        features = self.extract_features(ingested_data)
        cycle_report["steps"]["feature_extraction"] = {"n_features": len(features)}

        # b-c) PennyLane VQC
        epochs = 50 if heavy else 15
        vqc_result = self.run_pennylane_vqc(features, epochs=epochs)
        cycle_report["steps"]["pennylane_vqc"] = vqc_result

        # d) Qiskit QAOA (heavy mode only)
        if heavy:
            qaoa_result = self.run_qiskit_qaoa(features)
            cycle_report["steps"]["qiskit_qaoa"] = qaoa_result
        else:
            cycle_report["steps"]["qiskit_qaoa"] = {"status": "skipped_lightweight"}

        # e) Classical baseline
        classical_result = self.run_classical_baseline(features)
        cycle_report["steps"]["classical_baseline"] = classical_result

        # f) Score approaches
        quantum_score = self._extract_score(vqc_result)
        classical_score = classical_result.get("avg_score", 0.0)

        cycle_report["scores"] = {
            "quantum": quantum_score,
            "classical": classical_score,
            "quantum_weight_before": self.quantum_weight,
        }

        # g-h) Adjust quantum weight
        if quantum_score > 0 and classical_score > 0:
            ratio = quantum_score / max(classical_score, 0.001)
            if ratio > 1.05:
                self.quantum_weight = min(0.9, self.quantum_weight + 0.02)
                cycle_report["weight_adjustment"] = "increased_quantum"
            elif ratio < 0.95:
                self.quantum_weight = max(0.1, self.quantum_weight - 0.02)
                cycle_report["weight_adjustment"] = "decreased_quantum"
            else:
                cycle_report["weight_adjustment"] = "no_change"

        cycle_report["scores"]["quantum_weight_after"] = self.quantum_weight

        # j) Save learning metrics
        self.training_history.append({
            "cycle": self.cycle_count,
            "timestamp": utc_now(),
            "quantum_score": quantum_score,
            "classical_score": classical_score,
            "quantum_weight": self.quantum_weight,
        })

        metric_path = LEARNING_METRICS_DIR / "cycle_{:06d}.json".format(self.cycle_count)
        safe_json_write(metric_path, cycle_report)

        # k) Every 100 cycles, summary
        if self.cycle_count % 100 == 0:
            self._generate_summary()

        self._save_state()
        return cycle_report

    def _extract_score(self, result: Dict) -> float:
        if result.get("status") != "success":
            return 0.0
        inner = result.get("result", {})
        if isinstance(inner, dict):
            obj_val = inner.get("objective_value")
            if obj_val is not None:
                return float(obj_val)
            scores = inner.get("scores", [])
            if scores:
                score_vals = []
                for s in scores[:10]:
                    if isinstance(s, dict):
                        score_vals.append(s.get("score", 0))
                    else:
                        try:
                            score_vals.append(float(s))
                        except (TypeError, ValueError):
                            score_vals.append(0.0)
                return sum(score_vals) / max(len(score_vals), 1)
        return 0.5 * self.quantum_weight

    def _generate_summary(self):
        recent = self.training_history[-100:]
        if not recent:
            return
        avg_quantum = sum(r.get("quantum_score", 0) for r in recent) / len(recent)
        avg_classical = sum(r.get("classical_score", 0) for r in recent) / len(recent)
        weight_trend = [r.get("quantum_weight", 0.5) for r in recent]
        summary = {
            "generated_at": utc_now(),
            "cycles_covered": "{}-{}".format(recent[0].get("cycle", 0), recent[-1].get("cycle", 0)),
            "avg_quantum_score": avg_quantum,
            "avg_classical_score": avg_classical,
            "quantum_weight_start": weight_trend[0] if weight_trend else 0.5,
            "quantum_weight_end": weight_trend[-1] if weight_trend else 0.5,
            "quantum_outperformed_pct": sum(1 for r in recent if r.get("quantum_score", 0) > r.get("classical_score", 0)) / len(recent) * 100,
            "insight": "quantum_improving" if weight_trend and weight_trend[-1] > weight_trend[0] else "classical_preferred",
        }
        safe_json_write(LEARNING_METRICS_DIR / "summary_cycle_{}.json".format(self.cycle_count), summary)
        logger.info("Learning summary for cycles %s: quantum_weight=%.3f", summary["cycles_covered"], summary["quantum_weight_end"])


# ===================================================================
# 4. SELF-IMPROVEMENT MECHANISM
# ===================================================================
class SelfImprover:

    def __init__(self):
        self.prediction_log: List[Dict] = []
        self.signal_scores: Dict[str, List[float]] = defaultdict(list)
        self.last_daily_report: Optional[str] = None
        self._load_state()

    def _load_state(self):
        state_path = DATA_DIR / "quantum_feed" / "self_improver_state.json"
        state = safe_json_read(state_path)
        if state:
            self.prediction_log = state.get("prediction_log", [])[-1000:]
            self.signal_scores = defaultdict(list, state.get("signal_scores", {}))
            self.last_daily_report = state.get("last_daily_report")

    def _save_state(self):
        state_path = DATA_DIR / "quantum_feed" / "self_improver_state.json"
        trimmed_scores = {k: v[-200:] for k, v in self.signal_scores.items()}
        safe_json_write(state_path, {
            "prediction_log": self.prediction_log[-1000:],
            "signal_scores": trimmed_scores,
            "last_daily_report": self.last_daily_report,
            "updated_at": utc_now(),
        })

    def record_prediction(self, source: str, predicted: float, actual: Optional[float], metadata: Dict = None):
        self.prediction_log.append({
            "timestamp": utc_now(),
            "source": source,
            "predicted": predicted,
            "actual": actual,
            "error": abs(predicted - actual) if actual is not None else None,
            "metadata": metadata or {},
        })
        if actual is not None:
            accuracy = 1.0 - min(abs(predicted - actual), 1.0)
            self.signal_scores[source].append(accuracy)

    def evaluate_signals(self, ingested_data: Dict) -> Dict[str, float]:
        scores = {}
        for source, accuracy_list in self.signal_scores.items():
            recent = accuracy_list[-50:]
            if recent:
                scores[source] = sum(recent) / len(recent)
        return scores

    def check_confidence_calibration(self) -> Dict[str, Any]:
        bins: Dict[str, List[float]] = defaultdict(list)
        for pred in self.prediction_log[-500:]:
            if pred.get("actual") is not None:
                conf = pred.get("predicted", 0.5)
                won = pred["actual"] > 0
                bucket = round(conf * 10) / 10
                bins[str(bucket)].append(1.0 if won else 0.0)

        calibration = {}
        for bucket, outcomes in bins.items():
            actual_win_rate = sum(outcomes) / len(outcomes)
            calibration[bucket] = {
                "expected_win_rate": float(bucket),
                "actual_win_rate": actual_win_rate,
                "n_samples": len(outcomes),
                "calibrated": abs(float(bucket) - actual_win_rate) < 0.15,
            }
        return calibration

    def generate_daily_report(self, ingested_data: Dict, scenario_results: Dict, trainer: QuantumTrainer) -> Optional[Dict]:
        today = et_now().strftime("%Y-%m-%d")
        if self.last_daily_report == today:
            return None

        signal_scores = self.evaluate_signals(ingested_data)
        sorted_signals = sorted(signal_scores.items(), key=lambda x: -x[1])
        calibration = self.check_confidence_calibration()

        strategy_rankings = scenario_results.get("best_strategies", {}) if scenario_results else {}

        recent_training = trainer.training_history[-24:] if trainer.training_history else []
        avg_quantum = sum(r.get("quantum_score", 0) for r in recent_training) / max(len(recent_training), 1)
        avg_classical = sum(r.get("classical_score", 0) for r in recent_training) / max(len(recent_training), 1)

        report = {
            "date": today,
            "generated_at": utc_now(),
            "training_cycles_today": len(recent_training),
            "top_5_predictive_signals": sorted_signals[:5],
            "bottom_5_signals": sorted_signals[-5:] if len(sorted_signals) > 5 else sorted_signals,
            "strategy_parameter_adjustments": {
                "quantum_weight": trainer.quantum_weight,
                "recommended_direction": "increase_quantum" if avg_quantum > avg_classical else "maintain_classical",
            },
            "confidence_calibration": calibration,
            "quantum_vs_classical": {
                "avg_quantum_score": avg_quantum,
                "avg_classical_score": avg_classical,
                "quantum_advantage": avg_quantum - avg_classical,
            },
            "scenario_insights": {
                "strategy_rankings": strategy_rankings,
            },
            "memory_usage_mb": memory_mb(),
        }

        safe_json_write(SELF_IMPROVEMENT_DIR / "daily_{}.json".format(today), report)
        self.last_daily_report = today
        self._save_state()
        logger.info("Daily self-improvement report generated for %s", today)
        return report


# ===================================================================
# 5. FEEDBACK INTO TRADE ENGINE
# ===================================================================
class TradeFeedback:

    def update_all(self, trainer: QuantumTrainer, scenario_sim: ScenarioSimulator,
                   ingested_data: Dict, scenario_results: Dict) -> Dict[str, bool]:
        results = {}
        results["regime_calibration"] = self._update_regime_calibration(ingested_data, trainer)
        results["latest_signal"] = self._update_latest_signal(trainer, scenario_results)
        results["strategy_recommendations"] = self._generate_strategy_recs(
            trainer, scenario_sim, ingested_data, scenario_results
        )
        results["quantum_reservoir_regime"] = self._run_quantum_reservoir(ingested_data)
        return results

    def _run_quantum_reservoir(self, ingested_data: Dict) -> bool:
        """Run quantum reservoir regime detection and write prediction."""
        try:
            from src.research.quantum_reservoir import run_quantum_reservoir_full
            # Extract market data from ingested sources
            sources = ingested_data.get("sources", {})
            signal_data = sources.get("latest_signal", {}).get("data", {})
            market_data = signal_data.get("market_data")
            # Daily: train if model missing, then predict
            result = run_quantum_reservoir_full(market_data=market_data)
            if result.get("status") == "success":
                logger.info("Quantum reservoir regime: %s (conf=%.3f)",
                            result.get("quantum_prediction", {}).get("regime", "?"),
                            result.get("quantum_prediction", {}).get("confidence", 0))
                return True
            else:
                logger.warning("Quantum reservoir failed: %s", result.get("error", "unknown"))
                return False
        except Exception as e:
            logger.warning("Quantum reservoir unavailable: %s", e)
            return False

    def _update_regime_calibration(self, ingested_data: Dict, trainer: QuantumTrainer) -> bool:
        try:
            existing = safe_json_read(REGIME_CAL_PATH)
            if not existing:
                existing = []
            if isinstance(existing, list):
                meta_entry = {
                    "event": "quantum_learner_calibration",
                    "severity": trainer.quantum_weight,
                    "regime_signature_keys": ["quantum_weight_adjustment"],
                    "updated_at": utc_now(),
                    "cycle": trainer.cycle_count,
                }
                existing = [e for e in existing if not (isinstance(e, dict) and e.get("event") == "quantum_learner_calibration")]
                existing.append(meta_entry)
                return safe_json_write(REGIME_CAL_PATH, existing)
            return False
        except Exception as e:
            logger.warning("Failed to update regime calibration: %s", e)
            return False

    def _update_latest_signal(self, trainer: QuantumTrainer, scenario_results: Dict) -> bool:
        try:
            sig = safe_json_read(LATEST_SIGNAL_PATH)
            if not sig:
                return False
            sig["quantum_learning"] = {
                "quantum_weight": trainer.quantum_weight,
                "training_cycle": trainer.cycle_count,
                "updated_at": utc_now(),
            }
            if scenario_results and scenario_results.get("scenarios"):
                worst = {}
                for name, data in scenario_results.get("scenarios", {}).items():
                    summary = data.get("summary", {})
                    worst[name] = summary.get("p05_terminal_return", 0)
                sig["quantum_learning"]["worst_case_scenarios"] = dict(
                    sorted(worst.items(), key=lambda x: x[1])[:5]
                )
            return safe_json_write(LATEST_SIGNAL_PATH, sig)
        except Exception as e:
            logger.warning("Failed to update latest signal: %s", e)
            return False

    def _generate_strategy_recs(self, trainer: QuantumTrainer, scenario_sim: ScenarioSimulator,
                                 ingested_data: Dict, scenario_results: Dict) -> bool:
        try:
            sources = ingested_data.get("sources", {})
            sig = sources.get("latest_signal", {}).get("data", {})
            buckets = sig.get("bucket_scores", {})
            sorted_buckets = sorted(buckets.items(), key=lambda x: -x[1]) if buckets else []

            sector_tickers = {
                "OIL_SUPPLY": ["XLE", "USO", "OXY"],
                "ENERGY_CASCADE": ["XLE", "FANG", "DVN"],
                "DEFENSE": ["LMT", "NOC", "RTX", "GD", "BA"],
                "SAFE_HAVEN": ["GLD", "TLT", "UUP"],
                "GEOPOLITICAL": ["EFA", "EEM", "VWO"],
                "SHIPPING": ["ZIM", "SBLK", "EGLE"],
                "AVIATION": ["DAL", "UAL", "AAL"],
                "FOOD_CHAIN": ["ADM", "BG", "MOS"],
                "INFLATION": ["TIP", "VTIP", "SCHP"],
                "TECH_SELLOFF": ["QQQ", "XLK", "SOXX"],
            }

            strategy_rankings = scenario_sim.get_strategy_rankings()
            best_strategy = max(strategy_rankings, key=strategy_rankings.get) if strategy_rankings else "balanced_defensive"

            recs = []
            for bucket_name, score in sorted_buckets[:5]:
                tickers = sector_tickers.get(bucket_name, ["SPY"])
                confidence = min(score / 10.0, 0.95) * trainer.quantum_weight + (1 - trainer.quantum_weight) * 0.5

                recent = trainer.training_history[-50:]
                win_rate = sum(1 for r in recent if r.get("quantum_score", 0) > 0.5) / max(len(recent), 1)

                recs.append({
                    "sector": bucket_name,
                    "tickers": tickers,
                    "direction": "long" if score >= 7 else "neutral",
                    "confidence": round(confidence, 3),
                    "disruption_score": score,
                    "historical_win_rate": round(win_rate, 3),
                    "recommended_strategy": best_strategy,
                    "scenario_summary": "Score {}/10, quantum_weight={:.2f}".format(score, trainer.quantum_weight),
                })

            output = {
                "generated_at": utc_now(),
                "training_cycle": trainer.cycle_count,
                "quantum_weight": trainer.quantum_weight,
                "best_overall_strategy": best_strategy,
                "strategy_rankings": strategy_rankings,
                "top_5_recommendations": recs[:5],
                "not_for_direct_execution": True,
                "research_artifact_only": True,
            }

            return safe_json_write(STRATEGY_RECS_PATH, output)
        except Exception as e:
            logger.warning("Failed to generate strategy recommendations: %s", e)
            return False


# ===================================================================
# 5b. QUANTUM OUTPUT INTEGRATION
# ===================================================================
def _integrate_quantum_outputs(scenario_results: Dict[str, Any], trainer) -> None:
    """Read quantum MC results and expanded portfolio weights, feed into scoring."""
    qmc_path = QUANTUM_FEED / "quantum_mc_results.json"
    portfolio_path = QUANTUM_FEED / "optimal_portfolio.json"

    # --- Scenario stress test results -> regime scoring confidence ---
    if scenario_results and scenario_results.get("scenarios"):
        worst_scenarios = {}
        for name, data in scenario_results.get("scenarios", {}).items():
            summary = data.get("summary", {})
            worst_scenarios[name] = summary.get("p05_terminal_return", 0.0)

        # Adjust regime confidence based on tail risk severity
        avg_tail = sum(worst_scenarios.values()) / max(len(worst_scenarios), 1)
        tail_severity = max(0.0, min(1.0, abs(avg_tail) * 10))  # 0-1 scale

        cal_path = QUANTUM_FEED / "regime_tail_severity.json"  # separate from list-format audit log
        try:
            _loaded = json.loads(cal_path.read_text()) if cal_path.exists() else {}
            cal = _loaded if isinstance(_loaded, dict) else {}
        except Exception:
            cal = {}
        cal["scenario_tail_severity"] = round(tail_severity, 4)
        cal["scenario_confidence_adjustment"] = round(1.0 - tail_severity * 0.3, 4)
        cal["scenarios_evaluated"] = len(worst_scenarios)
        cal["last_scenario_update"] = utc_now()
        safe_json_write(cal_path, cal)
        logger.info("  Regime confidence adjusted by scenario tail severity: %.4f", tail_severity)

    # --- Quantum MC results -> position sizing refinement ---
    if qmc_path.exists():
        try:
            qmc = json.loads(qmc_path.read_text())
            speedup = qmc.get("aggregate", {}).get("avg_speedup_factor", 1.0)
            accuracy = qmc.get("aggregate", {}).get("avg_accuracy_delta", 0.0)
            # If quantum MC shows good accuracy, slightly boost quantum weight
            if accuracy < 0.02 and speedup > 1.0:
                trainer.quantum_weight = min(0.8, trainer.quantum_weight + 0.005)
                logger.info("  Quantum MC integration: speedup=%.2f, accuracy_delta=%.4f -> weight bumped to %.3f",
                            speedup, accuracy, trainer.quantum_weight)
        except Exception as e:
            logger.debug("Could not read quantum MC results: %s", e)

    # --- Expanded portfolio weights -> trade idea ranking boost ---
    if portfolio_path.exists():
        try:
            portfolio = json.loads(portfolio_path.read_text())
            weights = portfolio.get("optimal_weights", {})
            if weights:
                # Write a ranking boost file that the trade engine can consume
                boost_path = QUANTUM_FEED / "portfolio_ranking_boost.json"
                boost = {
                    "timestamp": utc_now(),
                    "source": "expanded_portfolio_optimizer",
                    "regime": portfolio.get("regime", "NORMAL"),
                    "asset_boosts": {
                        sym: round(wt * 2.0, 4)  # Convert weight to boost factor (0-0.4)
                        for sym, wt in weights.items()
                    },
                    "portfolio_sharpe": portfolio.get("portfolio_metrics", {}).get("sharpe_ratio", 0),
                    "not_for_direct_execution": True,
                }
                safe_json_write(boost_path, boost)
                logger.info("  Portfolio ranking boost written for %d assets", len(weights))
        except Exception as e:
            logger.debug("Could not read portfolio weights: %s", e)


# ===================================================================
# 6. MAIN DAEMON LOOP
# ===================================================================
def main():
    logger.info("=" * 70)
    logger.info("Quantum Continuous Learning System starting")
    logger.info("Repo root: %s", REPO_ROOT)
    logger.info("Heavy compute window: 4:30 PM - 8:30 AM ET + weekends")
    logger.info("Lightweight cycle: ~5 min | Heavy cycle: ~15-30 min")
    logger.info("=" * 70)

    for d in [LEARNING_METRICS_DIR, SELF_IMPROVEMENT_DIR, QUANTUM_FEED, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    ingestion = DataIngestion()
    simulator = ScenarioSimulator()
    trainer = QuantumTrainer()
    improver = SelfImprover()
    feedback = TradeFeedback()

    logger.info("Resuming from cycle %d, quantum_weight=%.3f", trainer.cycle_count, trainer.quantum_weight)

    last_scenario_results: Dict[str, Any] = {}

    while not _SHUTDOWN:
        cycle_start = time.time()
        heavy = is_heavy_compute_window()
        mode = "HEAVY" if heavy else "LIGHTWEIGHT"

        try:
            logger.info("--- Cycle %d [%s] ---", trainer.cycle_count + 1, mode)

            if not check_memory_guard():
                logger.warning("Memory pressure -- running GC and sleeping 60s")
                gc.collect()
                time.sleep(60)
                continue

            # 1. DATA INGESTION
            logger.info("[1/5] Ingesting data from all sources...")
            ingested = ingestion.collect_all()
            n_ok = ingested.get("sources_collected", 0)
            n_fail = ingested.get("sources_failed", 0)
            logger.info("  Collected %d sources OK, %d failed", n_ok, n_fail)

            # 2. SCENARIO SIMULATION
            if heavy:
                logger.info("[2/5] Running heavy Monte Carlo scenarios (10000 paths)...")
                last_scenario_results = simulator.run_scenario_batch(n_paths=10000, lightweight=False)
            else:
                logger.info("[2/5] Running lightweight scenario batch (500 paths)...")
                last_scenario_results = simulator.run_scenario_batch(n_paths=500, lightweight=True)
            n_scenarios = len(last_scenario_results.get("scenarios", {}))
            logger.info("  Simulated %d scenarios", n_scenarios)

            if _SHUTDOWN:
                break

            # 2b. QUANTUM AMPLITUDE ESTIMATION (heavy mode only)
            if heavy and not _SHUTDOWN:
                try:
                    from src.research.quantum_monte_carlo import run_quantum_monte_carlo
                    logger.info("[2b/5] Running Quantum Amplitude Estimation MC...")
                    qmc_result = run_quantum_monte_carlo(
                        n_classical_paths=10000,
                        n_state_qubits=4,
                        n_eval_qubits=6,
                    )
                    n_qmc = len(qmc_result.get("scenarios", {}))
                    agg = qmc_result.get("aggregate", {})
                    logger.info("  QAE MC: %d scenarios, avg_speedup=%.2f, avg_accuracy_delta=%.6f",
                                n_qmc, agg.get("avg_speedup_factor", 0), agg.get("avg_accuracy_delta", 0))
                except Exception as qmc_err:
                    logger.warning("Quantum MC estimation (non-fatal): %s", qmc_err)

            # 3. RECURSIVE TRAINING
            logger.info("[3/5] Training cycle (heavy=%s)...", heavy)
            cycle_report = trainer.training_cycle(ingested, heavy=heavy)
            q_score = cycle_report.get("scores", {}).get("quantum", 0)
            c_score = cycle_report.get("scores", {}).get("classical", 0)
            logger.info("  Quantum=%.4f Classical=%.4f Weight=%.3f", q_score, c_score, trainer.quantum_weight)

            # Record predictions for self-improvement
            for source_name, source_data in ingested.get("sources", {}).items():
                if source_data.get("status") == "ok" and source_data.get("count", 0) > 0:
                    improver.record_prediction(
                        source=source_name,
                        predicted=q_score,
                        actual=c_score if c_score > 0 else None,
                        metadata={"cycle": trainer.cycle_count},
                    )

            if _SHUTDOWN:
                break

            # 4. SELF-IMPROVEMENT
            logger.info("[4/5] Self-improvement evaluation...")
            daily_report = improver.generate_daily_report(ingested, last_scenario_results, trainer)
            if daily_report:
                logger.info("  Daily improvement report generated")
            else:
                logger.info("  Daily report already generated today")

            # 5. FEEDBACK INTO TRADE ENGINE
            logger.info("[5/5] Updating trade engine configs...")
            fb_results = feedback.update_all(trainer, simulator, ingested, last_scenario_results)
            for key, ok in fb_results.items():
                logger.info("  %s: %s", key, "OK" if ok else "FAILED")

            # 5b. QUANTUM MC + EXPANDED PORTFOLIO INTEGRATION
            try:
                _integrate_quantum_outputs(last_scenario_results, trainer)
            except Exception as qmc_err:
                logger.warning("Quantum output integration (non-fatal): %s", qmc_err)

            elapsed = time.time() - cycle_start
            logger.info("Cycle %d complete in %.1fs | Memory: %.1f MB",
                        trainer.cycle_count, elapsed, memory_mb())

            # Sleep between cycles
            if heavy:
                sleep_time = max(60, 900 - elapsed)
            else:
                sleep_time = max(30, 300 - elapsed)

            logger.info("Sleeping %.0fs before next cycle...", sleep_time)
            sleep_end = time.time() + sleep_time
            while time.time() < sleep_end and not _SHUTDOWN:
                time.sleep(5)

        except Exception as e:
            logger.error("Cycle error (non-fatal): %s\n%s", e, traceback.format_exc())
            try:
                trainer._save_state()
                improver._save_state()
            except Exception:
                pass
            for _ in range(12):
                if _SHUTDOWN:
                    break
                time.sleep(5)

    logger.info("Shutting down -- saving state...")
    try:
        trainer._save_state()
        improver._save_state()
        simulator._save_history()
    except Exception as e:
        logger.error("Error saving state on shutdown: %s", e)
    logger.info("Quantum Continuous Learning System stopped.")


if __name__ == "__main__":
    main()
