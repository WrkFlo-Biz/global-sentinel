#!/usr/bin/env python3
"""
Dynamic Kelly Criterion Position Sizer
Based on @julias.algos + Kelly Criterion research. Uses Quarter Kelly for safety.

Tracks win rate per strategy from:
  - reports/paper_trades/ (historical paper trade results)
  - data/quantum_feed/trade_feedback_dataset.jsonl (all trade outcomes)

Calculates per strategy:
  - Win rate (W) = wins / total trades
  - Win/Loss ratio (R) = avg_win / avg_loss
  - Kelly % = W - [(1-W) / R]
  - Quarter Kelly = Kelly% / 4 (safety margin)

Applies to each trade signal:
  - High Kelly% strategies get larger positions
  - Low Kelly% strategies get smaller or skipped
  - Negative Kelly% = DO NOT TRADE this strategy

Rolling recalculation: updates daily with new data.
Output: data/quantum_feed/kelly_sizing.json
"""
import json, os, datetime, glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"
PAPER_TRADES_DIR = REPO_ROOT / "reports" / "paper_trades"
FEEDBACK_FILE = QF / "trade_feedback_dataset.jsonl"
OUTPUT_FILE = QF / "kelly_sizing.json"

# Position sizing bounds
MIN_POSITION_PCT = 0.01   # 1% minimum position
MAX_POSITION_PCT = 0.12   # 12% maximum position (matches day trade sizing)
DEFAULT_EQUITY = 100_000  # fallback if we can't read account


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def log(msg):
    print(f"[{iso_now()}] KELLY: {msg}", flush=True)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Data Ingestion
# ---------------------------------------------------------------------------

def load_paper_trades() -> List[Dict]:
    """Load all paper trade reports from reports/paper_trades/."""
    trades = []
    if not PAPER_TRADES_DIR.exists():
        log(f"  Paper trades dir not found: {PAPER_TRADES_DIR}")
        return trades

    for fpath in sorted(PAPER_TRADES_DIR.glob("*.json")):
        try:
            data = json.loads(fpath.read_text())
            strategy = data.get("strategy", "unknown")
            positions = data.get("positions", [])
            if not isinstance(positions, list):
                positions = []  # handle case where positions is a count (int)
            total_pnl = data.get("total_pnl", 0)
            winners = data.get("winners", 0)
            losers = data.get("losers", 0)
            num_trades = data.get("num_trades", 0)
            date = data.get("date", fpath.stem)

            # If positions have individual PnL, use those
            for pos in positions:
                pnl = pos.get("pnl", pos.get("unrealized_pnl", 0))
                trades.append({
                    "source": "paper_trade",
                    "strategy": strategy,
                    "symbol": pos.get("symbol", "?"),
                    "pnl": float(pnl) if pnl else 0,
                    "date": date,
                    "file": fpath.name,
                })

            # If no positions but summary stats exist, create synthetic entries
            if not positions and num_trades > 0:
                avg_pnl = total_pnl / num_trades if num_trades else 0
                for _ in range(winners):
                    trades.append({
                        "source": "paper_trade",
                        "strategy": strategy,
                        "symbol": "aggregate",
                        "pnl": abs(avg_pnl) if avg_pnl != 0 else 1.0,
                        "date": date,
                        "file": fpath.name,
                    })
                for _ in range(losers):
                    trades.append({
                        "source": "paper_trade",
                        "strategy": strategy,
                        "symbol": "aggregate",
                        "pnl": -abs(avg_pnl) if avg_pnl != 0 else -1.0,
                        "date": date,
                        "file": fpath.name,
                    })
        except Exception as e:
            log(f"  Error loading {fpath}: {e}")

    return trades


def load_feedback_trades() -> List[Dict]:
    """Load trades from trade_feedback_dataset.jsonl."""
    trades = []
    if not FEEDBACK_FILE.exists():
        log(f"  Feedback file not found: {FEEDBACK_FILE}")
        return trades

    try:
        with open(FEEDBACK_FILE, "r") as f:
            lines = f.readlines()
    except Exception as e:
        log(f"  Error reading feedback file: {e}")
        return lines

    # Group by symbol to pair buys/sells
    by_symbol = defaultdict(list)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            sym = record.get("symbol", "")
            by_symbol[sym].append(record)
        except Exception:
            continue

    # Pair buys and sells to calculate PnL
    for sym, records in by_symbol.items():
        buys = [r for r in records if r.get("side") == "buy"]
        sells = [r for r in records if r.get("side") == "sell"]

        # Simple pairing: match buy-sell pairs chronologically
        pairs = min(len(buys), len(sells))
        buys_sorted = sorted(buys, key=lambda x: x.get("date", ""))
        sells_sorted = sorted(sells, key=lambda x: x.get("date", ""))

        for i in range(pairs):
            buy_price = float(buys_sorted[i].get("filled_price", 0))
            sell_price = float(sells_sorted[i].get("filled_price", 0))
            qty = float(buys_sorted[i].get("qty", 1))

            if buy_price > 0 and sell_price > 0:
                pnl = (sell_price - buy_price) * qty
                # Determine strategy from symbol pattern
                strategy = _classify_strategy(sym)
                trades.append({
                    "source": "live_feedback",
                    "strategy": strategy,
                    "symbol": sym,
                    "pnl": round(pnl, 2),
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "qty": qty,
                    "date": sells_sorted[i].get("date", ""),
                })

    return trades


def _classify_strategy(symbol: str) -> str:
    """Classify a symbol into a strategy bucket."""
    sym = symbol.upper()
    # Options have strike/expiry in symbol (e.g., SPY260323P00655000)
    if any(c in sym for c in "CP") and len(sym) > 8 and any(ch.isdigit() for ch in sym[3:]):
        return "options_0dte"
    # Crypto
    if sym.endswith("USD") or sym.endswith("USDT") or sym in ("BTCUSD", "ETHUSD"):
        return "crypto"
    # ETFs
    etfs = {"SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SLV", "XLF", "XLE", "XLK",
            "XLV", "XLI", "XLB", "XLC", "XLY", "XLP", "XLU", "XLRE", "VXX", "UVXY",
            "USO", "UCO", "SCO", "EEM", "EFA", "UUP"}
    if sym in etfs:
        return "etfs"
    # Default to stocks/day trade
    return "day_trade_momentum"


# ---------------------------------------------------------------------------
# Kelly Criterion Calculation
# ---------------------------------------------------------------------------

def calculate_kelly(trades: List[Dict]) -> Dict:
    """
    Calculate Kelly Criterion for a set of trades.
    Returns: {win_rate, win_loss_ratio, kelly_pct, quarter_kelly, recommendation, stats}
    """
    if not trades:
        return {
            "win_rate": 0,
            "win_loss_ratio": 0,
            "kelly_pct": 0,
            "quarter_kelly": 0,
            "position_pct": MIN_POSITION_PCT,
            "total_trades": 0,
            "winners": 0,
            "losers": 0,
            "breakeven": 0,
            "total_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "recommendation": "insufficient_data",
            "min_trades_needed": 20,
        }

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    breakeven = [t for t in trades if t.get("pnl", 0) == 0]

    total = len(wins) + len(losses)
    if total == 0:
        return {
            "win_rate": 0,
            "win_loss_ratio": 0,
            "kelly_pct": 0,
            "quarter_kelly": 0,
            "position_pct": MIN_POSITION_PCT,
            "total_trades": len(trades),
            "winners": 0,
            "losers": 0,
            "breakeven": len(breakeven),
            "total_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "recommendation": "insufficient_data",
        }

    # Win rate
    W = len(wins) / total

    # Average win and loss
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 1

    # Win/Loss ratio
    R = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # Kelly % = W - [(1-W) / R]
    if R == 0 or R == float("inf"):
        kelly_pct = 0
    else:
        kelly_pct = W - ((1 - W) / R)

    # Quarter Kelly for safety
    quarter_kelly = kelly_pct / 4

    # Recommendation
    if total < 20:
        recommendation = "insufficient_data"
    elif kelly_pct < 0:
        recommendation = "DO_NOT_TRADE"
    elif quarter_kelly < MIN_POSITION_PCT:
        recommendation = "minimum_size"
    elif quarter_kelly > MAX_POSITION_PCT:
        recommendation = "cap_at_max"
    else:
        recommendation = "trade"

    # Clamp quarter kelly to bounds
    position_pct = max(0, min(MAX_POSITION_PCT, quarter_kelly))
    if recommendation == "DO_NOT_TRADE":
        position_pct = 0
    elif recommendation == "insufficient_data":
        position_pct = MIN_POSITION_PCT  # conservative default

    return {
        "win_rate": round(W, 4),
        "win_loss_ratio": round(R, 4) if R != float("inf") else 999,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "kelly_pct": round(kelly_pct, 4),
        "quarter_kelly": round(quarter_kelly, 4),
        "position_pct": round(position_pct, 4),
        "total_trades": total,
        "winners": len(wins),
        "losers": len(losses),
        "breakeven": len(breakeven),
        "total_pnl": round(sum(t["pnl"] for t in trades), 2),
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Equity Reading
# ---------------------------------------------------------------------------

def get_account_equity() -> float:
    """Read equity from the paper day-trade account (ALPACA_API_KEY).

    The broker_health_log tracks the *live* account (~$152),
    so we query the paper account directly via API.
    Falls back to DEFAULT_EQUITY only if the API call fails.
    """
    import urllib.request, urllib.error
    env = {}
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    api_key = env.get("ALPACA_API_KEY", os.environ.get("ALPACA_API_KEY", ""))
    api_secret = env.get("ALPACA_SECRET_KEY", os.environ.get("ALPACA_SECRET_KEY", ""))
    if api_key and api_secret:
        try:
            req = urllib.request.Request(
                "https://paper-api.alpaca.markets/v2/account",
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": api_secret,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                acct = json.loads(resp.read())
            eq = float(acct.get("equity", 0))
            if eq > 0:
                log(f"  Paper account equity: ${eq:,.2f}")
                return eq
        except Exception as e:
            log(f"  Paper account API error: {e}")
    # Fallback: try broker routing
    broker = load_json(QF / "broker_routing.json")
    equity = broker.get("equity", broker.get("account_equity", 0))
    if equity and float(equity) > 0:
        return float(equity)
    return DEFAULT_EQUITY


# ---------------------------------------------------------------------------
# Main Runner
# ---------------------------------------------------------------------------

def run_kelly_sizer() -> Dict:
    """Run the Kelly Criterion position sizer across all strategies."""
    log("Running Dynamic Kelly Criterion Position Sizer...")

    # 1. Load all trade data
    paper_trades = load_paper_trades()
    feedback_trades = load_feedback_trades()
    all_trades = paper_trades + feedback_trades
    log(f"  Loaded {len(paper_trades)} paper trades + {len(feedback_trades)} feedback trades = {len(all_trades)} total")

    # 2. Group trades by strategy
    by_strategy = defaultdict(list)
    for t in all_trades:
        by_strategy[t.get("strategy", "unknown")].append(t)

    # Also calculate for known strategy names that may not have trades yet
    known_strategies = [
        "day_trade_momentum", "options_0dte", "crypto", "etfs",
        "futures_commodities", "bonds", "currencies", "sectors",
        "scalping_engine", "ict_smc", "medium_long",
    ]
    for s in known_strategies:
        if s not in by_strategy:
            by_strategy[s] = []

    # 3. Calculate Kelly for each strategy
    equity = get_account_equity()
    strategies_output = {}
    for strategy_name, trades in sorted(by_strategy.items()):
        kelly = calculate_kelly(trades)

        # Calculate dollar sizing
        dollar_size = round(equity * kelly["position_pct"], 2)
        kelly["equity"] = equity
        kelly["dollar_size"] = dollar_size
        kelly["strategy"] = strategy_name

        strategies_output[strategy_name] = kelly

        status = kelly["recommendation"]
        log(f"  {strategy_name:>25s}: W={kelly['win_rate']:.1%} R={kelly['win_loss_ratio']:.2f} "
            f"Kelly={kelly['kelly_pct']:.2%} QK={kelly['quarter_kelly']:.2%} -> {status} "
            f"(${dollar_size:,.0f} per trade, {kelly['total_trades']} trades)")

    # 4. Build summary
    tradeable = {k: v for k, v in strategies_output.items() if v["recommendation"] == "trade"}
    blocked = {k: v for k, v in strategies_output.items() if v["recommendation"] == "DO_NOT_TRADE"}
    insufficient = {k: v for k, v in strategies_output.items() if v["recommendation"] == "insufficient_data"}

    output = {
        "timestamp": iso_now(),
        "strategy": "kelly_sizer",
        "equity": equity,
        "total_trades_analyzed": len(all_trades),
        "data_sources": {
            "paper_trades": len(paper_trades),
            "feedback_trades": len(feedback_trades),
            "paper_trades_dir": str(PAPER_TRADES_DIR),
            "feedback_file": str(FEEDBACK_FILE),
        },
        "strategies": strategies_output,
        "summary": {
            "tradeable_strategies": list(tradeable.keys()),
            "blocked_strategies": list(blocked.keys()),
            "insufficient_data": list(insufficient.keys()),
            "total_strategies": len(strategies_output),
        },
        "sizing_bounds": {
            "min_position_pct": MIN_POSITION_PCT,
            "max_position_pct": MAX_POSITION_PCT,
            "method": "quarter_kelly",
            "note": "Quarter Kelly = Kelly% / 4 for safety margin",
        },
    }

    save_json(OUTPUT_FILE, output)
    log(f"Kelly sizer complete: {len(strategies_output)} strategies analyzed, "
        f"{len(tradeable)} tradeable, {len(blocked)} blocked. Saved to {OUTPUT_FILE}")
    return output


def get_kelly_for_strategy(strategy_name: str) -> Dict:
    """Quick lookup: read kelly_sizing.json and return sizing for one strategy.
    Use this from other strategy modules before placing trades."""
    data = load_json(OUTPUT_FILE)
    if not data or "strategies" not in data:
        return {"position_pct": MIN_POSITION_PCT, "recommendation": "no_kelly_data"}
    return data["strategies"].get(strategy_name, {
        "position_pct": MIN_POSITION_PCT,
        "recommendation": "strategy_not_found",
    })


if __name__ == "__main__":
    result = run_kelly_sizer()
    print(f"\nKelly Sizer Results:")
    print(f"  Equity: ${result['equity']:,.0f}")
    print(f"  Total trades analyzed: {result['total_trades_analyzed']}")
    print(f"  Tradeable strategies: {result['summary']['tradeable_strategies']}")
    print(f"  Blocked (negative Kelly): {result['summary']['blocked_strategies']}")
    print(f"  Insufficient data: {result['summary']['insufficient_data']}")
    print(f"\nPer-Strategy Sizing:")
    for name, kelly in result["strategies"].items():
        print(f"  {name:>25s}: {kelly['recommendation']:>20s} | "
              f"QK={kelly['quarter_kelly']:>7.2%} | ${kelly['dollar_size']:>10,.0f} | "
              f"W={kelly['win_rate']:.1%} ({kelly['total_trades']} trades)")
