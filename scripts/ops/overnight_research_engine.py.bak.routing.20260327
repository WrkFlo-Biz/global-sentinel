#!/usr/bin/env python3
"""Overnight Research Engine for Global Sentinel.

Runs continuously until 8:30 AM ET, performing market research and
scenario analysis every 30 minutes. Fetches crypto, futures, Asian
markets, oil, and gold data, then scores and ranks day-trade scenarios
for a $125 LIVE account capital base. Goal: 2x return at Monday open.

Usage:
    python scripts/ops/overnight_research_engine.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup & env loading
# ---------------------------------------------------------------------------
sys.path.insert(0, "/opt/global-sentinel")

ENV_PATH = Path("/opt/global-sentinel/.env")
REPO_ROOT = Path("/opt/global-sentinel")
RESEARCH_LOG = REPO_ROOT / "logs" / "research" / "overnight_research.jsonl"
TRADE_PLAN_PATH = REPO_ROOT / "reports" / "flash" / "morning_trade_plan.json"
FINAL_PICKS_PATH = REPO_ROOT / "reports" / "flash" / "final_morning_picks.json"

ET = timezone(timedelta(hours=-4))  # EDT (March = DST active)


def load_dotenv(path: Path) -> None:
    """Minimal .env loader — no dependencies."""
    if not path.exists():
        print(f"[WARN] .env not found at {path}")
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val


load_dotenv(ENV_PATH)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> Optional[Dict[str, Any]]:
    """GET JSON from url, return parsed dict or None on error."""
    hdrs = {"Accept": "application/json", "User-Agent": "GlobalSentinel/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  [HTTP ERR] {url[:80]}... => {exc}")
        return None


def alpaca_headers() -> Dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------
def fetch_crypto_prices() -> Dict[str, Any]:
    """Fetch BTC, ETH, SOL latest quotes from Alpaca."""
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes?symbols=BTC/USD,ETH/USD,SOL/USD"
    data = http_get(url, headers=alpaca_headers())
    result: Dict[str, Any] = {}
    if data and "quotes" in data:
        for sym, quote in data["quotes"].items():
            mid = (float(quote.get("ap", 0)) + float(quote.get("bp", 0))) / 2
            result[sym] = {
                "bid": float(quote.get("bp", 0)),
                "ask": float(quote.get("ap", 0)),
                "mid": round(mid, 2),
                "timestamp": quote.get("t", ""),
            }
    return result


def fetch_stock_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch latest quote for a single stock/ETF from Alpaca."""
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest"
    data = http_get(url, headers=alpaca_headers())
    if data and "quote" in data:
        q = data["quote"]
        mid = (float(q.get("ap", 0)) + float(q.get("bp", 0))) / 2
        return {
            "symbol": symbol,
            "bid": float(q.get("bp", 0)),
            "ask": float(q.get("ap", 0)),
            "mid": round(mid, 4),
            "timestamp": q.get("t", ""),
        }
    return None


def fetch_futures_proxies() -> Dict[str, Any]:
    """Fetch ETF proxies for futures: SPY(ES), QQQ(NQ), USO(CL)."""
    results: Dict[str, Any] = {}
    mapping = {"SPY": "ES_proxy", "QQQ": "NQ_proxy", "USO": "CL_proxy"}
    for etf, label in mapping.items():
        q = fetch_stock_quote(etf)
        if q:
            results[label] = q
    return results


def fetch_yahoo_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch a quote from Yahoo Finance v8 API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    hdrs = {"User-Agent": "Mozilla/5.0 (GlobalSentinel Research)"}
    data = http_get(url, headers=hdrs, timeout=10)
    if not data:
        return None
    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        return {
            "symbol": symbol,
            "price": meta.get("regularMarketPrice", 0),
            "previous_close": meta.get("chartPreviousClose", meta.get("previousClose", 0)),
            "currency": meta.get("currency", ""),
            "exchange": meta.get("exchangeName", ""),
        }
    except (KeyError, IndexError, TypeError):
        return None


def fetch_asian_markets() -> Dict[str, Any]:
    """Fetch Nikkei, Hang Seng, Shanghai Composite from Yahoo Finance."""
    indices = {
        "^N225": "Nikkei_225",
        "^HSI": "Hang_Seng",
        "000001.SS": "Shanghai_Composite",
    }
    results: Dict[str, Any] = {}
    for yf_sym, label in indices.items():
        q = fetch_yahoo_quote(yf_sym)
        if q:
            price = q["price"]
            prev = q["previous_close"]
            chg_pct = ((price - prev) / prev * 100) if prev else 0
            results[label] = {
                "price": price,
                "previous_close": prev,
                "change_pct": round(chg_pct, 2),
            }
    return results


def fetch_oil_price() -> Optional[Dict[str, Any]]:
    """Fetch WTI crude oil price via Yahoo Finance (CL=F)."""
    q = fetch_yahoo_quote("CL=F")
    if q:
        price = q["price"]
        prev = q["previous_close"]
        chg_pct = ((price - prev) / prev * 100) if prev else 0
        return {
            "price": price,
            "previous_close": prev,
            "change_pct": round(chg_pct, 2),
            "friday_close_ref": 108.62,
        }
    return None


def fetch_gold_price() -> Optional[Dict[str, Any]]:
    """Fetch gold price via Yahoo Finance (GC=F)."""
    q = fetch_yahoo_quote("GC=F")
    if q:
        price = q["price"]
        prev = q["previous_close"]
        chg_pct = ((price - prev) / prev * 100) if prev else 0
        return {
            "price": price,
            "previous_close": prev,
            "change_pct": round(chg_pct, 2),
        }
    return None


def fetch_vix() -> Optional[Dict[str, Any]]:
    """Fetch VIX level from Yahoo Finance."""
    q = fetch_yahoo_quote("^VIX")
    if q:
        return {
            "level": q["price"],
            "previous_close": q["previous_close"],
        }
    return None


# ---------------------------------------------------------------------------
# Research cycle — gather all data
# ---------------------------------------------------------------------------
def run_research_cycle() -> Dict[str, Any]:
    """Execute one full research data-gathering cycle."""
    now_et = datetime.now(ET)
    print(f"\n{'='*60}")
    print(f"[RESEARCH CYCLE] {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
    print(f"{'='*60}")

    cycle: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "timestamp_et": now_et.isoformat(),
        "cycle_type": "overnight_research",
    }

    # 1) Crypto
    print("  Fetching crypto prices (BTC, ETH, SOL)...")
    cycle["crypto"] = fetch_crypto_prices()
    for sym, info in cycle["crypto"].items():
        print(f"    {sym}: ${info['mid']:,.2f}")

    # 2) Futures proxies
    print("  Fetching futures proxies (SPY, QQQ, USO)...")
    cycle["futures_proxies"] = fetch_futures_proxies()
    for label, info in cycle["futures_proxies"].items():
        print(f"    {label} ({info['symbol']}): ${info['mid']:.2f}")

    # 3) Asian markets
    print("  Fetching Asian market indices...")
    cycle["asian_markets"] = fetch_asian_markets()
    for label, info in cycle["asian_markets"].items():
        print(f"    {label}: {info['price']:,.2f} ({info['change_pct']:+.2f}%)")

    # 4) Oil (WTI)
    print("  Fetching WTI crude oil...")
    oil = fetch_oil_price()
    cycle["oil_wti"] = oil
    if oil:
        print(f"    WTI: ${oil['price']:.2f} ({oil['change_pct']:+.2f}% from prev close)")

    # 5) Gold
    print("  Fetching gold price...")
    gold = fetch_gold_price()
    cycle["gold"] = gold
    if gold:
        print(f"    Gold: ${gold['price']:.2f} ({gold['change_pct']:+.2f}%)")

    # 6) VIX
    print("  Fetching VIX...")
    vix = fetch_vix()
    cycle["vix"] = vix
    if vix:
        print(f"    VIX: {vix['level']:.2f}")

    # 7) Scenario-relevant quotes
    print("  Fetching scenario-relevant quotes...")
    scenario_syms = ["UCO", "GUSH", "JETS", "ITA", "GLD", "NUGT", "UVXY", "STNG", "FRO", "SQQQ", "MPC", "VLO"]
    cycle["scenario_quotes"] = {}
    for sym in scenario_syms:
        q = fetch_stock_quote(sym)
        if q:
            cycle["scenario_quotes"][sym] = q
            print(f"    {sym}: ${q['mid']:.2f}")

    return cycle


# ---------------------------------------------------------------------------
# Scenario engine
# ---------------------------------------------------------------------------
CAPITAL = 125.0

SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "A",
        "name": "Opening Range Breakout on Oil Plays",
        "vehicle": "UCO or GUSH (3x leveraged oil ETF)",
        "thesis": "If WTI opens +2% from Friday close ($108.62), UCO could gap +6-8%. Buy market order at 9:31 AM.",
        "entry": "Market order at 9:31 AM",
        "stop_loss_pct": -5.0,
        "stop_loss_dollar": 6.25,
        "target_pct_range": [15, 25],
        "leverage": 3,
        "data_key": "oil_wti",
        "signal_field": "change_pct",
        "signal_threshold": 2.0,
    },
    {
        "id": "B",
        "name": "Short Airlines at Open",
        "vehicle": "JETS puts (1 contract ~$1.25 premium)",
        "thesis": "Airlines crushed by fuel costs (jet fuel = 30% of airline costs). Buy 1 JETS put if oil stays elevated.",
        "entry": "Buy 1 JETS put at open",
        "stop_loss_pct": -100.0,
        "stop_loss_dollar": 1.25,
        "target_pct_range": [50, 150],
        "leverage": 1,
        "data_key": "oil_wti",
        "signal_field": "change_pct",
        "signal_threshold": 1.0,
    },
    {
        "id": "C",
        "name": "Defense Sector Gap Up",
        "vehicle": "ITA or PPA (defense ETFs)",
        "thesis": "$1.5T defense budget + ongoing war = strong tailwind. Target 2-3% gap up, ride momentum.",
        "entry": "Market order at open",
        "stop_loss_pct": -3.0,
        "stop_loss_dollar": 3.75,
        "target_pct_range": [2, 5],
        "leverage": 1,
        "data_key": None,
        "signal_field": None,
        "signal_threshold": None,
    },
    {
        "id": "D",
        "name": "Gold Safe Haven Momentum",
        "vehicle": "GLD calls or NUGT (3x gold miners)",
        "thesis": "Gold at ~$5,123 trending higher on geopolitical risk. Buy 1 near-the-money GLD call.",
        "entry": "Buy 1 GLD call at open",
        "stop_loss_pct": -100.0,
        "stop_loss_dollar": 1.50,
        "target_pct_range": [30, 100],
        "leverage": 1,
        "data_key": "gold",
        "signal_field": "change_pct",
        "signal_threshold": 0.3,
    },
    {
        "id": "E",
        "name": "VIX Spike Play",
        "vehicle": "UVXY or VXX (3x VIX)",
        "thesis": "VIX likely to spike Monday with oil crisis + CPI Wednesday fear. 3x leverage means 10-15% moves possible.",
        "entry": "Market order at open",
        "stop_loss_pct": -8.0,
        "stop_loss_dollar": 10.0,
        "target_pct_range": [10, 25],
        "leverage": 3,
        "data_key": "vix",
        "signal_field": "level",
        "signal_threshold": 20.0,
    },
    {
        "id": "F",
        "name": "Tanker Stock Breakout",
        "vehicle": "STNG, FRO, or ZIM shares",
        "thesis": "VLCC rates at all-time highs ($423K/day). Strong pre-market likely, ride opening momentum.",
        "entry": "Market order at open",
        "stop_loss_pct": -5.0,
        "stop_loss_dollar": 6.25,
        "target_pct_range": [5, 15],
        "leverage": 1,
        "data_key": "oil_wti",
        "signal_field": "change_pct",
        "signal_threshold": 0.5,
    },
    {
        "id": "G",
        "name": "Short Tech / QQQ",
        "vehicle": "SQQQ (3x inverse Nasdaq)",
        "thesis": "Tech getting hit by oil-driven inflation fears. Nasdaq futures -2.14% Sunday night.",
        "entry": "Market order at open",
        "stop_loss_pct": -5.0,
        "stop_loss_dollar": 6.25,
        "target_pct_range": [5, 15],
        "leverage": 3,
        "data_key": "futures_proxies",
        "signal_field": "NQ_proxy",
        "signal_threshold": None,
    },
    {
        "id": "H",
        "name": "Refinery Crack Spread Play",
        "vehicle": "MPC, VLO, or CRAK ETF",
        "thesis": "Refiners benefit when crude rises (wider crack spreads). Less volatile than crude, steadier upside.",
        "entry": "Market order at open",
        "stop_loss_pct": -4.0,
        "stop_loss_dollar": 5.0,
        "target_pct_range": [3, 8],
        "leverage": 1,
        "data_key": "oil_wti",
        "signal_field": "change_pct",
        "signal_threshold": 1.0,
    },
]


def score_scenario(scenario: Dict[str, Any], cycle_data: Dict[str, Any]) -> Dict[str, Any]:
    """Score a single scenario based on current market data.

    Returns a dict with probability, expected returns, EV, kelly, risk/reward.
    """
    s = dict(scenario)
    base_prob = 0.40  # prior probability
    signal_boost = 0.0

    # Check if the relevant data signal is present and strong
    data_key = s.get("data_key")
    signal_field = s.get("signal_field")
    signal_threshold = s.get("signal_threshold")

    signal_value = None
    if data_key and signal_field:
        if data_key == "futures_proxies":
            # Special handling: check NQ proxy direction
            fp = cycle_data.get("futures_proxies", {})
            nq = fp.get("NQ_proxy")
            if nq:
                signal_value = nq.get("mid", 0)
                # For short tech, we want NQ to be down — hard to tell from
                # mid price alone so give moderate boost based on thesis
                signal_boost = 0.10
        else:
            source = cycle_data.get(data_key)
            if source and isinstance(source, dict):
                signal_value = source.get(signal_field)
                if signal_value is not None and signal_threshold is not None:
                    if signal_field == "level":
                        # VIX: higher = better for our play
                        if signal_value >= signal_threshold:
                            signal_boost = min(0.25, (signal_value - signal_threshold) / signal_threshold * 0.5)
                        else:
                            signal_boost = -0.10
                    else:
                        # change_pct: positive = bullish signal
                        if signal_value >= signal_threshold:
                            signal_boost = min(0.30, (signal_value / signal_threshold - 1) * 0.15 + 0.15)
                        elif signal_value > 0:
                            signal_boost = 0.05
                        else:
                            signal_boost = -0.15

    # Asian market sentiment adjustment
    asian = cycle_data.get("asian_markets", {})
    asian_neg_count = sum(1 for v in asian.values() if isinstance(v, dict) and v.get("change_pct", 0) < -1)
    if asian_neg_count >= 2:
        # Global risk-off: boosts VIX play, gold, short tech; hurts defense/tanker less
        if s["id"] in ("E", "D", "G"):
            signal_boost += 0.08
        elif s["id"] in ("C", "F"):
            signal_boost -= 0.05

    # Gold trending check for gold scenario
    if s["id"] == "D":
        gold = cycle_data.get("gold")
        if gold and gold.get("price", 0) > 5100:
            signal_boost += 0.05

    probability = max(0.05, min(0.85, base_prob + signal_boost))

    # Returns
    lo_target, hi_target = s["target_pct_range"]
    best_case_return = hi_target / 100.0
    base_case_return = lo_target / 100.0
    worst_case_return = s["stop_loss_pct"] / 100.0

    # Dollar amounts
    best_dollar = CAPITAL * best_case_return
    base_dollar = CAPITAL * base_case_return
    worst_dollar = s["stop_loss_dollar"]

    # Expected value = P(win)*avg_win - P(lose)*loss
    avg_return = (best_case_return + base_case_return) / 2
    ev_dollar = probability * (CAPITAL * avg_return) - (1 - probability) * worst_dollar
    ev_pct = ev_dollar / CAPITAL * 100

    # Risk/reward ratio
    avg_gain = (best_dollar + base_dollar) / 2
    risk_reward = avg_gain / worst_dollar if worst_dollar > 0 else 999

    # Kelly criterion: f* = (bp - q) / b where b=avg win/loss ratio, p=prob, q=1-p
    b = avg_gain / worst_dollar if worst_dollar > 0 else 1
    q = 1 - probability
    kelly_fraction = max(0, (b * probability - q) / b) if b > 0 else 0
    kelly_bet = CAPITAL * kelly_fraction

    return {
        "scenario_id": s["id"],
        "name": s["name"],
        "vehicle": s["vehicle"],
        "thesis": s["thesis"],
        "entry": s["entry"],
        "signal_value": signal_value,
        "signal_threshold": signal_threshold,
        "signal_boost": round(signal_boost, 3),
        "probability": round(probability, 3),
        "returns": {
            "best_case_pct": hi_target,
            "base_case_pct": lo_target,
            "worst_case_pct": s["stop_loss_pct"],
            "best_case_dollar": round(best_dollar, 2),
            "base_case_dollar": round(base_dollar, 2),
            "worst_case_dollar": round(-worst_dollar, 2),
        },
        "expected_value_dollar": round(ev_dollar, 2),
        "expected_value_pct": round(ev_pct, 2),
        "risk_reward_ratio": round(risk_reward, 2),
        "kelly_fraction": round(kelly_fraction, 3),
        "kelly_optimal_bet": round(kelly_bet, 2),
        "stop_loss_dollar": round(worst_dollar, 2),
        "max_loss": round(worst_dollar, 2),
    }


def rank_scenarios(cycle_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Score and rank all scenarios by expected value."""
    scored = [score_scenario(s, cycle_data) for s in SCENARIOS]
    scored.sort(key=lambda x: x["expected_value_dollar"], reverse=True)
    for rank, s in enumerate(scored, 1):
        s["rank"] = rank
    return scored


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def append_research_log(cycle_data: Dict[str, Any], scored: List[Dict[str, Any]]) -> None:
    """Append cycle data + scored scenarios to JSONL log."""
    RESEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "cycle": cycle_data,
        "scenarios_ranked": scored,
    }
    with open(RESEARCH_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(f"  [LOG] Appended to {RESEARCH_LOG}")


def write_trade_plan(scored: List[Dict[str, Any]], cycle_data: Dict[str, Any]) -> None:
    """Write current best scenarios to morning_trade_plan.json."""
    TRADE_PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "generated_et": datetime.now(ET).isoformat(),
        "capital": CAPITAL,
        "market_snapshot": {
            "oil_wti": cycle_data.get("oil_wti"),
            "gold": cycle_data.get("gold"),
            "vix": cycle_data.get("vix"),
            "crypto": cycle_data.get("crypto"),
            "asian_markets": cycle_data.get("asian_markets"),
        },
        "top_3_picks": scored[:3],
        "all_scenarios": scored,
    }
    with open(TRADE_PLAN_PATH, "w") as f:
        json.dump(plan, f, indent=2, default=str)
    print(f"  [PLAN] Written to {TRADE_PLAN_PATH}")


def write_final_report(scored: List[Dict[str, Any]], cycle_data: Dict[str, Any]) -> None:
    """Write final morning picks report."""
    FINAL_PICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "report_type": "final_morning_picks",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "generated_et": datetime.now(ET).isoformat(),
        "capital": CAPITAL,
        "recommendation": scored[0]["name"] if scored else "No scenarios scored",
        "top_pick": scored[0] if scored else None,
        "runner_up": scored[1] if len(scored) > 1 else None,
        "market_snapshot": {
            "oil_wti": cycle_data.get("oil_wti"),
            "gold": cycle_data.get("gold"),
            "vix": cycle_data.get("vix"),
            "crypto": cycle_data.get("crypto"),
            "asian_markets": cycle_data.get("asian_markets"),
        },
        "all_scenarios_ranked": scored,
    }
    with open(FINAL_PICKS_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  [FINAL] Written to {FINAL_PICKS_PATH}")


def format_telegram_message(scored: List[Dict[str, Any]], cycle_data: Dict[str, Any]) -> str:
    """Format a compact Telegram message with top picks."""
    now_et = datetime.now(ET)
    lines = [
        f"OVERNIGHT RESEARCH ENGINE — FINAL PICKS",
        f"{now_et.strftime('%Y-%m-%d %H:%M ET')}",
        f"Capital: ${CAPITAL:.0f}",
        "",
    ]

    # Market snapshot
    oil = cycle_data.get("oil_wti")
    gold = cycle_data.get("gold")
    vix = cycle_data.get("vix")
    if oil:
        lines.append(f"WTI Oil: ${oil['price']:.2f} ({oil['change_pct']:+.2f}%)")
    if gold:
        lines.append(f"Gold: ${gold['price']:.2f} ({gold['change_pct']:+.2f}%)")
    if vix:
        lines.append(f"VIX: {vix['level']:.2f}")

    crypto = cycle_data.get("crypto", {})
    for sym in ("BTC/USD", "ETH/USD", "SOL/USD"):
        c = crypto.get(sym)
        if c:
            lines.append(f"{sym}: ${c['mid']:,.2f}")

    lines.append("")
    lines.append("--- TOP 3 SCENARIOS ---")

    for i, s in enumerate(scored[:3], 1):
        lines.append(
            f"\n#{i} [{s['scenario_id']}] {s['name']}"
            f"\n  Vehicle: {s['vehicle']}"
            f"\n  Prob: {s['probability']*100:.0f}% | EV: ${s['expected_value_dollar']:+.2f} ({s['expected_value_pct']:+.1f}%)"
            f"\n  R/R: {s['risk_reward_ratio']:.1f}x | Kelly: ${s['kelly_optimal_bet']:.0f}"
            f"\n  Max Loss: ${s['stop_loss_dollar']:.2f}"
        )

    lines.append("")
    lines.append("--- FULL RANKING ---")
    for s in scored:
        lines.append(
            f"  {s['rank']}. [{s['scenario_id']}] {s['name']}: EV ${s['expected_value_dollar']:+.2f}"
        )

    return "\n".join(lines)


def send_telegram_final(scored: List[Dict[str, Any]], cycle_data: Dict[str, Any]) -> None:
    """Send final picks via TelegramTopicNotifier."""
    try:
        from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier
        notifier = TelegramTopicNotifier(topic="v6_digest")
        msg = format_telegram_message(scored, cycle_data)
        result = notifier.send_message(msg)
        if result.ok:
            print("  [TELEGRAM] Final picks sent successfully")
        else:
            print(f"  [TELEGRAM] Send failed: {result.reason}")
    except Exception as exc:
        print(f"  [TELEGRAM] Error: {exc}")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def print_rankings(scored: List[Dict[str, Any]]) -> None:
    """Print ranked scenarios to stdout."""
    print(f"\n{'='*60}")
    print(f"  SCENARIO RANKINGS (Capital: ${CAPITAL})")
    print(f"{'='*60}")
    for s in scored:
        marker = " <<<" if s["rank"] == 1 else ""
        print(
            f"  #{s['rank']} [{s['scenario_id']}] {s['name']}{marker}\n"
            f"      Vehicle: {s['vehicle']}\n"
            f"      Prob: {s['probability']*100:.0f}% | "
            f"EV: ${s['expected_value_dollar']:+.2f} ({s['expected_value_pct']:+.1f}%) | "
            f"R/R: {s['risk_reward_ratio']:.1f}x\n"
            f"      Kelly: {s['kelly_fraction']*100:.1f}% (${s['kelly_optimal_bet']:.0f}) | "
            f"Max Loss: ${s['stop_loss_dollar']:.2f}\n"
            f"      Returns: [{s['returns']['worst_case_pct']}% / "
            f"+{s['returns']['base_case_pct']}% / +{s['returns']['best_case_pct']}%]\n"
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    DEADLINE_ET = datetime(2026, 3, 10, 8, 30, 0, tzinfo=ET)
    CYCLE_INTERVAL_SEC = 30 * 60  # 30 minutes

    print("=" * 60)
    print(" OVERNIGHT RESEARCH ENGINE")
    print(f" Started: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")
    print(f" Deadline: {DEADLINE_ET.strftime('%Y-%m-%d %H:%M:%S ET')}")
    print(f" Capital: ${CAPITAL:.0f}")
    print(f" Cycle interval: {CYCLE_INTERVAL_SEC // 60} minutes")
    print(f" Scenarios: {len(SCENARIOS)}")
    print(f" Alpaca key: {ALPACA_API_KEY[:8]}..." if ALPACA_API_KEY else " [WARN] No Alpaca API key!")
    print("=" * 60)

    cycle_count = 0
    last_scored: List[Dict[str, Any]] = []
    last_cycle_data: Dict[str, Any] = {}

    while True:
        now_et = datetime.now(ET)
        remaining = (DEADLINE_ET - now_et).total_seconds()

        if remaining <= 0:
            print(f"\n[DEADLINE REACHED] {now_et.strftime('%H:%M:%S ET')} — generating final report.")
            break

        cycle_count += 1
        print(f"\n[CYCLE {cycle_count}] Time remaining: {remaining/60:.0f} minutes")

        try:
            cycle_data = run_research_cycle()
            scored = rank_scenarios(cycle_data)

            print_rankings(scored)
            append_research_log(cycle_data, scored)
            write_trade_plan(scored, cycle_data)

            last_scored = scored
            last_cycle_data = cycle_data

        except Exception:
            print(f"  [ERROR] Cycle failed:")
            traceback.print_exc()

        # Sleep until next cycle (or until deadline)
        now_et = datetime.now(ET)
        remaining = (DEADLINE_ET - now_et).total_seconds()
        if remaining <= 0:
            break

        sleep_secs = min(CYCLE_INTERVAL_SEC, remaining)
        next_run = now_et + timedelta(seconds=sleep_secs)
        print(f"\n  Sleeping {sleep_secs/60:.1f} min — next cycle at {next_run.strftime('%H:%M:%S ET')}")
        time.sleep(sleep_secs)

    # Final report
    if not last_scored:
        print("[FINAL] No data collected — running one last cycle...")
        try:
            last_cycle_data = run_research_cycle()
            last_scored = rank_scenarios(last_cycle_data)
            print_rankings(last_scored)
        except Exception:
            print("[FINAL] Last cycle also failed.")
            traceback.print_exc()

    if last_scored:
        write_final_report(last_scored, last_cycle_data)
        append_research_log(last_cycle_data, last_scored)
        send_telegram_final(last_scored, last_cycle_data)

        print("\n" + "=" * 60)
        print(" FINAL RECOMMENDATION")
        print("=" * 60)
        top = last_scored[0]
        print(f"  TOP PICK: [{top['scenario_id']}] {top['name']}")
        print(f"  Vehicle: {top['vehicle']}")
        print(f"  EV: ${top['expected_value_dollar']:+.2f} | Prob: {top['probability']*100:.0f}%")
        print(f"  Entry: {top['entry']}")
        print(f"  Kelly optimal: ${top['kelly_optimal_bet']:.0f} of ${CAPITAL}")
        print("=" * 60)

    print(f"\n[DONE] Engine stopped at {datetime.now(ET).strftime('%H:%M:%S ET')}")


if __name__ == "__main__":
    main()
