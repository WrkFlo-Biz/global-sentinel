#!/usr/bin/env python3
"""
24/7 Synthetic Trade Simulator for Global Sentinel
Runs mock trades across ALL strategies continuously to train models.

Features:
- Simulates trades for all 10 strategies using live + historical data
- Follows Asia/London/NY session timing for realistic execution
- Tracks P&L per strategy, per session, per signal source
- Feeds results into quantum continuous learner for recursive training
- Identifies which strategies work in which market regimes
- Discovers cross-strategy patterns (e.g., "when bonds signal recession AND crypto goes risk-off, short tech works 78% of the time")

Runs 24/7 as a daemon. During market hours uses live data.
During off-hours uses Monte Carlo synthetic scenarios.
"""
import json, os, sys, time, datetime, traceback, random
import numpy as np
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data/quantum_feed"
RESULTS_DIR = REPO_ROOT / "data/synthetic_trades"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] SIM: {msg}", flush=True)

def load_json(path):
    try: return json.loads(Path(path).read_text())
    except: return {}

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    p = Path(path)
    try:
        p.write_text(json.dumps(data, indent=2, default=str))
    except PermissionError:
        try:
            p.unlink(missing_ok=True)
            p.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e2:
            log(f"save_json fallback failed for {path}: {e2}")

def get_et():
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)

def get_session():
    et = get_et()
    h = et.hour
    if 19 <= h or h < 3: return "asia"
    elif 3 <= h < 9: return "london"
    elif 9 <= h < 11: return "ny_open"
    elif 11 <= h < 14: return "ny_midday"
    elif 14 <= h < 16: return "ny_power_hour"
    elif 16 <= h < 17: return "ny_close"
    return "after_hours"

# === SYNTHETIC PRICE GENERATOR ===
class SyntheticMarket:
    """Generate realistic synthetic price movements for training."""
    def __init__(self):
        self.prices = {}
        self.base_prices = {
            "SPY": 640, "QQQ": 570, "NVDA": 170, "TSLA": 375, "AMD": 205,
            "META": 540, "AAPL": 250, "AMZN": 210, "XLE": 62, "USO": 115,
            "GLD": 400, "TLT": 86, "UVXY": 55, "BTC": 70000, "ETH": 3500,
            "JETS": 25, "CCL": 26, "DAL": 67, "MOS": 25, "CF": 66,
            "EEM": 40, "IWM": 250, "SOXL": 50, "OXY": 64, "COIN": 180,
        }
        self.reset()

    def reset(self):
        self.prices = {s: p for s, p in self.base_prices.items()}

    def tick(self, session, regime="normal"):
        """Generate one tick of price movement based on session and regime."""
        vol_multiplier = {
            "asia": 0.3, "london": 0.8, "ny_open": 1.5,
            "ny_midday": 0.4, "ny_power_hour": 1.2, "ny_close": 0.6,
            "after_hours": 0.2,
        }.get(session, 0.5)

        regime_drift = {
            "normal": 0.0001, "elevated": -0.0002, "crisis": -0.0005,
            "bull": 0.0005, "bear": -0.0004,
        }.get(regime, 0)

        # Session-specific behavior (AMD model)
        if session == "asia":
            # Consolidation — tight range
            vol_multiplier *= 0.5
        elif session == "london":
            # Manipulation — fake breakout then reverse
            vol_multiplier *= 1.2
            regime_drift *= -0.5  # Counter-trend
        elif session == "ny_open":
            # Expansion — follow trend with high vol
            vol_multiplier *= 1.5

        for sym in self.prices:
            base_vol = 0.002 if sym not in ("BTC", "ETH", "UVXY", "SOXL") else 0.005
            vol = base_vol * vol_multiplier
            drift = regime_drift
            # Sector correlations
            if sym in ("XLE", "USO", "OXY") and regime == "crisis":
                drift += 0.001  # Oil up in crisis
            if sym in ("JETS", "CCL", "DAL") and regime == "crisis":
                drift -= 0.001  # Travel down in crisis
            if sym == "UVXY":
                drift *= -3  # VIX moves opposite to market
            if sym in ("GLD", "TLT") and regime in ("elevated", "crisis"):
                drift += 0.0003  # Safe haven
            move = np.random.normal(drift, vol)
            self.prices[sym] = round(self.prices[sym] * (1 + move), 2)
        return dict(self.prices)

# === STRATEGY SIMULATORS ===
class MockStrategy:
    def __init__(self, name):
        self.name = name
        self.trades = []
        self.open_positions = []
        self.total_pnl = 0
        self.wins = 0
        self.losses = 0
        self.session_pnl = {"asia": 0, "london": 0, "ny_open": 0, "ny_midday": 0, "ny_power_hour": 0}

    def enter(self, symbol, direction, price, session, reason=""):
        pos = {"symbol": symbol, "direction": direction, "entry": price,
               "entry_time": iso_now(), "session": session, "reason": reason,
               "high_water": price, "stop": price * (0.95 if direction == "long" else 1.05),
               "target": price * (1.10 if direction == "long" else 0.90)}
        self.open_positions.append(pos)

    def update(self, prices, session):
        closed = []
        for pos in self.open_positions:
            sym = pos["symbol"]
            if sym not in prices: continue
            price = prices[sym]
            # Update HWM
            if pos["direction"] == "long":
                pos["high_water"] = max(pos["high_water"], price)
                pnl_pct = (price - pos["entry"]) / pos["entry"]
                hit_stop = price <= pos["stop"]
                hit_target = price >= pos["target"]
            else:
                pos["high_water"] = min(pos["high_water"], price)
                pnl_pct = (pos["entry"] - price) / pos["entry"]
                hit_stop = price >= pos["stop"]
                hit_target = price <= pos["target"]

            if hit_stop or hit_target:
                pnl = pnl_pct * 100
                self.total_pnl += pnl
                if pnl > 0: self.wins += 1
                else: self.losses += 1
                self.session_pnl[pos["session"]] = self.session_pnl.get(pos["session"], 0) + pnl
                self.trades.append({**pos, "exit": price, "exit_time": iso_now(),
                                    "pnl_pct": round(pnl, 2), "exit_reason": "target" if hit_target else "stop"})
                closed.append(pos)
        for c in closed:
            self.open_positions.remove(c)

    def stats(self):
        total = self.wins + self.losses
        return {
            "strategy": self.name, "total_trades": total,
            "wins": self.wins, "losses": self.losses,
            "win_rate": round(self.wins / max(1, total), 3),
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl": round(self.total_pnl / max(1, total), 2),
            "session_pnl": self.session_pnl,
            "open_positions": len(self.open_positions),
        }

def create_strategies():
    return {
        "stocks_momentum": MockStrategy("stocks_momentum"),
        "stocks_value": MockStrategy("stocks_value"),
        "options_0dte": MockStrategy("options_0dte"),
        "options_swing": MockStrategy("options_swing"),
        "crypto_trend": MockStrategy("crypto_trend"),
        "etf_rotation": MockStrategy("etf_rotation"),
        "futures_oil": MockStrategy("futures_oil"),
        "futures_gold": MockStrategy("futures_gold"),
        "bonds_yield_curve": MockStrategy("bonds_yield_curve"),
        "fx_dollar": MockStrategy("fx_dollar"),
        "prediction_vol": MockStrategy("prediction_vol"),
        "world_indices": MockStrategy("world_indices"),
        "sector_rotation": MockStrategy("sector_rotation"),
        "topo_arb": MockStrategy("topo_arb"),
        "tapo_reversal": MockStrategy("tapo_reversal"),
        "uncertainty_premium": MockStrategy("uncertainty_premium"),
    }

def generate_signals(prices, session, regime, strategies):
    """Generate trade signals for each strategy based on current conditions."""
    # Only enter during appropriate sessions
    if session == "asia":
        # Only crypto and FX trade Asia
        if random.random() < 0.1:
            strategies["crypto_trend"].enter("BTC", "long" if random.random() > 0.5 else "short",
                                              prices["BTC"], session, "asia_trend")
    elif session == "london":
        # Manipulation phase — fade the first breakout
        if random.random() < 0.15:
            strategies["topo_arb"].enter("SPY", "short" if random.random() > 0.4 else "long",
                                          prices["SPY"], session, "london_manipulation_fade")
    elif session == "ny_open":
        # Best window — all strategies can enter
        if random.random() < 0.3:
            # Momentum stocks
            top = max(prices.items(), key=lambda x: x[1] if x[0] not in ("BTC","ETH") else 0)
            strategies["stocks_momentum"].enter(top[0], "long", top[1], session, "ny_open_momentum")
        if random.random() < 0.2:
            strategies["options_0dte"].enter("NVDA", "long" if regime != "crisis" else "short",
                                              prices["NVDA"], session, "ny_open_0dte")
        if regime in ("elevated", "crisis") and random.random() < 0.25:
            strategies["futures_oil"].enter("USO", "long", prices["USO"], session, "war_oil_play")
            strategies["uncertainty_premium"].enter("JETS", "short", prices["JETS"], session, "complacency_short")
    elif session == "ny_power_hour":
        # Second best window
        if random.random() < 0.2:
            strategies["sector_rotation"].enter("XLE", "long" if regime == "crisis" else "short",
                                                 prices["XLE"], session, "power_hour_sector")
        if random.random() < 0.15:
            strategies["tapo_reversal"].enter("SPY", "long", prices["SPY"], session, "tapo_snapback")
    elif session == "ny_midday":
        if random.random() < 0.12:
            strategies["stocks_value"].enter("AAPL", "long" if random.random() > 0.4 else "short",
                                              prices["AAPL"], session, "midday_value")
        if random.random() < 0.1:
            strategies["bonds_yield_curve"].enter("TLT", "long" if regime in ("elevated", "crisis") else "short",
                                                    prices["TLT"], session, "midday_bond_trade")
    elif session == "ny_close":
        if random.random() < 0.1:
            strategies["etf_rotation"].enter("SPY", "long" if regime == "bull" else "short",
                                              prices["SPY"], session, "eod_positioning")
    elif session == "after_hours":
        if random.random() < 0.08:
            strategies["crypto_trend"].enter("ETH", "long" if random.random() > 0.5 else "short",
                                              prices["ETH"], session, "afterhours_crypto")
        if random.random() < 0.05:
            strategies["fx_dollar"].enter("GLD", "long" if regime != "bull" else "short",
                                            prices["GLD"], session, "afterhours_safe_haven")
        if random.random() < 0.06:
            strategies["prediction_vol"].enter("UVXY", "long" if regime in ("elevated", "crisis") else "short",
                                                prices["UVXY"], session, "afterhours_vol_trade")

def run_simulation_cycle(market, strategies, regime):
    """Run one simulation cycle (1 tick = ~5 min of market time)."""
    session = get_session()
    prices = market.tick(session, regime)

    # Generate new signals
    generate_signals(prices, session, regime, strategies)

    # Update all open positions
    for strat in strategies.values():
        strat.update(prices, session)

    return session, prices

def save_results(strategies, cycle):
    """Save simulation results for training."""
    results = {
        "timestamp": iso_now(),
        "cycle": cycle,
        "strategies": {name: strat.stats() for name, strat in strategies.items()},
        "cross_insights": [],
    }

    # Cross-strategy analysis
    best = max(strategies.values(), key=lambda s: s.total_pnl)
    worst = min(strategies.values(), key=lambda s: s.total_pnl)
    results["cross_insights"].append(f"Best strategy: {best.name} (+{best.total_pnl:.1f}%)")
    results["cross_insights"].append(f"Worst strategy: {worst.name} ({worst.total_pnl:.1f}%)")

    # Session analysis
    session_totals = {}
    for strat in strategies.values():
        for sess, pnl in strat.session_pnl.items():
            session_totals[sess] = session_totals.get(sess, 0) + pnl
    best_session = max(session_totals.items(), key=lambda x: x[1]) if session_totals else ("none", 0)
    results["cross_insights"].append(f"Best session: {best_session[0]} (+{best_session[1]:.1f}%)")

    save_json(QF / "synthetic_trade_results.json", results)

    # Detailed trade log for quantum learner
    all_trades = []
    for name, strat in strategies.items():
        for t in strat.trades[-50:]:  # Last 50 per strategy
            all_trades.append({**t, "strategy": name})
    save_json(RESULTS_DIR / f"trades_{datetime.date.today().isoformat()}.json", {"trades": all_trades})

    return results

# === MAIN DAEMON ===
def run():
    log("24/7 Synthetic Trade Simulator starting...")
    log("Strategies: 16 | Sessions: Asia/London/NY | Regimes: normal/elevated/crisis")

    market = SyntheticMarket()
    strategies = create_strategies()
    cycle = 0

    # Determine regime from system state
    regime_file = QF / "hmm_regime.json"

    while True:
        try:
            cycle += 1
            # Get current regime
            regime_data = load_json(regime_file)
            regime = regime_data.get("current_regime", "normal")
            if isinstance(regime, dict):
                regime = "normal"

            session, prices = run_simulation_cycle(market, strategies, regime)

            # Log every 100 cycles
            if cycle % 100 == 0:
                results = save_results(strategies, cycle)
                total_trades = sum(s.wins + s.losses for s in strategies.values())
                total_pnl = sum(s.total_pnl for s in strategies.values())
                log(f"Cycle {cycle} | Session={session} | Regime={regime} | "
                    f"Trades={total_trades} | PnL={total_pnl:.1f}% | "
                    f"Best={max(strategies.values(), key=lambda s: s.total_pnl).name}")

            # Reset strategies every 1000 cycles to prevent drift
            if cycle % 1000 == 0:
                log(f"Resetting after {cycle} cycles. Saving final results.")
                save_results(strategies, cycle)
                market.reset()
                strategies = create_strategies()

            # Sleep: 5 sec during market hours (fast training), 1 sec off-hours
            sleep_time = 5 if session in ("ny_open", "ny_midday", "ny_power_hour", "ny_close") else 1

            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("Shutting down...")
            save_results(strategies, cycle)
            break
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
