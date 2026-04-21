#!/usr/bin/env python3
"""
Global Sentinel - Multi-Broker Paper Trading Simulator

Simulates trade routing across multiple brokers to compare:
- Fill quality and slippage
- Commission costs
- Overall P&L impact
- Execution speed characteristics

After 30 days of data, recommends optimal broker per trade type.

Brokers simulated:
- Alpaca: Real paper fills via API (already connected)
- Tastytrade: Simulated based on published fill quality stats
- IBKR: Simulated based on published fill quality stats

Output: data/quantum_feed/multi_broker_simulation.json
"""

import json
import os
import sys
import time
import datetime
import random
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QUANTUM_FEED = REPO_ROOT / "data" / "quantum_feed"
OUTPUT_PATH = QUANTUM_FEED / "multi_broker_simulation.json"
HISTORY_PATH = REPO_ROOT / "logs" / "multi_broker_history.jsonl"

# Load env
env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())

DT_KEY = env.get("ALPACA_API_KEY", "")
DT_SECRET = env.get("ALPACA_SECRET_KEY", "")
DT_BASE = "https://paper-api.alpaca.markets"

LIVE_KEY = env.get("ALPACA_API_KEY_LIVE", "")
LIVE_SECRET = env.get("ALPACA_SECRET_KEY_LIVE", "")


def log(msg: str):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[{ts}] [MULTI-BROKER] {msg}", flush=True)


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ============================================================
# Broker Fill Models
# ============================================================

class BrokerModel:
    """Base class for broker fill simulation."""
    name: str = "base"

    # Commission schedule
    stock_commission_per_share: float = 0.0
    stock_commission_min: float = 0.0
    option_commission_per_contract: float = 0.0
    option_assignment_fee: float = 0.0

    # Fill quality parameters (relative to NBBO midpoint)
    # Positive = improvement (better than mid), Negative = slippage
    stock_fill_improvement_bps: float = 0.0  # basis points
    stock_fill_improvement_stddev: float = 1.0
    option_fill_improvement_bps: float = 0.0
    option_fill_improvement_stddev: float = 2.0

    # Execution latency (ms)
    latency_mean_ms: float = 50.0
    latency_stddev_ms: float = 20.0

    # Rejection rate (fraction)
    rejection_rate: float = 0.001

    def simulate_stock_fill(self, side: str, price: float, qty: int, spread: float) -> Dict[str, Any]:
        """Simulate a stock fill. Returns fill details."""
        if random.random() < self.rejection_rate:
            return {"status": "rejected", "broker": self.name}

        # Fill improvement in dollars
        improvement_bps = random.gauss(self.stock_fill_improvement_bps, self.stock_fill_improvement_stddev)
        improvement_dollars = price * improvement_bps / 10000.0

        if side == "buy":
            fill_price = price - improvement_dollars  # Lower = better for buyer
        else:
            fill_price = price + improvement_dollars  # Higher = better for seller

        # Ensure fill stays within spread
        half_spread = spread / 2.0
        if side == "buy":
            fill_price = max(fill_price, price - half_spread)
            fill_price = min(fill_price, price + half_spread)
        else:
            fill_price = min(fill_price, price + half_spread)
            fill_price = max(fill_price, price - half_spread)

        commission = max(self.stock_commission_min, self.stock_commission_per_share * qty)
        latency = max(1, random.gauss(self.latency_mean_ms, self.latency_stddev_ms))

        slippage = (fill_price - price) * qty if side == "buy" else (price - fill_price) * qty

        return {
            "status": "filled",
            "broker": self.name,
            "fill_price": round(fill_price, 4),
            "mid_price": price,
            "qty": qty,
            "side": side,
            "commission": round(commission, 4),
            "slippage": round(slippage, 4),
            "improvement_bps": round(improvement_bps, 2),
            "latency_ms": round(latency, 1),
            "asset_type": "stock",
        }

    def simulate_option_fill(self, side: str, price: float, qty: int, spread: float) -> Dict[str, Any]:
        """Simulate an option fill."""
        if random.random() < self.rejection_rate:
            return {"status": "rejected", "broker": self.name}

        improvement_bps = random.gauss(self.option_fill_improvement_bps, self.option_fill_improvement_stddev)
        improvement_dollars = price * improvement_bps / 10000.0

        if side == "buy":
            fill_price = price - improvement_dollars
        else:
            fill_price = price + improvement_dollars

        fill_price = max(0.01, fill_price)  # Options can't go negative

        commission = self.option_commission_per_contract * qty
        latency = max(1, random.gauss(self.latency_mean_ms, self.latency_stddev_ms))
        slippage = (fill_price - price) * qty * 100 if side == "buy" else (price - fill_price) * qty * 100

        return {
            "status": "filled",
            "broker": self.name,
            "fill_price": round(fill_price, 4),
            "mid_price": price,
            "qty": qty,
            "side": side,
            "commission": round(commission, 4),
            "slippage": round(slippage, 4),
            "improvement_bps": round(improvement_bps, 2),
            "latency_ms": round(latency, 1),
            "asset_type": "option",
        }


class AlpacaModel(BrokerModel):
    """Alpaca: Commission-free stocks, $0.65/contract options."""
    name = "alpaca"
    stock_commission_per_share = 0.0
    stock_commission_min = 0.0
    option_commission_per_contract = 0.65
    option_assignment_fee = 0.0

    # Alpaca routes through Virtu/Citadel - decent PFOF fill quality
    stock_fill_improvement_bps = 1.5  # ~1.5 bps improvement on average
    stock_fill_improvement_stddev = 2.0
    option_fill_improvement_bps = -5.0  # Options fills slightly worse than mid
    option_fill_improvement_stddev = 8.0

    latency_mean_ms = 45.0
    latency_stddev_ms = 25.0
    rejection_rate = 0.002


class TastytradeModel(BrokerModel):
    """Tastytrade: Capped commissions, good options fills."""
    name = "tastytrade"
    stock_commission_per_share = 0.0
    stock_commission_min = 0.0
    option_commission_per_contract = 1.00  # $1/contract to open, $0 to close
    option_assignment_fee = 0.0

    # Tastytrade known for good options execution
    stock_fill_improvement_bps = 1.0
    stock_fill_improvement_stddev = 1.5
    option_fill_improvement_bps = 2.0  # Better options fills than Alpaca
    option_fill_improvement_stddev = 5.0

    latency_mean_ms = 35.0
    latency_stddev_ms = 15.0
    rejection_rate = 0.001


class IBKRModel(BrokerModel):
    """Interactive Brokers Pro: Per-share commissions, best execution."""
    name = "ibkr"
    stock_commission_per_share = 0.005  # $0.005/share, $1 min
    stock_commission_min = 1.00
    option_commission_per_contract = 0.65
    option_assignment_fee = 0.0

    # IBKR routes to exchanges directly, best fill quality
    stock_fill_improvement_bps = 3.0  # Best price improvement
    stock_fill_improvement_stddev = 1.5
    option_fill_improvement_bps = 4.0  # Excellent options fills
    option_fill_improvement_stddev = 4.0

    latency_mean_ms = 15.0  # Fastest execution
    latency_stddev_ms = 8.0
    rejection_rate = 0.0005


BROKER_MODELS = {
    "alpaca": AlpacaModel(),
    "tastytrade": TastytradeModel(),
    "ibkr": IBKRModel(),
}


# ============================================================
# Simulation Engine
# ============================================================

class MultiBrokerSimulator:
    def __init__(self):
        self.simulation_data: List[Dict[str, Any]] = []
        self.broker_stats: Dict[str, Dict[str, Any]] = {
            name: {
                "total_trades": 0,
                "stock_trades": 0,
                "option_trades": 0,
                "total_commission": 0.0,
                "total_slippage": 0.0,
                "total_pnl_impact": 0.0,  # commission + slippage
                "avg_fill_improvement_bps": 0.0,
                "avg_latency_ms": 0.0,
                "rejections": 0,
                "fills": [],
            }
            for name in BROKER_MODELS
        }
        self._load_history()

    def _load_history(self):
        """Load historical simulation data."""
        if HISTORY_PATH.exists():
            try:
                for line in HISTORY_PATH.read_text().splitlines():
                    if line.strip():
                        entry = json.loads(line)
                        self.simulation_data.append(entry)
                log(f"Loaded {len(self.simulation_data)} historical simulation entries")
            except Exception as e:
                log(f"History load error: {e}")

    def simulate_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: int,
        spread: float,
        asset_type: str = "stock",
        signal_source: str = "",
    ) -> Dict[str, Any]:
        """Simulate a trade across all brokers."""
        timestamp = now_utc().isoformat()
        results = {}

        for broker_name, model in BROKER_MODELS.items():
            if asset_type == "option":
                fill = model.simulate_option_fill(side, price, qty, spread)
            else:
                fill = model.simulate_stock_fill(side, price, qty, spread)

            fill["timestamp"] = timestamp
            fill["symbol"] = symbol
            fill["signal_source"] = signal_source
            results[broker_name] = fill

            # Update stats
            stats = self.broker_stats[broker_name]
            if fill["status"] == "filled":
                stats["total_trades"] += 1
                if asset_type == "stock":
                    stats["stock_trades"] += 1
                else:
                    stats["option_trades"] += 1
                stats["total_commission"] += fill["commission"]
                stats["total_slippage"] += fill["slippage"]
                stats["total_pnl_impact"] += fill["commission"] + fill["slippage"]
                stats["fills"].append(fill)
            else:
                stats["rejections"] += 1

        entry = {
            "timestamp": timestamp,
            "symbol": symbol,
            "side": side,
            "price": price,
            "qty": qty,
            "spread": spread,
            "asset_type": asset_type,
            "signal_source": signal_source,
            "broker_fills": results,
        }
        self.simulation_data.append(entry)

        # Append to history
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            log(f"History write error: {e}")

        return entry

    def get_broker_summary(self) -> Dict[str, Any]:
        """Compute per-broker summary statistics."""
        summaries = {}
        for broker_name, stats in self.broker_stats.items():
            fills = stats["fills"]
            n = len(fills)
            if n == 0:
                summaries[broker_name] = {
                    "total_trades": 0,
                    "message": "No fills yet",
                }
                continue

            avg_improvement = sum(f["improvement_bps"] for f in fills) / n
            avg_latency = sum(f["latency_ms"] for f in fills) / n
            avg_commission = stats["total_commission"] / n
            avg_slippage = stats["total_slippage"] / n

            stock_fills = [f for f in fills if f["asset_type"] == "stock"]
            option_fills = [f for f in fills if f["asset_type"] == "option"]

            summaries[broker_name] = {
                "total_trades": n,
                "stock_trades": len(stock_fills),
                "option_trades": len(option_fills),
                "total_commission": round(stats["total_commission"], 2),
                "total_slippage": round(stats["total_slippage"], 2),
                "total_pnl_impact": round(stats["total_pnl_impact"], 2),
                "avg_fill_improvement_bps": round(avg_improvement, 2),
                "avg_latency_ms": round(avg_latency, 1),
                "avg_commission_per_trade": round(avg_commission, 4),
                "avg_slippage_per_trade": round(avg_slippage, 4),
                "rejections": stats["rejections"],
                "stock_avg_improvement_bps": round(
                    sum(f["improvement_bps"] for f in stock_fills) / len(stock_fills), 2
                ) if stock_fills else 0,
                "option_avg_improvement_bps": round(
                    sum(f["improvement_bps"] for f in option_fills) / len(option_fills), 2
                ) if option_fills else 0,
            }

        return summaries

    def get_recommendations(self) -> Dict[str, Any]:
        """After 30 days of data, recommend optimal broker per trade type."""
        total_days = self._count_unique_days()
        summaries = self.get_broker_summary()

        recommendations = {
            "data_days": total_days,
            "sufficient_data": total_days >= 30,
            "per_trade_type": {},
        }

        if total_days < 30:
            recommendations["message"] = (
                f"Need {30 - total_days} more days of data before making recommendations. "
                f"Currently have {total_days} days."
            )
            # Still provide preliminary rankings
            recommendations["preliminary"] = True

        # Rank brokers for stocks
        stock_rankings = []
        for name, summary in summaries.items():
            if summary.get("stock_trades", 0) == 0:
                continue
            # Score: lower total cost is better
            # Cost = avg commission + avg slippage (negative slippage = improvement)
            fills = self.broker_stats[name]["fills"]
            stock_fills = [f for f in fills if f["asset_type"] == "stock"]
            if not stock_fills:
                continue
            avg_total_cost = (
                sum(f["commission"] + f["slippage"] for f in stock_fills) / len(stock_fills)
            )
            stock_rankings.append((name, avg_total_cost, summary))

        stock_rankings.sort(key=lambda x: x[1])
        if stock_rankings:
            best = stock_rankings[0]
            recommendations["per_trade_type"]["stocks"] = {
                "recommended_broker": best[0],
                "avg_total_cost_per_trade": round(best[1], 4),
                "ranking": [
                    {"broker": r[0], "avg_total_cost": round(r[1], 4)}
                    for r in stock_rankings
                ],
                "reasoning": f"{best[0]} has lowest total cost (commission + slippage) for stock trades",
            }

        # Rank brokers for options
        option_rankings = []
        for name, summary in summaries.items():
            if summary.get("option_trades", 0) == 0:
                continue
            fills = self.broker_stats[name]["fills"]
            option_fills = [f for f in fills if f["asset_type"] == "option"]
            if not option_fills:
                continue
            avg_total_cost = (
                sum(f["commission"] + f["slippage"] for f in option_fills) / len(option_fills)
            )
            option_rankings.append((name, avg_total_cost, summary))

        option_rankings.sort(key=lambda x: x[1])
        if option_rankings:
            best = option_rankings[0]
            recommendations["per_trade_type"]["options"] = {
                "recommended_broker": best[0],
                "avg_total_cost_per_trade": round(best[1], 4),
                "ranking": [
                    {"broker": r[0], "avg_total_cost": round(r[1], 4)}
                    for r in option_rankings
                ],
                "reasoning": f"{best[0]} has lowest total cost for option trades",
            }

        # Best for latency-sensitive trades (scalping)
        latency_rankings = []
        for name, summary in summaries.items():
            if summary.get("total_trades", 0) == 0:
                continue
            latency_rankings.append((name, summary["avg_latency_ms"]))
        latency_rankings.sort(key=lambda x: x[1])
        if latency_rankings:
            recommendations["per_trade_type"]["latency_sensitive"] = {
                "recommended_broker": latency_rankings[0][0],
                "avg_latency_ms": latency_rankings[0][1],
                "ranking": [
                    {"broker": r[0], "avg_latency_ms": r[1]}
                    for r in latency_rankings
                ],
            }

        return recommendations

    def _count_unique_days(self) -> int:
        """Count unique trading days in simulation data."""
        days = set()
        for entry in self.simulation_data:
            ts = entry.get("timestamp", "")
            if ts:
                days.add(ts[:10])
        return len(days)

    def write_output(self):
        """Write simulation results to quantum feed output."""
        summaries = self.get_broker_summary()
        recommendations = self.get_recommendations()

        output = {
            "timestamp": now_utc().isoformat(),
            "simulation_type": "multi_broker_paper",
            "total_simulated_trades": len(self.simulation_data),
            "data_days": self._count_unique_days(),
            "broker_summaries": summaries,
            "recommendations": recommendations,
            "recent_trades": self.simulation_data[-20:] if self.simulation_data else [],
        }

        QUANTUM_FEED.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(output, indent=2))
        log(f"Output written to {OUTPUT_PATH}")
        return output


# ============================================================
# Integration with Paper Trader
# ============================================================

def simulate_from_paper_trades():
    """Read today's paper trade reports and simulate across brokers."""
    simulator = MultiBrokerSimulator()
    reports_dir = REPO_ROOT / "reports" / "paper_trades"

    if not reports_dir.exists():
        log("No paper trade reports directory found")
        simulator.write_output()
        return

    # Process all report files
    for report_file in sorted(reports_dir.glob("day_trade_*.json")):
        try:
            report = json.loads(report_file.read_text())
            for pos in report.get("positions", []):
                symbol = pos.get("underlying", pos.get("symbol", ""))
                entry_price = pos.get("entry_price", 0)
                exit_price = pos.get("exit_price", pos.get("current_price", entry_price))

                if not symbol or not entry_price:
                    continue

                # Determine asset type
                is_option = len(pos.get("symbol", "")) > 10  # Option symbols are longer
                asset_type = "option" if is_option else "stock"
                qty = pos.get("qty", 1)

                # Estimate spread (typically 0.1% for liquid stocks, 2-5% for options)
                if asset_type == "option":
                    spread = entry_price * 0.03  # 3% typical option spread
                else:
                    spread = entry_price * 0.001  # 0.1% typical stock spread

                signal_source = ",".join(
                    pos.get("signal_consensus", {}).get("triggered_signals", {}).keys()
                ) if pos.get("signal_consensus") else "momentum"

                # Simulate entry
                simulator.simulate_trade(
                    symbol=symbol,
                    side="buy",
                    price=entry_price,
                    qty=qty,
                    spread=spread,
                    asset_type=asset_type,
                    signal_source=signal_source,
                )

                # Simulate exit
                if exit_price and exit_price != entry_price:
                    simulator.simulate_trade(
                        symbol=symbol,
                        side="sell",
                        price=exit_price,
                        qty=qty,
                        spread=spread,
                        asset_type=asset_type,
                        signal_source=signal_source,
                    )
        except Exception as e:
            log(f"Error processing {report_file}: {e}")

    # Also process live Alpaca positions for real-time comparison
    try:
        _simulate_current_alpaca_positions(simulator)
    except Exception as e:
        log(f"Error simulating current positions: {e}")

    output = simulator.write_output()

    # Log summary
    for broker, summary in output.get("broker_summaries", {}).items():
        total = summary.get("total_trades", 0)
        if total > 0:
            log(f"  {broker}: {total} trades, "
                f"commission=${summary.get('total_commission', 0):.2f}, "
                f"slippage=${summary.get('total_slippage', 0):.2f}, "
                f"improvement={summary.get('avg_fill_improvement_bps', 0):.1f}bps")

    recs = output.get("recommendations", {})
    for trade_type, rec in recs.get("per_trade_type", {}).items():
        log(f"  Best for {trade_type}: {rec.get('recommended_broker', 'N/A')}")

    return output


def _simulate_current_alpaca_positions(simulator: MultiBrokerSimulator):
    """Simulate current Alpaca paper positions across other brokers."""
    if not DT_KEY or not DT_SECRET:
        return

    url = f"{DT_BASE}/v2/positions"
    req = urllib.request.Request(url)
    req.add_header("APCA-API-KEY-ID", DT_KEY)
    req.add_header("APCA-API-SECRET-KEY", DT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            positions = json.loads(resp.read())
    except Exception:
        return

    for pos in (positions or []):
        symbol = pos.get("symbol", "")
        entry = float(pos.get("avg_entry_price", 0))
        current = float(pos.get("current_price", 0))
        qty = abs(int(pos.get("qty", 0)))
        side = pos.get("side", "long")

        if not symbol or not entry or not qty:
            continue

        is_option = len(symbol) > 10
        asset_type = "option" if is_option else "stock"
        spread = entry * (0.03 if is_option else 0.001)

        simulator.simulate_trade(
            symbol=symbol,
            side="buy" if side == "long" else "sell",
            price=entry,
            qty=qty,
            spread=spread,
            asset_type=asset_type,
            signal_source="live_position",
        )


# ============================================================
# CLI
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Broker Paper Trading Simulator")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--loop-minutes", type=int, default=60, help="Loop interval in minutes")
    parser.add_argument("--test", action="store_true", help="Run with synthetic test trades")
    args = parser.parse_args()

    if args.test:
        log("Running test simulation with synthetic trades...")
        sim = MultiBrokerSimulator()
        test_trades = [
            ("SPY", "buy", 565.50, 100, 0.05, "stock"),
            ("SPY", "sell", 567.20, 100, 0.05, "stock"),
            ("QQQ", "buy", 480.00, 50, 0.04, "stock"),
            ("QQQ", "sell", 478.50, 50, 0.04, "stock"),
            ("TSLA", "buy", 170.00, 30, 0.15, "stock"),
            ("SPY240329C565", "buy", 2.50, 5, 0.15, "option"),
            ("SPY240329C565", "sell", 3.10, 5, 0.15, "option"),
            ("TSLA240329P170", "buy", 1.80, 3, 0.20, "option"),
        ]
        for symbol, side, price, qty, spread, atype in test_trades:
            result = sim.simulate_trade(symbol, side, price, qty, spread, atype, "test")
            for broker, fill in result["broker_fills"].items():
                if fill["status"] == "filled":
                    log(f"  {broker}: {symbol} {side} fill=${fill['fill_price']:.4f} "
                        f"(mid=${price:.2f}), imp={fill['improvement_bps']:.1f}bps, "
                        f"comm=${fill['commission']:.2f}, lat={fill['latency_ms']:.0f}ms")
        output = sim.write_output()
        print(json.dumps(output, indent=2))
        return

    if args.once:
        simulate_from_paper_trades()
        return

    log("Starting multi-broker simulation loop...")
    while True:
        try:
            simulate_from_paper_trades()
        except Exception as e:
            log(f"Error: {e}")
        time.sleep(args.loop_minutes * 60)


if __name__ == "__main__":
    main()
