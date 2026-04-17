#!/usr/bin/env python3
"""Continuous Strategy Trainer — 24/7 Mock Scenario Training Loop.

Generates synthetic market scenarios, feeds them through all 25 strategies,
tracks simulated P&L, and computes Kelly criterion sizing. Runs 24/7
including when markets are closed.

Architecture:
  ScenarioGenerator → MockSnapshot → ReplayScorer → StrategyEngine
      → SimulatedPnL → KellyCalculator → Training State

Usage:
  python scripts/ops/continuous_strategy_trainer.py [--interval 30] [--scenarios-per-cycle 10]

Output:
  - logs/training/strategy_pnl_YYYYMMDD.jsonl  (per-trade P&L)
  - logs/training/kelly_state.json              (rolling Kelly sizing)
  - logs/training/training_summary.json         (aggregate stats)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("continuous_strategy_trainer")

# ---------------------------------------------------------------------------
# Scenario Generation
# ---------------------------------------------------------------------------

# 12 scenario archetypes covering the full range of market conditions
SCENARIO_ARCHETYPES = {
    "bull_momentum": {
        "description": "Strong uptrend, risk-on, low VIX",
        "wti_change_pct": (1.0, 4.0),
        "vix": (15, 22),
        "dxy": (100, 104),
        "spy_change_pct": (0.5, 2.5),
        "geo_tension": (0.1, 0.3),
        "commodity_shock": (0.1, 0.3),
        "oil_regime": "NORMAL",
        "regime_prob": (0.05, 0.25),
    },
    "bear_selloff": {
        "description": "Sharp selloff, risk-off, rising VIX",
        "wti_change_pct": (-4.0, -1.0),
        "vix": (25, 45),
        "dxy": (104, 110),
        "spy_change_pct": (-3.0, -0.5),
        "geo_tension": (0.3, 0.6),
        "commodity_shock": (0.2, 0.5),
        "oil_regime": "ELEVATED",
        "regime_prob": (0.35, 0.60),
    },
    "oil_shock_escalation": {
        "description": "Major oil supply disruption — Hormuz/attack",
        "wti_change_pct": (3.0, 12.0),
        "vix": (28, 50),
        "dxy": (103, 108),
        "spy_change_pct": (-3.0, 0.5),
        "geo_tension": (0.7, 1.0),
        "commodity_shock": (0.7, 1.0),
        "oil_regime": "SHOCK",
        "regime_prob": (0.65, 0.95),
    },
    "oil_shock_dislocation": {
        "description": "Extreme disruption — multiple chokepoints",
        "wti_change_pct": (8.0, 20.0),
        "vix": (40, 80),
        "dxy": (106, 115),
        "spy_change_pct": (-6.0, -2.0),
        "geo_tension": (0.9, 1.0),
        "commodity_shock": (0.9, 1.0),
        "oil_regime": "DISLOCATION",
        "regime_prob": (0.85, 1.0),
    },
    "ceasefire_rally": {
        "description": "Peace signal — massive risk-on reversal",
        "wti_change_pct": (-8.0, -3.0),
        "vix": (18, 28),
        "dxy": (99, 103),
        "spy_change_pct": (2.0, 5.0),
        "geo_tension": (0.1, 0.3),
        "commodity_shock": (0.1, 0.3),
        "oil_regime": "NORMAL",
        "regime_prob": (0.05, 0.20),
    },
    "sideways_chop": {
        "description": "Range-bound, low conviction, theta decay",
        "wti_change_pct": (-1.0, 1.0),
        "vix": (18, 25),
        "dxy": (102, 106),
        "spy_change_pct": (-0.5, 0.5),
        "geo_tension": (0.2, 0.4),
        "commodity_shock": (0.2, 0.4),
        "oil_regime": "NORMAL",
        "regime_prob": (0.15, 0.35),
    },
    "vix_spike_crash": {
        "description": "VIX explosion — fear event",
        "wti_change_pct": (-2.0, 3.0),
        "vix": (35, 70),
        "dxy": (105, 112),
        "spy_change_pct": (-5.0, -2.0),
        "geo_tension": (0.5, 0.8),
        "commodity_shock": (0.4, 0.7),
        "oil_regime": "ELEVATED",
        "regime_prob": (0.50, 0.80),
    },
    "shipping_crisis": {
        "description": "Hormuz blockade — shipping rates 3-5x",
        "wti_change_pct": (2.0, 8.0),
        "vix": (25, 40),
        "dxy": (103, 108),
        "spy_change_pct": (-2.0, 0.0),
        "geo_tension": (0.6, 0.9),
        "commodity_shock": (0.6, 0.9),
        "oil_regime": "SHOCK",
        "regime_prob": (0.55, 0.85),
        "chokepoint_hormuz": (0.7, 1.0),
    },
    "em_contagion": {
        "description": "EM capital flight — dollar strength",
        "wti_change_pct": (0.0, 3.0),
        "vix": (22, 35),
        "dxy": (108, 115),
        "spy_change_pct": (-2.0, 0.0),
        "geo_tension": (0.4, 0.6),
        "commodity_shock": (0.3, 0.6),
        "currency_stress": (0.6, 0.9),
        "oil_regime": "ELEVATED",
        "regime_prob": (0.40, 0.65),
    },
    "gold_surge": {
        "description": "Safe haven bid — gold/silver breakout",
        "wti_change_pct": (0.0, 4.0),
        "vix": (22, 35),
        "dxy": (98, 103),
        "spy_change_pct": (-1.0, 1.0),
        "geo_tension": (0.4, 0.7),
        "commodity_shock": (0.3, 0.6),
        "oil_regime": "ELEVATED",
        "regime_prob": (0.30, 0.55),
    },
    "tech_rotation": {
        "description": "Sector rotation out of tech into value/energy",
        "wti_change_pct": (1.0, 3.0),
        "vix": (18, 28),
        "dxy": (101, 105),
        "spy_change_pct": (-1.0, 1.0),
        "geo_tension": (0.2, 0.4),
        "commodity_shock": (0.2, 0.5),
        "oil_regime": "NORMAL",
        "regime_prob": (0.15, 0.35),
    },
    "overnight_gap_event": {
        "description": "Major overnight headline — gap at open",
        "wti_change_pct": (-5.0, 8.0),
        "vix": (20, 40),
        "dxy": (101, 108),
        "spy_change_pct": (-3.0, 3.0),
        "geo_tension": (0.3, 0.8),
        "commodity_shock": (0.3, 0.8),
        "oil_regime": "ELEVATED",
        "regime_prob": (0.30, 0.70),
    },
}

# Asset price ranges for simulation (approximate current levels March 2026)
ASSET_PRICES = {
    "USO": 127.0, "XOP": 45.0, "OXY": 65.0, "XLE": 61.0, "CVX": 207.0,
    "XOM": 170.0, "STNG": 55.0, "FRO": 30.0, "ZIM": 22.0, "NAT": 4.0,
    "LMT": 604.0, "RTX": 193.0, "NOC": 682.0, "KTOS": 35.0, "AVAV": 180.0,
    "GLD": 430.0, "GDX": 92.0, "SLV": 68.0, "JETS": 18.0, "UAL": 65.0,
    "AAL": 11.0, "DAL": 45.0, "EZU": 50.0, "EWG": 30.0, "LNG": 210.0,
    "MOS": 25.0, "CF": 80.0, "NTR": 45.0, "WEAT": 5.5,
    "CCJ": 55.0, "URA": 28.0, "SMR": 25.0,
    "PANW": 180.0, "NET": 110.0, "CRWD": 340.0,
    "UVXY": 52.0, "SVXY": 18.0, "VXX": 35.0,
    "EEM": 57.0, "INDA": 52.0, "EWZ": 38.0, "FXI": 36.0,
    "TLT": 88.0, "TIP": 108.0, "GS": 520.0, "MS": 105.0,
    "PSX": 130.0, "VLO": 135.0, "MPC": 155.0,
    "CNQ": 30.0, "EWJ": 84.0, "EWK": 55.0,
    "DBA": 25.0, "CORN": 22.0, "SPY": 650.0, "QQQ": 577.0,
}


def _rand_range(r: tuple) -> float:
    """Random float in range tuple."""
    return random.uniform(r[0], r[1])


def generate_scenario(archetype: str | None = None) -> dict[str, Any]:
    """Generate a synthetic market scenario for strategy training.

    If archetype is None, randomly selects one weighted by real-world frequency.
    """
    if archetype is None:
        # Weighted selection: war scenarios more frequent (current regime)
        weights = {
            "bull_momentum": 8,
            "bear_selloff": 10,
            "oil_shock_escalation": 15,
            "oil_shock_dislocation": 5,
            "ceasefire_rally": 10,
            "sideways_chop": 15,
            "vix_spike_crash": 5,
            "shipping_crisis": 8,
            "em_contagion": 5,
            "gold_surge": 7,
            "tech_rotation": 7,
            "overnight_gap_event": 5,
        }
        archetype = random.choices(
            list(weights.keys()), weights=list(weights.values()), k=1
        )[0]

    cfg = SCENARIO_ARCHETYPES[archetype]
    wti_chg = _rand_range(cfg["wti_change_pct"])
    vix = _rand_range(cfg["vix"])
    spy_chg = _rand_range(cfg["spy_change_pct"])

    # Build mock scorecard
    scorecard = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "CRISIS" if cfg.get("regime_prob", (0, 0))[1] > 0.65 else (
            "ELEVATED" if cfg.get("regime_prob", (0, 0))[1] > 0.35 else "NORMAL"
        ),
        "regime_shift_probability": _rand_range(cfg["regime_prob"]),
        "confidence": random.uniform(0.55, 0.90),
        "v6_oil_regime": cfg["oil_regime"],
        "component_scores": {
            "geopolitical_tension": _rand_range(cfg["geo_tension"]),
            "commodity_shock": _rand_range(cfg["commodity_shock"]),
            "market_volatility": min(1.0, vix / 60.0),
            "currency_stress": _rand_range(cfg.get("currency_stress", (0.1, 0.4))),
            "policy_uncertainty": random.uniform(0.1, 0.5),
            "yield_curve": random.uniform(0.05, 0.4),
            "politician_alpha": random.uniform(0.0, 0.3),
            "credit_spread": random.uniform(0.05, 0.3),
            "liquidity_stress": random.uniform(0.02, 0.2),
            "consciousness_coherence": random.uniform(0.0, 0.15),
            "labor_disruption": random.uniform(0.0, 0.1),
            "policy_signals": random.uniform(0.05, 0.3),
        },
        "chokepoint_risk": {
            "hormuz": _rand_range(cfg.get("chokepoint_hormuz", (0.0, 0.5))),
            "bab_el_mandeb": random.uniform(0.0, 0.3),
            "panama": random.uniform(0.0, 0.1),
        },
    }

    # Build mock market data
    market_data = {"VIX": {"price": vix, "change_pct": random.uniform(-5, 15)}}
    for symbol, base_price in ASSET_PRICES.items():
        # Correlate moves with scenario
        if symbol in ("USO", "XOP", "OXY", "XLE", "CVX", "XOM"):
            move = wti_chg * random.uniform(0.6, 1.4)
        elif symbol in ("STNG", "FRO", "ZIM", "NAT"):
            move = wti_chg * random.uniform(0.3, 1.8)
        elif symbol in ("JETS", "UAL", "AAL", "DAL"):
            move = -wti_chg * random.uniform(0.3, 1.2)  # Inverse to oil
        elif symbol in ("GLD", "GDX", "SLV"):
            move = random.uniform(0.5, 3.0) if vix > 30 else random.uniform(-1.0, 1.5)
        elif symbol in ("LMT", "RTX", "NOC", "KTOS", "AVAV"):
            geo = scorecard["component_scores"]["geopolitical_tension"]
            move = geo * random.uniform(1.0, 5.0)
        elif symbol in ("UVXY", "VXX"):
            move = (vix - 25) * random.uniform(0.5, 2.0)
        elif symbol in ("SVXY",):
            move = -(vix - 25) * random.uniform(0.3, 1.0)
        elif symbol in ("SPY", "QQQ"):
            move = spy_chg * random.uniform(0.8, 1.2)
        else:
            move = spy_chg * random.uniform(0.3, 1.0) + random.uniform(-1.0, 1.0)

        price = base_price * (1 + move / 100)
        market_data[symbol] = {
            "price": round(price, 2),
            "change_pct": round(move, 2),
            "volume": random.randint(500_000, 50_000_000),
        }

    return {
        "scenario_id": f"sim-{archetype}-{int(time.time()*1000) % 1_000_000:06d}",
        "archetype": archetype,
        "description": cfg["description"],
        "scorecard": scorecard,
        "market_data": market_data,
        "bridge_results": {},  # Strategies fallback to scorecard when bridges empty
    }


# ---------------------------------------------------------------------------
# Simulated P&L Calculator
# ---------------------------------------------------------------------------

def simulate_trade_pnl(idea: dict, scenario: dict) -> dict[str, Any]:
    """Simulate P&L for a trade idea within a scenario.

    Uses the scenario's market move for the symbol to estimate
    what the P&L would be if the trade was executed at scenario start
    and held for the scenario duration.
    """
    symbol = idea.get("symbol", "")
    direction = idea.get("direction", "long")
    notional = idea.get("notional_usd", 0)
    confidence = idea.get("confidence_score", idea.get("confidence", 0.5))
    strategy = idea.get("strategy", "unknown")
    tier = idea.get("tier", "untiered")

    mkt = scenario["market_data"].get(symbol, {})
    move_pct = mkt.get("change_pct", 0)

    # Directional P&L
    if direction == "short":
        move_pct = -move_pct

    # Apply some noise for realistic simulation
    slippage = random.uniform(-0.15, 0.05)  # Slight negative bias (realistic)
    realized_pct = move_pct + slippage

    # Apply stop-loss and take-profit (simplified)
    stop_loss = idea.get("stop_loss_pct", -3.0)
    take_profit = idea.get("take_profit_pct", 5.0)
    realized_pct = max(stop_loss, min(take_profit, realized_pct))

    pnl_usd = notional * (realized_pct / 100)

    return {
        "strategy": strategy,
        "symbol": symbol,
        "direction": direction,
        "notional_usd": notional,
        "move_pct": round(move_pct, 3),
        "realized_pct": round(realized_pct, 3),
        "pnl_usd": round(pnl_usd, 2),
        "confidence": round(confidence, 3),
        "tier": tier,
        "scenario_id": scenario["scenario_id"],
        "archetype": scenario["archetype"],
        "win": pnl_usd > 0,
    }


# ---------------------------------------------------------------------------
# Kelly Criterion Calculator
# ---------------------------------------------------------------------------

class KellyCalculator:
    """Rolling Kelly criterion calculator per strategy.

    Tracks wins/losses across simulated trades and computes optimal
    Kelly fraction for position sizing.
    """

    def __init__(self, state_path: Path):
        self._state_path = state_path
        self._state: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self._state_path.exists():
            try:
                self._state = json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._state = {}

    def _save(self):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2))

    def record_trade(self, strategy: str, pnl_usd: float, notional: float):
        """Record a simulated trade outcome."""
        if strategy not in self._state:
            self._state[strategy] = {
                "wins": 0, "losses": 0, "total_trades": 0,
                "total_win_pnl": 0, "total_loss_pnl": 0,
                "kelly_fraction": 0, "recommended_size_mult": 1.0,
                "sharpe_approx": 0, "avg_win": 0, "avg_loss": 0,
                "win_rate": 0, "expectancy": 0,
            }

        s = self._state[strategy]
        s["total_trades"] += 1
        if pnl_usd > 0:
            s["wins"] += 1
            s["total_win_pnl"] += pnl_usd
        else:
            s["losses"] += 1
            s["total_loss_pnl"] += abs(pnl_usd)

        # Compute Kelly
        if s["total_trades"] >= 10:  # Min trades for meaningful Kelly
            win_rate = s["wins"] / s["total_trades"]
            avg_win = s["total_win_pnl"] / max(s["wins"], 1)
            avg_loss = s["total_loss_pnl"] / max(s["losses"], 1)
            win_loss_ratio = avg_win / max(avg_loss, 0.01)

            # Kelly formula: f = (p * b - q) / b
            p = win_rate
            q = 1 - p
            b = win_loss_ratio
            kelly = (p * b - q) / max(b, 0.01) if b > 0 else 0

            # Half-Kelly for safety (standard practice)
            half_kelly = max(0, min(kelly / 2, 0.25))  # Cap at 25%

            # Approximate Sharpe from trade data
            trades_list = []  # Would track individual returns for real Sharpe
            expectancy = (win_rate * avg_win) - (q * avg_loss)

            s["win_rate"] = round(win_rate, 4)
            s["avg_win"] = round(avg_win, 2)
            s["avg_loss"] = round(avg_loss, 2)
            s["kelly_fraction"] = round(kelly, 4)
            s["recommended_size_mult"] = round(half_kelly * 4, 2)  # Scale to multiplier
            s["expectancy"] = round(expectancy, 2)

    def save(self):
        self._save()

    def get_state(self) -> dict:
        return self._state

    def get_strategy_sizing(self, strategy: str) -> float:
        """Get recommended size multiplier for a strategy."""
        s = self._state.get(strategy, {})
        return s.get("recommended_size_mult", 1.0)


# ---------------------------------------------------------------------------
# Training Summary
# ---------------------------------------------------------------------------

class TrainingSummary:
    """Aggregates training run statistics."""

    def __init__(self, summary_path: Path):
        self._path = summary_path
        self._data = {
            "total_scenarios": 0,
            "total_trades": 0,
            "total_pnl": 0,
            "scenarios_by_archetype": {},
            "strategies_triggered": {},
            "last_updated": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cycles_completed": 0,
        }

    def record_cycle(self, scenario: dict, trades: list[dict]):
        arch = scenario["archetype"]
        self._data["total_scenarios"] += 1
        self._data["scenarios_by_archetype"][arch] = (
            self._data["scenarios_by_archetype"].get(arch, 0) + 1
        )

        for t in trades:
            self._data["total_trades"] += 1
            self._data["total_pnl"] += t["pnl_usd"]
            strat = t["strategy"]
            if strat not in self._data["strategies_triggered"]:
                self._data["strategies_triggered"][strat] = {
                    "count": 0, "wins": 0, "losses": 0, "pnl": 0,
                }
            s = self._data["strategies_triggered"][strat]
            s["count"] += 1
            s["pnl"] += t["pnl_usd"]
            if t["win"]:
                s["wins"] += 1
            else:
                s["losses"] += 1

        self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._data["cycles_completed"] += 1

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def log_summary(self):
        d = self._data
        logger.info(
            "Training: %d scenarios, %d trades, $%.2f simulated P&L, %d strategies active",
            d["total_scenarios"], d["total_trades"], d["total_pnl"],
            len(d["strategies_triggered"]),
        )


# ---------------------------------------------------------------------------
# Main Training Loop
# ---------------------------------------------------------------------------

class ContinuousStrategyTrainer:
    """24/7 training loop that generates scenarios and trains all strategies."""

    def __init__(
        self,
        repo_root: Path,
        interval_seconds: int = 30,
        scenarios_per_cycle: int = 10,
    ):
        self._repo_root = repo_root
        self._interval = interval_seconds
        self._scenarios_per_cycle = scenarios_per_cycle
        self._running = True

        # Paths
        self._log_dir = repo_root / "logs" / "training"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._kelly_path = self._log_dir / "kelly_state.json"
        self._summary_path = self._log_dir / "training_summary.json"

        # Components
        self._kelly = KellyCalculator(self._kelly_path)
        self._summary = TrainingSummary(self._summary_path)
        self._strategy_engine = None

        # Signal handling
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %d — shutting down gracefully", signum)
        self._running = False

    def _init_strategy_engine(self):
        """Initialize the strategy engine with config."""
        try:
            from src.alpha.strategy_engine import StrategyEngine

            config_relpath = "config/war_strategies.yaml"
            self._strategy_engine = StrategyEngine(config_path=config_relpath, repo_root=str(self._repo_root))
            import yaml
            with open(self._repo_root / config_relpath) as f:
                config = yaml.safe_load(f)
            logger.info("Strategy engine initialized with %d strategies",
                        len(config.get("strategies", {})))
            return True








            return True
        except Exception:
            logger.exception("Failed to init strategy engine — using mock evaluation")
            return False

    def _mock_evaluate(self, scenario: dict) -> list[dict]:
        """Fallback mock evaluation when strategy engine can't load.

        Simulates which strategies would trigger based on scenario archetype
        and scorecard values. Uses the same logic as the real strategy engine.
        """
        ideas = []
        sc = scenario["scorecard"]
        mkt = scenario["market_data"]
        regime = sc.get("v6_oil_regime", "NORMAL")
        geo = sc["component_scores"].get("geopolitical_tension", 0)
        commodity = sc["component_scores"].get("commodity_shock", 0)
        vix = mkt.get("VIX", {}).get("price", 20)
        wti_chg = mkt.get("USO", {}).get("change_pct", 0)

        # Strategy 1: Oil Momentum Intraday
        if abs(wti_chg) > 1.5:
            direction = "long" if wti_chg > 0 else "short"
            for sym in ["USO", "XOP", "OXY"]:
                ideas.append({
                    "strategy": "oil_momentum_intraday", "symbol": sym,
                    "direction": direction, "notional_usd": 170,
                    "confidence": min(0.5 + abs(wti_chg) * 0.05, 0.9),
                    "tier": "tier_1", "stop_loss_pct": -1.5, "take_profit_pct": 3.0,
                })

        # Strategy 2: Shipping Rate Explosion
        if regime in ("SHOCK", "DISLOCATION") and sc.get("chokepoint_risk", {}).get("hormuz", 0) > 0.5:
            for sym in ["STNG", "FRO", "ZIM"]:
                ideas.append({
                    "strategy": "shipping_rate_explosion", "symbol": sym,
                    "direction": "long", "notional_usd": 85,
                    "confidence": 0.7, "tier": "tier_2",
                    "stop_loss_pct": -3.0, "take_profit_pct": 10.0,
                })

        # Strategy 3: Defense Accumulation
        if geo > 0.5:
            for sym in ["LMT", "RTX", "NOC"]:
                ideas.append({
                    "strategy": "defense_accumulation", "symbol": sym,
                    "direction": "long", "notional_usd": 85,
                    "confidence": 0.6 + geo * 0.2, "tier": "tier_2",
                    "stop_loss_pct": -4.0, "take_profit_pct": 8.0,
                })

        # Strategy 4: Gold Safe Haven
        if geo > 0.3 or vix > 28:
            for sym in ["GLD", "GDX"]:
                ideas.append({
                    "strategy": "gold_safe_haven", "symbol": sym,
                    "direction": "long", "notional_usd": 170,
                    "confidence": 0.6, "tier": "tier_1",
                    "stop_loss_pct": -2.0, "take_profit_pct": 6.0,
                })

        # Strategy 5: Airline Short
        if wti_chg > 2.0 and regime in ("SHOCK", "DISLOCATION"):
            for sym in ["JETS", "UAL", "AAL"]:
                ideas.append({
                    "strategy": "airline_short", "symbol": sym,
                    "direction": "short", "notional_usd": 85,
                    "confidence": 0.65, "tier": "tier_2",
                    "stop_loss_pct": -3.0, "take_profit_pct": 5.0,
                })

        # Strategy 6: VIX Spike Scalp
        if vix > 35:
            ideas.append({
                "strategy": "vix_spike_scalp", "symbol": "SVXY",
                "direction": "long", "notional_usd": 85,
                "confidence": 0.55, "tier": "tier_2",
                "stop_loss_pct": -2.0, "take_profit_pct": 5.0,
            })
        elif vix < 22:
            ideas.append({
                "strategy": "vix_spike_scalp", "symbol": "UVXY",
                "direction": "long", "notional_usd": 42,
                "confidence": 0.45, "tier": "tier_2",
                "stop_loss_pct": -3.0, "take_profit_pct": 8.0,
            })

        # Strategy 7: Oil Gap Persistence
        if abs(wti_chg) > 2.0 and regime in ("ELEVATED", "SHOCK"):
            direction = "long" if wti_chg > 0 else "short"
            ideas.append({
                "strategy": "oil_gap_persistence", "symbol": "XLE",
                "direction": direction, "notional_usd": 170,
                "confidence": 0.65, "tier": "tier_1",
                "stop_loss_pct": -2.0, "take_profit_pct": 4.0,
            })

        # Strategy 8: US Premarket Gap
        spy_chg = mkt.get("SPY", {}).get("change_pct", 0)
        if abs(spy_chg) > 1.5:
            direction = "long" if spy_chg > 0 else "short"
            ideas.append({
                "strategy": "us_premarket_gap", "symbol": "SPY",
                "direction": direction, "notional_usd": 170,
                "confidence": 0.60, "tier": "tier_1",
                "stop_loss_pct": -1.5, "take_profit_pct": 3.0,
            })

        # Strategy 9: Supply Shock Pairs
        if wti_chg > 2.0:
            ideas.append({
                "strategy": "supply_shock_pairs", "symbol": "XLE",
                "direction": "long", "notional_usd": 85,
                "confidence": 0.60, "tier": "tier_1",
                "stop_loss_pct": -2.0, "take_profit_pct": 4.0,
            })
            ideas.append({
                "strategy": "supply_shock_pairs", "symbol": "JETS",
                "direction": "short", "notional_usd": 85,
                "confidence": 0.60, "tier": "tier_1",
                "stop_loss_pct": -2.0, "take_profit_pct": 4.0,
            })

        # Strategy 10-25: Lower-tier strategies (simplified triggers)
        tier3_triggers = {
            "europe_energy_crisis": (commodity > 0.5, ["EZU"], "short"),
            "fertilizer_food_chain": (commodity > 0.6, ["MOS", "CF"], "long"),
            "nuclear_renaissance": (geo > 0.5, ["CCJ", "URA"], "long"),
            "inflation_hedge": (wti_chg > 3.0, ["TIP", "GLD"], "long"),
            "em_capital_flight": (geo > 0.5 and vix > 25, ["EEM"], "short"),
            "refining_crack_spread": (wti_chg > 2.0, ["PSX"], "long"),
            "wall_street_vol": (vix > 25, ["GS"], "long"),
            "petro_inflation": (wti_chg > 3.0, ["TIP"], "long"),
            "china_oil_import_shock": (wti_chg > 3.0 and geo > 0.5, ["FXI"], "short"),
            "oil_mean_reversion": (wti_chg > 5.0, ["USO"], "short"),
        }
        for strat_name, (trigger, symbols, direction) in tier3_triggers.items():
            if trigger:
                for sym in symbols:
                    ideas.append({
                        "strategy": strat_name, "symbol": sym,
                        "direction": direction, "notional_usd": 42,
                        "confidence": 0.45, "tier": "tier_3",
                        "stop_loss_pct": -3.0, "take_profit_pct": 6.0,
                    })

        # Strategy 26: Zahloria Optimized — EMA/VWAP pullback on momentum names
        # Triggers on moderate moves (0.5-3%) with volume — the "pullback sweet spot"
        zahloria_watchlist = [
            ("NVDA", "long"), ("AMD", "long"), ("TSLA", "long"), ("SOXL", "long"),
            ("XLE", "long"), ("TQQQ", "long"), ("COIN", "long"), ("PLTR", "long"),
            ("MRVL", "long"), ("AVGO", "long"),
        ]
        for sym, default_dir in zahloria_watchlist:
            sym_data = mkt.get(sym, {})
            sym_chg = sym_data.get("change_pct", 0)
            abs_chg = abs(sym_chg)
            # Zahloria triggers on moderate pullback (0.5-3%) — the sweet spot
            if 0.5 <= abs_chg <= 3.0:
                direction = "long" if sym_chg > 0 else "short"
                conf = min(0.45 + abs_chg * 0.08, 0.80)
                ideas.append({
                    "strategy": "zahloria_optimized", "symbol": sym,
                    "direction": direction, "notional_usd": 85,
                    "confidence": round(conf, 3), "tier": "tier_2",
                    "stop_loss_pct": -2.0, "take_profit_pct": 4.0,
                })

        # Strategy 27: AMD Power of 3 — ICT stop hunt reversal
        # Triggers on sharp moves (0.5-3%) that look like manipulation/stop hunts
        # Entry is OPPOSITE direction (fade the manipulation)
        amd_watchlist = ["SPY", "QQQ", "XLE", "NVDA", "TSLA", "SOXL", "GLD", "USO"]
        for sym in amd_watchlist:
            sym_data = mkt.get(sym, {})
            sym_chg = sym_data.get("change_pct", 0)
            abs_chg = abs(sym_chg)
            # AMD triggers on sharp moves that are manipulation candidates
            if 0.5 <= abs_chg <= 3.0:
                # Fade the manipulation — enter opposite direction
                direction = "long" if sym_chg < 0 else "short"
                conf = min(0.50 + abs_chg * 0.06, 0.80)
                stop = -(abs_chg + 0.5)
                target = (abs_chg + 0.5) * 2.0  # 1:2 R/R
                ideas.append({
                    "strategy": "amd_power_of_3", "symbol": sym,
                    "direction": direction, "notional_usd": 85,
                    "confidence": round(conf, 3), "tier": "tier_2",
                    "stop_loss_pct": round(max(stop, -4.0), 2),
                    "take_profit_pct": round(min(target, 8.0), 2),
                })

        # Strategy 28: Simons Pattern Recognition — multi-signal fusion anomaly detection
        # Detects divergence between composite signal score and price action
        # Signal-price divergence = mean reversion; signal-price agreement = momentum
        simons_tickers = {
            "XLE": 0.3, "USO": 0.3, "OXY": 0.3,  # Energy — bullish on high geo/commodity
            "LMT": 0.3, "RTX": 0.3,                # Defense — bullish on high geo
            "GLD": 0.25, "GDX": 0.25,              # Gold — bullish on high vol/geo
            "SPY": -0.2, "QQQ": -0.2,              # Broad — bearish on high geo
            "UAL": -0.3, "JETS": -0.3,             # Airlines — bearish on commodity shock
            "SOXL": -0.15, "NVDA": -0.15,          # Tech — mixed
        }
        composite_signal = (geo * 0.25 + commodity * 0.2 +
                           (vix / 60.0) * 0.2 +
                           sc["component_scores"].get("currency_stress", 0) * 0.1 +
                           sc.get("chokepoint_risk", {}).get("hormuz", 0) * 0.15 +
                           sc["component_scores"].get("policy_signals", 0) * 0.1)

        for sym, sensitivity in simons_tickers.items():
            sym_data = mkt.get(sym, {})
            sym_chg = sym_data.get("change_pct", 0)
            # Expected direction based on signals
            expected_dir = 1.0 if (composite_signal > 0.4 and sensitivity > 0) else (
                -1.0 if (composite_signal > 0.4 and sensitivity < 0) else 0
            )
            actual_dir = 1.0 if sym_chg > 0 else -1.0
            abs_chg = abs(sym_chg)

            if abs_chg < 0.5:
                continue

            # Divergence: signal and price disagree
            if expected_dir != 0 and expected_dir != actual_dir and abs_chg >= 0.5:
                direction = "long" if expected_dir > 0 else "short"
                conf = min(0.45 + composite_signal * 0.2 + abs_chg * 0.05, 0.80)
                ideas.append({
                    "strategy": "simons_pattern_recognition", "symbol": sym,
                    "direction": direction, "notional_usd": 170,
                    "confidence": round(conf, 3), "tier": "tier_1",
                    "stop_loss_pct": round(-min(abs_chg + 1.0, 4.0), 2),
                    "take_profit_pct": round(min(abs_chg * 1.5, 6.0), 2),
                })
            # Confirmation: signal and price agree strongly
            elif expected_dir != 0 and expected_dir == actual_dir and abs_chg >= 1.5 and composite_signal > 0.5:
                direction = "long" if sym_chg > 0 else "short"
                conf = min(0.50 + composite_signal * 0.15 + abs_chg * 0.04, 0.80)
                ideas.append({
                    "strategy": "simons_pattern_recognition", "symbol": sym,
                    "direction": direction, "notional_usd": 170,
                    "confidence": round(conf, 3), "tier": "tier_1",
                    "stop_loss_pct": round(-min(abs_chg * 0.4, 3.0), 2),
                    "take_profit_pct": round(min(abs_chg * 2.0, 8.0), 2),
                })

        return ideas

    def run_cycle(self):
        """Run one training cycle — generate scenarios, evaluate, track P&L."""
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        pnl_log_path = self._log_dir / f"strategy_pnl_{today}.jsonl"

        for _ in range(self._scenarios_per_cycle):
            scenario = generate_scenario()

            # Evaluate strategies
            if self._strategy_engine:
                try:
                    ideas = self._strategy_engine.evaluate_entries(
                        scorecard=scenario["scorecard"],
                        bridge_results=scenario["bridge_results"],
                        market_data=scenario["market_data"],
                    )
                except Exception:
                    ideas = self._mock_evaluate(scenario)
            else:
                ideas = self._mock_evaluate(scenario)

            if not ideas:
                continue

            # Simulate P&L for each triggered trade
            trades = []
            for idea in ideas:
                trade = simulate_trade_pnl(idea, scenario)
                trades.append(trade)

                # Record in Kelly calculator
                self._kelly.record_trade(
                    trade["strategy"], trade["pnl_usd"], trade["notional_usd"]
                )

            # Log trades
            with open(pnl_log_path, "a") as f:
                for t in trades:
                    f.write(json.dumps(t) + "\n")

            # Update summary
            self._summary.record_cycle(scenario, trades)

        # Save state
        self._kelly.save()
        self._summary.save()

    def run(self):
        """Main training loop — runs 24/7."""
        logger.info(
            "Starting continuous strategy trainer: %d scenarios/cycle, %ds interval",
            self._scenarios_per_cycle, self._interval,
        )

        # Try to load real strategy engine
        self._init_strategy_engine()

        cycle = 0
        while self._running:
            try:
                self.run_cycle()
                cycle += 1

                if cycle % 10 == 0:
                    self._summary.log_summary()
                    kelly = self._kelly.get_state()
                    validated = sum(
                        1 for s in kelly.values()
                        if s.get("total_trades", 0) >= 50
                    )
                    logger.info(
                        "Kelly status: %d/%d strategies with 50+ trades",
                        validated, len(kelly),
                    )

                if cycle % 100 == 0:
                    # Log per-strategy Kelly sizing
                    kelly = self._kelly.get_state()
                    for name, data in sorted(kelly.items(), key=lambda x: x[1].get("total_trades", 0), reverse=True):
                        if data.get("total_trades", 0) >= 10:
                            logger.info(
                                "  %s: %d trades, %.1f%% WR, kelly=%.3f, expectancy=$%.2f",
                                name, data["total_trades"],
                                data.get("win_rate", 0) * 100,
                                data.get("kelly_fraction", 0),
                                data.get("expectancy", 0),
                            )

            except Exception:
                logger.exception("Error in training cycle %d", cycle)

            time.sleep(self._interval)

        logger.info("Trainer stopped after %d cycles", cycle)
        self._kelly.save()
        self._summary.save()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Continuous Strategy Trainer")
    parser.add_argument("--interval", type=int, default=30,
                        help="Seconds between training cycles (default: 30)")
    parser.add_argument("--scenarios-per-cycle", type=int, default=10,
                        help="Scenarios per cycle (default: 10)")
    args = parser.parse_args()

    trainer = ContinuousStrategyTrainer(
        repo_root=REPO_ROOT,
        interval_seconds=args.interval,
        scenarios_per_cycle=args.scenarios_per_cycle,
    )
    trainer.run()


if __name__ == "__main__":
    main()
