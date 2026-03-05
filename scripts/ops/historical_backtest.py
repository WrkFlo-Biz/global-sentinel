#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Historical Backtest

Replays historical crisis scenarios through the regime scorer to validate
detection timing and threshold calibration.

Uses Yahoo Finance for historical vol data and simulated disruption events.

Usage:
    python3 scripts/ops/historical_backtest.py --repo-root .
    python3 scripts/ops/historical_backtest.py --repo-root . --scenario covid_march_2020
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_yahoo_history(symbol: str, period1: int, period2: int) -> Optional[Dict]:
    """Fetch historical daily OHLCV from Yahoo Finance."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&period1={period1}&period2={period2}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel-Backtest/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


def compute_rolling_vol(closes: List[float], window: int = 20) -> List[float]:
    """Compute rolling realized volatility (daily, %)."""
    if len(closes) < window + 1:
        return []
    vols = []
    for i in range(window, len(closes)):
        sub = closes[i - window:i + 1]
        log_rets = []
        for j in range(1, len(sub)):
            if sub[j] > 0 and sub[j - 1] > 0:
                log_rets.append(math.log(sub[j] / sub[j - 1]))
        if len(log_rets) >= 2:
            mean = sum(log_rets) / len(log_rets)
            var = sum((r - mean) ** 2 for r in log_rets) / (len(log_rets) - 1)
            vols.append(math.sqrt(var) * 100.0)
        else:
            vols.append(0.0)
    return vols


# --- Historical Crisis Scenarios ---
SCENARIOS = {
    "covid_march_2020": {
        "name": "COVID-19 Market Crash",
        "date_range": ("2020-02-01", "2020-04-15"),
        "symbols": ["SPY", "DAL", "UAL", "AAL", "BA", "HYG", "EEM"],
        "expected_crisis_date": "2020-03-09",
        "description": "Travel ban, lockdowns, airline industry collapse",
        "simulated_disruptions": [
            {"date": "2020-03-11", "severity": "high", "title": "WHO declares COVID-19 pandemic"},
            {"date": "2020-03-12", "severity": "high", "title": "US-Europe travel ban announced"},
            {"date": "2020-03-13", "severity": "high", "title": "National emergency declared"},
            {"date": "2020-03-15", "severity": "high", "title": "Fed emergency rate cut to 0%"},
            {"date": "2020-03-16", "severity": "high", "title": "Circuit breaker triggered - markets limit down"},
        ],
    },
    "ukraine_invasion_2022": {
        "name": "Russia-Ukraine War",
        "date_range": ("2022-02-01", "2022-04-01"),
        "symbols": ["SPY", "EEM", "USO", "GLD", "DAL", "HYG"],
        "expected_crisis_date": "2022-02-24",
        "description": "Russian invasion of Ukraine, energy shock, sanctions",
        "simulated_disruptions": [
            {"date": "2022-02-22", "severity": "high", "title": "Russia recognizes Donbas republics"},
            {"date": "2022-02-24", "severity": "high", "title": "Russia invades Ukraine - full-scale war"},
            {"date": "2022-02-25", "severity": "high", "title": "Airspace closures across Eastern Europe"},
            {"date": "2022-02-26", "severity": "high", "title": "SWIFT sanctions on Russian banks"},
            {"date": "2022-03-08", "severity": "high", "title": "Oil spikes above $130/barrel"},
        ],
    },
    "svb_collapse_2023": {
        "name": "SVB Bank Collapse",
        "date_range": ("2023-03-01", "2023-04-15"),
        "symbols": ["SPY", "KRE", "HYG", "LQD", "GLD"],
        "expected_crisis_date": "2023-03-10",
        "description": "Silicon Valley Bank collapse, banking contagion fears",
        "simulated_disruptions": [
            {"date": "2023-03-08", "severity": "medium", "title": "SVB announces $1.8B loss on bond portfolio"},
            {"date": "2023-03-09", "severity": "high", "title": "SVB stock crashes 60%, bank run begins"},
            {"date": "2023-03-10", "severity": "high", "title": "FDIC seizes SVB - largest bank failure since 2008"},
            {"date": "2023-03-12", "severity": "high", "title": "Signature Bank closed, contagion fears spread"},
            {"date": "2023-03-13", "severity": "medium", "title": "Fed announces emergency lending facility"},
        ],
    },
    "tariff_shock_2025": {
        "name": "2025 Tariff Escalation",
        "date_range": ("2025-01-15", "2025-03-01"),
        "symbols": ["SPY", "EEM", "FXI", "DAL", "UAL", "HYG"],
        "expected_crisis_date": "2025-02-04",
        "description": "Sweeping tariffs on China, Canada, Mexico",
        "simulated_disruptions": [
            {"date": "2025-02-01", "severity": "high", "title": "US announces 25% tariffs on Canada and Mexico"},
            {"date": "2025-02-04", "severity": "high", "title": "China retaliates with counter-tariffs"},
            {"date": "2025-02-10", "severity": "medium", "title": "Supply chain disruptions escalate"},
        ],
    },
}


class HistoricalBacktest:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.thresholds = yaml.safe_load(
            (repo_root / "config" / "thresholds.yaml").read_text(encoding="utf-8")
        )

    def run_scenario(self, scenario_key: str) -> Dict[str, Any]:
        scenario = SCENARIOS[scenario_key]
        print(f"\n{'=' * 70}")
        print(f"  BACKTEST: {scenario['name']}")
        print(f"  Period: {scenario['date_range'][0]} to {scenario['date_range'][1]}")
        print(f"  Expected crisis: {scenario['expected_crisis_date']}")
        print(f"{'=' * 70}\n")

        # Fetch historical data
        start_str, end_str = scenario["date_range"]
        start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        period1 = int(start_dt.timestamp())
        period2 = int(end_dt.timestamp())

        # Fetch vol data for each symbol
        symbol_data: Dict[str, Dict] = {}
        for sym in scenario["symbols"]:
            data = fetch_yahoo_history(sym, period1, period2)
            if data:
                try:
                    chart = data["chart"]["result"][0]
                    timestamps = chart.get("timestamp", [])
                    quotes = chart["indicators"]["quote"][0]
                    closes = quotes.get("close", [])
                    volumes = quotes.get("volume", [])
                    symbol_data[sym] = {
                        "timestamps": timestamps,
                        "closes": [c for c in closes if c is not None],
                        "volumes": [v for v in volumes if v is not None],
                    }
                except Exception:
                    pass

        if not symbol_data:
            return {"scenario": scenario_key, "error": "no_data_fetched", "timeline": []}

        # Build daily snapshots and score them
        from src.scoring.regime_shift import RegimeShiftScorer
        scorer = RegimeShiftScorer(self.thresholds)

        # Get the primary symbol (SPY) timestamps for the timeline
        primary = symbol_data.get("SPY", list(symbol_data.values())[0])
        timestamps = primary.get("timestamps", [])

        timeline = []
        mode = "NORMAL"
        mode_thresholds = self.thresholds.get("mode_thresholds", {})
        crisis_thresh = float(mode_thresholds.get("elevated_to_crisis", 0.75))
        elevated_thresh = float(mode_thresholds.get("normal_to_elevated", 0.45))

        for i in range(20, len(timestamps)):  # start after vol warmup
            ts = timestamps[i]
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

            # Build micro snapshot for this day
            micro = {}
            for sym, sd in symbol_data.items():
                closes = sd["closes"]
                volumes = sd["volumes"]
                if i < len(closes) and i >= 20:
                    window = closes[max(0, i - 20):i + 1]
                    if len(window) >= 2:
                        log_rets = []
                        for j in range(1, len(window)):
                            if window[j] > 0 and window[j - 1] > 0:
                                log_rets.append(math.log(window[j] / window[j - 1]))
                        if log_rets:
                            mean = sum(log_rets) / len(log_rets)
                            var = sum((r - mean) ** 2 for r in log_rets) / max(len(log_rets) - 1, 1)
                            sigma = math.sqrt(var) * 100.0
                        else:
                            sigma = 0
                        adv = sum(volumes[max(0, i - 20):i]) / max(min(i, 20), 1) if i < len(volumes) else 0
                        micro[sym] = {
                            "adv_shares": adv,
                            "sigma_daily_pct": round(sigma, 4),
                            "last_price": closes[i] if i < len(closes) else 0,
                        }

            # Check for simulated disruptions on this date
            disruptions = [d for d in scenario["simulated_disruptions"] if d["date"] == date_str]

            snapshot = {
                "market_microstructure": micro,
                "aviation_disruptions": disruptions,
                "gdelt_events": [],
                "finnhub": [],
                "fred": [],
                "data_freshness": {"market_microstructure": True},
                "fallback_mode": False,
            }

            result = scorer.score(snapshot)
            regime_p = result["regime_shift_probability"]
            confidence = result["confidence"]

            # Mode transition logic (simplified, no hysteresis for backtest)
            if regime_p >= crisis_thresh:
                new_mode = "CRISIS"
            elif regime_p >= elevated_thresh:
                new_mode = "ELEVATED"
            else:
                new_mode = "NORMAL"

            transition = None
            if new_mode != mode:
                transition = f"{mode} -> {new_mode}"
                mode = new_mode

            entry = {
                "date": date_str,
                "regime_p": round(regime_p, 4),
                "confidence": round(confidence, 4),
                "mode": mode,
                "transition": transition,
                "disruption_count": len(disruptions),
                "avg_vol": round(sum(m.get("sigma_daily_pct", 0) for m in micro.values()) / max(len(micro), 1), 4),
                "top_vol": max((m.get("sigma_daily_pct", 0) for m in micro.values()), default=0),
            }
            timeline.append(entry)

            # Print key dates
            if transition or disruptions:
                icon = "🔴" if "CRISIS" in (transition or "") else ("🟡" if "ELEVATED" in (transition or "") else "📰")
                print(f"  {icon} {date_str}  regime_p={regime_p:.3f}  mode={mode:10s}  "
                      f"avg_vol={entry['avg_vol']:.2f}%  "
                      f"{'TRANSITION: ' + transition if transition else ''}"
                      f"{'EVENT: ' + disruptions[0]['title'] if disruptions else ''}")

        # Analysis
        crisis_dates = [e["date"] for e in timeline if e.get("transition") and "CRISIS" in e["transition"]]
        elevated_dates = [e["date"] for e in timeline if e.get("transition") and "ELEVATED" in e["transition"]]
        expected = scenario["expected_crisis_date"]

        first_elevated = elevated_dates[0] if elevated_dates else None
        first_crisis = crisis_dates[0] if crisis_dates else None

        # Detection lead time
        if first_crisis and expected:
            try:
                crisis_dt = datetime.strptime(first_crisis, "%Y-%m-%d")
                expected_dt = datetime.strptime(expected, "%Y-%m-%d")
                lead_days = (expected_dt - crisis_dt).days
            except Exception:
                lead_days = None
        else:
            lead_days = None

        analysis = {
            "scenario": scenario_key,
            "name": scenario["name"],
            "expected_crisis_date": expected,
            "first_elevated_date": first_elevated,
            "first_crisis_date": first_crisis,
            "detection_lead_days": lead_days,
            "max_regime_p": max((e["regime_p"] for e in timeline), default=0),
            "max_vol": max((e["top_vol"] for e in timeline), default=0),
            "crisis_days": sum(1 for e in timeline if e["mode"] == "CRISIS"),
            "elevated_days": sum(1 for e in timeline if e["mode"] == "ELEVATED"),
            "timeline_days": len(timeline),
        }

        print(f"\n  ANALYSIS:")
        print(f"    Expected crisis:    {expected}")
        print(f"    First elevated:     {first_elevated or 'never'}")
        print(f"    First crisis:       {first_crisis or 'never'}")
        print(f"    Detection lead:     {lead_days} days" if lead_days is not None else "    Detection lead:     N/A")
        print(f"    Max regime P:       {analysis['max_regime_p']:.3f}")
        print(f"    Max vol:            {analysis['max_vol']:.2f}%")
        print(f"    Crisis days:        {analysis['crisis_days']}/{analysis['timeline_days']}")
        print()

        return {"analysis": analysis, "timeline": timeline}


def main():
    p = argparse.ArgumentParser(description="Global Sentinel Historical Backtest")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--scenario", default=None, choices=list(SCENARIOS.keys()),
                   help="Run specific scenario (default: all)")
    p.add_argument("--output-json", default=None)
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    bt = HistoricalBacktest(repo_root)

    results = {}
    scenarios = [args.scenario] if args.scenario else list(SCENARIOS.keys())

    for sc in scenarios:
        try:
            results[sc] = bt.run_scenario(sc)
        except Exception as e:
            print(f"  ERROR in {sc}: {e}")
            results[sc] = {"error": str(e)}

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Results saved to {out}")


if __name__ == "__main__":
    main()
