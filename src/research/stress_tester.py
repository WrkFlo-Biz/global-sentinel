#!/usr/bin/env python3
"""Historical Stress Testing Engine for Global Sentinel.

Applies 8 historical crisis scenarios to current portfolio positions
to estimate potential drawdowns and tail risk.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

ACCOUNTS = {
    "daytrade": {
        "api_key": os.getenv("ALPACA_API_KEY_DAYTRADE", ""),
        "secret_key": os.getenv("ALPACA_SECRET_KEY_DAYTRADE", ""),
        "base_url": os.getenv("ALPACA_BASE_URL_DAYTRADE", "https://paper-api.alpaca.markets"),
    },
    "medlong": {
        "api_key": os.getenv("ALPACA_API_KEY_MEDLONG", ""),
        "secret_key": os.getenv("ALPACA_SECRET_KEY_MEDLONG", ""),
        "base_url": os.getenv("ALPACA_BASE_URL_MEDLONG", "https://paper-api.alpaca.markets"),
    },
}

OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "stress_test_results.json"

# ── Historical Crisis Scenarios ────────────────────────────────────────
# Each scenario defines sector/factor shocks as percentage moves.
# Positions are mapped to the closest proxy to estimate impact.

CRISIS_SCENARIOS = {
    "2008_gfc": {
        "name": "2008 Global Financial Crisis",
        "description": "Credit crisis, Lehman collapse. Equities -38%, financials -55%, VIX +300%",
        "duration": "Sep 2008 - Mar 2009 (6 months)",
        "shocks": {
            # Sector ETF proxies for shock magnitudes
            "SPY": -0.38, "QQQ": -0.42, "IWM": -0.40,
            "XLF": -0.55, "XLK": -0.43, "XLE": -0.45,
            "XLV": -0.25, "XLP": -0.18, "XLU": -0.28,
            "XLI": -0.40, "XLB": -0.38, "XLRE": -0.42,
            "XLC": -0.35, "XLY": -0.45, "TLT": 0.20,
            "GLD": 0.05, "VIX": 3.00,
            "_default_equity": -0.38,
        },
    },
    "2010_flash_crash": {
        "name": "2010 Flash Crash",
        "description": "Intraday crash: SPY -9% in minutes, rapid recovery",
        "duration": "May 6, 2010 (intraday)",
        "shocks": {
            "SPY": -0.09, "QQQ": -0.10, "IWM": -0.12,
            "XLF": -0.10, "XLK": -0.11, "XLE": -0.08,
            "XLV": -0.07, "XLP": -0.05, "XLU": -0.06,
            "XLI": -0.09, "XLB": -0.08,
            "_default_equity": -0.09,
        },
    },
    "2020_covid": {
        "name": "2020 COVID-19 Crash",
        "description": "Pandemic sell-off: SPY -34% in 23 days, VIX hit 82",
        "duration": "Feb 19 - Mar 23, 2020 (23 trading days)",
        "shocks": {
            "SPY": -0.34, "QQQ": -0.28, "IWM": -0.42,
            "XLF": -0.40, "XLK": -0.27, "XLE": -0.58,
            "XLV": -0.28, "XLP": -0.18, "XLU": -0.30,
            "XLI": -0.40, "XLB": -0.30, "XLRE": -0.32,
            "XLC": -0.25, "XLY": -0.38, "TLT": 0.15,
            "GLD": -0.03, "VIX": 4.50,
            "_default_equity": -0.34,
        },
    },
    "2022_rate_shock": {
        "name": "2022 Rate Shock / Tech Crash",
        "description": "Fed aggressive hiking: QQQ -33%, TLT -31%, growth crushed",
        "duration": "Jan - Oct 2022 (10 months)",
        "shocks": {
            "SPY": -0.25, "QQQ": -0.33, "IWM": -0.28,
            "XLF": -0.12, "XLK": -0.32, "XLE": 0.55,
            "XLV": -0.10, "XLP": -0.05, "XLU": -0.02,
            "XLI": -0.15, "XLB": -0.18, "XLRE": -0.30,
            "XLC": -0.38, "XLY": -0.35, "TLT": -0.31,
            "GLD": -0.05, "VIX": 1.50,
            "_default_equity": -0.25,
            "_default_growth": -0.33,
        },
    },
    "2022_crypto_winter": {
        "name": "2022 Crypto Winter",
        "description": "Luna/FTX collapse: BTC -65%, crypto stocks crushed",
        "duration": "Apr - Dec 2022 (9 months)",
        "shocks": {
            "BTC": -0.65, "ETH": -0.70, "COIN": -0.86,
            "MARA": -0.80, "RIOT": -0.80, "MSTR": -0.72,
            "SPY": -0.10, "QQQ": -0.15,
            "XLF": -0.08, "XLK": -0.15,
            "_default_equity": -0.10,
            "_default_crypto": -0.65,
        },
    },
    "1991_gulf_war": {
        "name": "1991 Gulf War",
        "description": "Oil +130% then -60%, SPY -20% then +30%",
        "duration": "Aug 1990 - Feb 1991 (7 months)",
        "shocks": {
            "SPY": -0.20, "QQQ": -0.18, "IWM": -0.22,
            "XLE": 0.30, "XLF": -0.18, "XLK": -0.15,
            "XLV": -0.10, "XLP": -0.08, "XLU": -0.12,
            "XLI": -0.22, "GLD": 0.10,
            "USO": 0.50, "TLT": 0.08,
            "_default_equity": -0.20,
            "_default_energy": 0.30,
        },
    },
    "2018_trade_war": {
        "name": "2018 Q4 Trade War Sell-off",
        "description": "US-China trade war escalation, SPY -20% Q4",
        "duration": "Oct - Dec 2018 (3 months)",
        "shocks": {
            "SPY": -0.20, "QQQ": -0.23, "IWM": -0.27,
            "XLF": -0.18, "XLK": -0.22, "XLE": -0.25,
            "XLV": -0.15, "XLP": -0.08, "XLU": -0.02,
            "XLI": -0.22, "XLB": -0.18, "XLRE": -0.08,
            "XLC": -0.20, "XLY": -0.25, "TLT": 0.05,
            "GLD": 0.04,
            "_default_equity": -0.20,
        },
    },
    "2023_bank_crisis": {
        "name": "2023 Regional Bank Crisis",
        "description": "SVB/Signature collapse: KRE -35%, flight to quality",
        "duration": "Mar 2023 (2 weeks)",
        "shocks": {
            "SPY": -0.05, "QQQ": -0.02, "IWM": -0.10,
            "XLF": -0.18, "KRE": -0.35, "XLK": 0.02,
            "XLE": -0.08, "XLV": -0.05, "XLP": -0.02,
            "XLU": -0.03, "XLI": -0.06, "XLRE": -0.12,
            "TLT": 0.08, "GLD": 0.10,
            "_default_equity": -0.05,
            "_default_financial": -0.18,
        },
    },
}

# ── Sector classification for mapping unknown tickers ──────────────────
SECTOR_MAP = {
    "XLK": "technology", "XLF": "financial", "XLE": "energy",
    "XLV": "healthcare", "XLP": "consumer_staples", "XLU": "utilities",
    "XLI": "industrials", "XLB": "materials", "XLRE": "real_estate",
    "XLC": "communication", "XLY": "consumer_discretionary",
}


def _alpaca_headers(acct: dict) -> dict:
    return {
        "APCA-API-KEY-ID": acct["api_key"],
        "APCA-API-SECRET-KEY": acct["secret_key"],
    }


def get_positions(acct: dict) -> List[Dict[str, Any]]:
    url = f"{acct['base_url']}/v2/positions"
    resp = requests.get(url, headers=_alpaca_headers(acct), timeout=15)
    resp.raise_for_status()
    return [
        {
            "symbol": p["symbol"],
            "qty": float(p["qty"]),
            "market_value": float(p["market_value"]),
            "current_price": float(p.get("current_price", 0)),
            "side": p.get("side", "long"),
        }
        for p in resp.json()
    ]


def get_account_equity(acct: dict) -> float:
    url = f"{acct['base_url']}/v2/account"
    resp = requests.get(url, headers=_alpaca_headers(acct), timeout=15)
    resp.raise_for_status()
    return float(resp.json().get("equity", 0))


def _get_sector(symbol: str) -> Optional[str]:
    """Try to determine sector for a symbol via yfinance."""
    try:
        info = yf.Ticker(symbol).info
        sector = info.get("sector", "").lower()
        if "tech" in sector:
            return "technology"
        if "financ" in sector:
            return "financial"
        if "energy" in sector:
            return "energy"
        if "health" in sector:
            return "healthcare"
        if "consumer" in sector and "discret" in sector:
            return "consumer_discretionary"
        if "consumer" in sector and "staple" in sector:
            return "consumer_staples"
        if "utilit" in sector:
            return "utilities"
        if "industr" in sector:
            return "industrials"
        if "material" in sector:
            return "materials"
        if "real" in sector:
            return "real_estate"
        if "communicat" in sector:
            return "communication"
        return None
    except Exception:
        return None


def _resolve_shock(symbol: str, shocks: dict) -> float:
    """Find the best shock estimate for a symbol given scenario shocks."""
    # Direct match
    if symbol in shocks:
        return shocks[symbol]

    # Try sector-based mapping
    sector = _get_sector(symbol)
    sector_to_etf = {v: k for k, v in SECTOR_MAP.items()}
    if sector and sector_to_etf.get(sector) in shocks:
        return shocks[sector_to_etf[sector]]

    # Check special defaults
    for key in ["_default_equity", "_default_growth", "_default_financial"]:
        if key in shocks:
            return shocks[key]

    return shocks.get("_default_equity", -0.10)


def stress_test_portfolio(
    positions: List[Dict[str, Any]], equity: float, scenario_name: str, scenario: dict
) -> Dict[str, Any]:
    """Apply a single stress scenario to a portfolio."""
    shocks = scenario["shocks"]
    position_impacts = []
    total_pnl = 0.0

    for pos in positions:
        sym = pos["symbol"]
        mv = pos["market_value"]
        shock = _resolve_shock(sym, shocks)

        # For short positions, the shock impact is inverted
        if pos.get("side") == "short":
            pnl = -mv * shock
        else:
            pnl = mv * shock

        total_pnl += pnl
        position_impacts.append({
            "symbol": sym,
            "market_value": round(mv, 2),
            "shock_applied": round(shock * 100, 2),
            "estimated_pnl": round(pnl, 2),
            "pct_of_position": round(shock * 100, 2),
        })

    # Sort by worst impact
    position_impacts.sort(key=lambda x: x["estimated_pnl"])

    drawdown_pct = (total_pnl / equity * 100) if equity > 0 else 0

    return {
        "scenario": scenario_name,
        "scenario_name": scenario["name"],
        "description": scenario["description"],
        "duration": scenario["duration"],
        "portfolio_equity": round(equity, 2),
        "estimated_total_pnl": round(total_pnl, 2),
        "estimated_drawdown_pct": round(drawdown_pct, 2),
        "surviving_equity": round(equity + total_pnl, 2),
        "position_impacts": position_impacts,
        "worst_hit_position": position_impacts[0]["symbol"] if position_impacts else None,
        "worst_hit_pnl": position_impacts[0]["estimated_pnl"] if position_impacts else 0,
    }


def analyze_account(account_name: str, acct_config: dict) -> Dict[str, Any]:
    """Run all stress scenarios on one account."""
    logger.info(f"Stress testing account: {account_name}")

    try:
        equity = get_account_equity(acct_config)
        positions = get_positions(acct_config)
    except Exception as e:
        return {"account": account_name, "error": str(e)}

    if not positions:
        return {
            "account": account_name,
            "equity": equity,
            "message": "No positions — nothing to stress test",
            "scenarios": {},
        }

    scenarios_results = {}
    for sc_key, sc_def in CRISIS_SCENARIOS.items():
        scenarios_results[sc_key] = stress_test_portfolio(positions, equity, sc_key, sc_def)

    # Find worst scenario
    worst = min(scenarios_results.values(), key=lambda s: s["estimated_total_pnl"])

    # Tail risk: positions appearing in bottom-3 across multiple scenarios
    tail_risk_counts: Dict[str, int] = {}
    for sc in scenarios_results.values():
        for imp in sc["position_impacts"][:3]:
            tail_risk_counts[imp["symbol"]] = tail_risk_counts.get(imp["symbol"], 0) + 1
    tail_risk_sorted = sorted(tail_risk_counts.items(), key=lambda x: -x[1])

    return {
        "account": account_name,
        "equity": round(equity, 2),
        "num_positions": len(positions),
        "scenarios": scenarios_results,
        "worst_scenario": {
            "name": worst["scenario_name"],
            "key": worst["scenario"],
            "estimated_pnl": worst["estimated_total_pnl"],
            "drawdown_pct": worst["estimated_drawdown_pct"],
        },
        "max_drawdown_across_all": round(
            min(s["estimated_drawdown_pct"] for s in scenarios_results.values()), 2
        ),
        "tail_risk_positions": [
            {"symbol": sym, "appearances_in_bottom3": cnt}
            for sym, cnt in tail_risk_sorted[:5]
        ],
    }


def run() -> Dict[str, Any]:
    """Execute stress tests for all accounts."""
    timestamp = datetime.now(timezone.utc).isoformat()
    output = {
        "source": "stress_tester",
        "timestamp_utc": timestamp,
        "num_scenarios": len(CRISIS_SCENARIOS),
        "scenarios_list": list(CRISIS_SCENARIOS.keys()),
        "accounts": {},
    }

    for name, config in ACCOUNTS.items():
        if not config["api_key"]:
            continue
        try:
            output["accounts"][name] = analyze_account(name, config)
        except Exception as e:
            logger.error(f"Error stress testing {name}: {e}")
            output["accounts"][name] = {"account": name, "error": str(e)}

    # Cross-account summary
    all_worst_pnl = []
    for acct in output["accounts"].values():
        ws = acct.get("worst_scenario", {})
        if ws.get("estimated_pnl") is not None:
            all_worst_pnl.append(ws["estimated_pnl"])

    output["global_summary"] = {
        "combined_worst_case_pnl": round(sum(all_worst_pnl), 2) if all_worst_pnl else 0,
        "worst_single_account_pnl": round(min(all_worst_pnl), 2) if all_worst_pnl else 0,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"Stress test results written to {OUTPUT_PATH}")
    return output


if __name__ == "__main__":
    results = run()
    for acct_name, acct_data in results.get("accounts", {}).items():
        if "error" in acct_data:
            print(f"  {acct_name}: ERROR - {acct_data.get('error')}")
        else:
            ws = acct_data.get("worst_scenario", {})
            eq = acct_data.get('equity', 0)
            sn = ws.get('name', '')
            sp = ws.get('estimated_pnl', 0)
            sd = ws.get('drawdown_pct', 0)
            print(f"  {acct_name}: Equity=${eq:,.0f} | Worst: {sn} -> ${sp:,.0f} ({sd:.1f}%)")
    print(json.dumps(results.get("global_summary", {}), indent=2))
