#!/usr/bin/env python3
"""
Global Sentinel System — Black Swan Event Simulator

Simulates historical crisis scenarios against the GSS execution engine
to validate the three-layer decision matrix:
  1. Field Layer (GCP Z-scores)
  2. Narrative Layer (velocity)
  3. Execution Layer (Greeks, VIX)

Usage:
    python scripts/simulate_black_swan.py --scenario 2020_covid
    python scripts/simulate_black_swan.py --scenario 2008_gfc
    python scripts/simulate_black_swan.py --scenario iran_war_2026
    python scripts/simulate_black_swan.py --scenario all
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# HISTORICAL SCENARIO PARAMETERS
# ============================================================

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "2008_gfc": {
        "name": "2008 Global Financial Crisis (Lehman Brothers)",
        "date": "2008-09-15",
        "description": "Lehman Brothers collapses. Credit markets freeze. S&P drops 57% peak-to-trough.",
        "gcp_consciousness": {
            "max_z": 3.8,
            "mean_z": 2.9,
            "coherence_level": "extreme",
            "node_count": 65,
            "regional_z": {"north_america": 4.1, "europe": 3.5, "asia": 2.8},
            "regional_spikes": [
                {"region": "north_america", "z_score": 4.1, "level": "extreme",
                 "predicted_markets": ["SPY", "QQQ", "HYG"], "market_zone": "us_equities"},
                {"region": "europe", "z_score": 3.5, "level": "high",
                 "predicted_markets": ["EFA", "FXI"], "market_zone": "eu_equities"},
            ],
            "evidence": ["RNG extreme deviation 48h before Lehman filing",
                         "Global coherence spike: collective fear of systemic collapse"],
            "fresh": True,
        },
        "narrative_velocity": {
            "velocity_score": 2.8,
            "dominant_narrative": "financial_crisis",
            "article_count_1h": 4500,
            "article_count_24h": 85000,
            "acceleration": 3.2,
            "top_keywords": ["lehman", "bank failure", "credit default", "market crash", "contagion"],
            "fresh": True,
        },
        "options_greeks": {
            "symbols": {
                "SPY": {"put_call_ratio": 2.1, "net_gamma": -850000, "avg_iv": 0.65, "net_delta": -0.45},
                "QQQ": {"put_call_ratio": 1.8, "net_gamma": -420000, "avg_iv": 0.58, "net_delta": -0.38},
            },
            "aggregate": {
                "avg_put_call_ratio": 1.95,
                "max_gamma_squeeze_risk": 0.3,
                "vix_level": 80.0,
                "iv_rank": 99,
                "vix_signal": "extreme_fear",
            },
            "fresh": True,
        },
        "market_microstructure": {
            "SPY": {"last_price": 119.0, "sigma_daily_pct": 8.5},
            "QQQ": {"last_price": 29.0, "sigma_daily_pct": 9.2},
            "GLD": {"last_price": 88.0, "sigma_daily_pct": 3.1},
            "TLT": {"last_price": 107.0, "sigma_daily_pct": 2.8},
        },
        "scorecard": {
            "mode": "CRISIS",
            "regime_shift_probability": 0.92,
            "confidence": 0.88,
            "component_scores": {
                "geopolitical_tension": 0.4,
                "market_volatility": 0.95,
                "currency_stress": 0.7,
                "commodity_shock": 0.3,
                "credit_spread": 0.95,
                "liquidity_stress": 0.9,
                "consciousness_coherence": 0.85,
            },
        },
    },

    "2020_covid": {
        "name": "2020 COVID-19 Market Crash",
        "date": "2020-03-16",
        "description": "COVID lockdowns trigger fastest bear market in history. S&P drops 34% in 23 days.",
        "gcp_consciousness": {
            "max_z": 3.5,
            "mean_z": 2.7,
            "coherence_level": "extreme",
            "node_count": 118,
            "regional_z": {"north_america": 3.2, "europe": 3.8, "asia": 3.5, "south_america": 2.1},
            "regional_spikes": [
                {"region": "europe", "z_score": 3.8, "level": "extreme",
                 "predicted_markets": ["EFA", "FXI"], "market_zone": "eu_equities"},
                {"region": "asia", "z_score": 3.5, "level": "high",
                 "predicted_markets": ["FXI", "EEM"], "market_zone": "asian_equities"},
            ],
            "evidence": ["RNG deviation began 48h before WHO pandemic declaration",
                         "Global coherence: humanity processing existential threat simultaneously"],
            "fresh": True,
        },
        "narrative_velocity": {
            "velocity_score": 3.0,
            "dominant_narrative": "pandemic",
            "article_count_1h": 8000,
            "article_count_24h": 150000,
            "acceleration": 4.5,
            "top_keywords": ["covid", "pandemic", "lockdown", "circuit breaker", "market crash"],
            "fresh": True,
        },
        "options_greeks": {
            "symbols": {
                "SPY": {"put_call_ratio": 2.5, "net_gamma": -1200000, "avg_iv": 0.82, "net_delta": -0.55},
                "QQQ": {"put_call_ratio": 2.0, "net_gamma": -600000, "avg_iv": 0.75, "net_delta": -0.48},
            },
            "aggregate": {
                "avg_put_call_ratio": 2.25,
                "max_gamma_squeeze_risk": 0.2,
                "vix_level": 82.69,
                "iv_rank": 100,
                "vix_signal": "extreme_fear",
            },
            "fresh": True,
        },
        "market_microstructure": {
            "SPY": {"last_price": 239.0, "sigma_daily_pct": 12.0},
            "QQQ": {"last_price": 181.0, "sigma_daily_pct": 11.5},
            "GLD": {"last_price": 148.0, "sigma_daily_pct": 4.2},
            "TLT": {"last_price": 170.0, "sigma_daily_pct": 5.1},
        },
        "scorecard": {
            "mode": "CRISIS",
            "regime_shift_probability": 0.95,
            "confidence": 0.91,
            "component_scores": {
                "geopolitical_tension": 0.3,
                "market_volatility": 0.98,
                "currency_stress": 0.5,
                "commodity_shock": 0.7,
                "credit_spread": 0.8,
                "liquidity_stress": 0.85,
                "consciousness_coherence": 0.90,
            },
        },
    },

    "iran_war_2026": {
        "name": "Iran War Escalation 2026 (Simulated)",
        "date": "2026-03-06",
        "description": "US-Iran military escalation. Strait of Hormuz blockade. Oil +60%, defense stocks surge, airlines collapse.",
        "gcp_consciousness": {
            "max_z": 3.2,
            "mean_z": 2.5,
            "coherence_level": "high",
            "node_count": 125,
            "regional_z": {"north_america": 2.8, "europe": 2.5, "asia": 3.2, "middle_east": 4.5},
            "regional_spikes": [
                {"region": "middle_east", "z_score": 4.5, "level": "extreme",
                 "predicted_markets": ["XLE", "USO", "UGA"], "market_zone": "energy"},
                {"region": "asia", "z_score": 3.2, "level": "high",
                 "predicted_markets": ["FXI", "EEM"], "market_zone": "asian_equities"},
            ],
            "evidence": ["Middle East RNG cluster extreme deviation",
                         "Global coherence spike: collective attention on Strait of Hormuz",
                         "Asia regional spike: supply chain disruption fear"],
            "fresh": True,
        },
        "narrative_velocity": {
            "velocity_score": 2.5,
            "dominant_narrative": "war_conflict",
            "article_count_1h": 3500,
            "article_count_24h": 65000,
            "acceleration": 2.8,
            "top_keywords": ["iran", "strait of hormuz", "oil", "military", "airstrike", "blockade"],
            "fresh": True,
        },
        "options_greeks": {
            "symbols": {
                "SPY": {"put_call_ratio": 1.6, "net_gamma": -350000, "avg_iv": 0.38, "net_delta": -0.25},
                "QQQ": {"put_call_ratio": 1.4, "net_gamma": -180000, "avg_iv": 0.35, "net_delta": -0.20},
                "XLE": {"put_call_ratio": 0.4, "net_gamma": 250000, "avg_iv": 0.42, "net_delta": 0.60},
            },
            "aggregate": {
                "avg_put_call_ratio": 1.13,
                "max_gamma_squeeze_risk": 0.65,
                "vix_level": 35.0,
                "iv_rank": 82,
                "vix_signal": "fear",
            },
            "fresh": True,
        },
        "market_microstructure": {
            "SPY": {"last_price": 510.0, "sigma_daily_pct": 3.8},
            "QQQ": {"last_price": 440.0, "sigma_daily_pct": 4.2},
            "GLD": {"last_price": 215.0, "sigma_daily_pct": 2.5},
            "TLT": {"last_price": 92.0, "sigma_daily_pct": 1.8},
            "XLE": {"last_price": 98.0, "sigma_daily_pct": 5.5},
            "RTX": {"last_price": 125.0, "sigma_daily_pct": 4.1},
            "VLO": {"last_price": 165.0, "sigma_daily_pct": 6.2},
        },
        "scorecard": {
            "mode": "ELEVATED",
            "regime_shift_probability": 0.72,
            "confidence": 0.82,
            "component_scores": {
                "geopolitical_tension": 0.88,
                "market_volatility": 0.55,
                "currency_stress": 0.35,
                "commodity_shock": 0.82,
                "credit_spread": 0.25,
                "liquidity_stress": 0.20,
                "consciousness_coherence": 0.75,
            },
        },
    },

    "noise_trap": {
        "name": "Noise Trap (High Narrative, Low Coherence)",
        "date": "2026-03-06",
        "description": "Social media frenzy about minor event. High news velocity but GCP shows no real coherence. Classic bear trap.",
        "gcp_consciousness": {
            "max_z": 0.8,
            "mean_z": 0.5,
            "coherence_level": "random",
            "node_count": 125,
            "regional_z": {"north_america": 0.9, "europe": 0.6, "asia": 0.7},
            "regional_spikes": [],
            "evidence": ["RNG within normal random bounds", "No consciousness coherence detected"],
            "fresh": True,
        },
        "narrative_velocity": {
            "velocity_score": 2.5,
            "dominant_narrative": "financial_crisis",
            "article_count_1h": 2000,
            "article_count_24h": 35000,
            "acceleration": 2.0,
            "top_keywords": ["crash", "bubble", "selloff", "recession", "panic"],
            "fresh": True,
        },
        "options_greeks": {
            "symbols": {
                "SPY": {"put_call_ratio": 1.3, "net_gamma": -100000, "avg_iv": 0.22, "net_delta": -0.10},
            },
            "aggregate": {
                "avg_put_call_ratio": 1.3,
                "max_gamma_squeeze_risk": 0.15,
                "vix_level": 18.0,
                "iv_rank": 35,
                "vix_signal": "normal",
            },
            "fresh": True,
        },
        "market_microstructure": {
            "SPY": {"last_price": 520.0, "sigma_daily_pct": 1.2},
            "QQQ": {"last_price": 450.0, "sigma_daily_pct": 1.5},
        },
        "scorecard": {
            "mode": "NORMAL",
            "regime_shift_probability": 0.18,
            "confidence": 0.75,
            "component_scores": {
                "geopolitical_tension": 0.10,
                "market_volatility": 0.15,
                "currency_stress": 0.08,
                "commodity_shock": 0.05,
                "credit_spread": 0.10,
                "liquidity_stress": 0.05,
                "consciousness_coherence": 0.08,
            },
        },
    },

    "march_2026_pivot": {
        "name": "March 2026 Diplomatic Pivot (US-Iran De-escalation)",
        "date": "2026-03-05",
        "description": "US-Iran conflict with emerging diplomatic de-escalation signals. GCP Z=2.9, narrative velocity 2.4. GSS should detect Black Swan Shield on tech/energy divergence.",
        "gcp_consciousness": {
            "max_z": 2.9,
            "mean_z": 2.3,
            "coherence_level": "high",
            "node_count": 125,
            "regional_z": {"north_america": 2.6, "europe": 2.3, "asia": 2.8, "middle_east": 3.8},
            "regional_spikes": [
                {"region": "middle_east", "z_score": 3.8, "level": "extreme",
                 "predicted_markets": ["XLE", "USO", "OIH"], "market_zone": "energy"},
                {"region": "north_america", "z_score": 2.6, "level": "high",
                 "predicted_markets": ["SPY", "QQQ", "XLK"], "market_zone": "us_equities"},
            ],
            "evidence": ["Global consciousness coherent on Iran/US diplomatic signals",
                         "Middle East RNG nodes showing extreme deviation"],
            "fresh": True,
        },
        "narrative_velocity": {
            "velocity_score": 2.4,
            "dominant_narrative": "peace_talks",
            "article_count_1h": 3200,
            "article_count_24h": 52000,
            "acceleration": 2.1,
            "top_keywords": ["iran", "diplomacy", "de-escalation", "oil", "tech rebound", "peace talks"],
            "fresh": True,
        },
        "options_greeks": {
            "symbols": {
                "SPY": {"put_call_ratio": 1.2, "net_gamma": -350000, "avg_iv": 0.28, "net_delta": -0.15},
                "QQQ": {"put_call_ratio": 0.95, "net_gamma": -280000, "avg_iv": 0.30, "net_delta": 0.08},
                "XLE": {"put_call_ratio": 1.8, "net_gamma": -120000, "avg_iv": 0.42, "net_delta": -0.35},
                "AMD": {"put_call_ratio": 0.83, "net_gamma": -1500000, "avg_iv": 0.48, "net_delta": 0.22},
            },
            "aggregate": {
                "avg_put_call_ratio": 1.19,
                "max_gamma_squeeze_risk": 0.65,
                "vix_level": 21.15,
                "iv_rank": 68,
                "vix_signal": "elevated",
            },
            "fresh": True,
        },
        "market_microstructure": {
            "SPY": {"last_price": 510.0, "sigma_daily_pct": 1.8},
            "QQQ": {"last_price": 430.0, "sigma_daily_pct": 2.2},
            "XLE": {"last_price": 85.0, "sigma_daily_pct": 3.5},
            "AMD": {"last_price": 202.07, "sigma_daily_pct": 5.8},
            "AMZN": {"last_price": 216.78, "sigma_daily_pct": 3.1},
            "TSLA": {"last_price": 392.43, "sigma_daily_pct": 4.2},
        },
        "scorecard": {
            "mode": "ELEVATED",
            "regime_shift_probability": 0.68,
            "confidence": 0.79,
            "component_scores": {
                "geopolitical_tension": 0.82,
                "market_volatility": 0.55,
                "currency_stress": 0.35,
                "commodity_shock": 0.70,
                "credit_spread": 0.25,
                "liquidity_stress": 0.20,
                "consciousness_coherence": 0.78,
            },
        },
    },

    "pre_pulse": {
        "name": "Pre-Pulse (High Coherence, Low Narrative)",
        "date": "2026-03-06",
        "description": "GCP shows extreme coherence but news hasn't caught up. The field is active before the event — early positioning window.",
        "gcp_consciousness": {
            "max_z": 3.0,
            "mean_z": 2.4,
            "coherence_level": "high",
            "node_count": 125,
            "regional_z": {"north_america": 2.8, "europe": 2.2, "asia": 3.5},
            "regional_spikes": [
                {"region": "asia", "z_score": 3.5, "level": "high",
                 "predicted_markets": ["FXI", "EEM", "USDJPY"], "market_zone": "asian_equities"},
            ],
            "evidence": ["Strong RNG deviation without corresponding news", "Asia regional spike: event brewing in Pacific rim"],
            "fresh": True,
        },
        "narrative_velocity": {
            "velocity_score": 0.3,
            "dominant_narrative": None,
            "article_count_1h": 150,
            "article_count_24h": 2800,
            "acceleration": 0.1,
            "top_keywords": [],
            "fresh": True,
        },
        "options_greeks": {
            "symbols": {
                "SPY": {"put_call_ratio": 0.9, "net_gamma": 50000, "avg_iv": 0.16, "net_delta": 0.05},
            },
            "aggregate": {
                "avg_put_call_ratio": 0.9,
                "max_gamma_squeeze_risk": 0.10,
                "vix_level": 14.0,
                "iv_rank": 20,
                "vix_signal": "calm",
            },
            "fresh": True,
        },
        "market_microstructure": {
            "SPY": {"last_price": 525.0, "sigma_daily_pct": 0.8},
        },
        "scorecard": {
            "mode": "NORMAL",
            "regime_shift_probability": 0.22,
            "confidence": 0.60,
            "component_scores": {
                "geopolitical_tension": 0.12,
                "market_volatility": 0.08,
                "currency_stress": 0.10,
                "commodity_shock": 0.05,
                "credit_spread": 0.05,
                "liquidity_stress": 0.03,
                "consciousness_coherence": 0.70,
            },
        },
    },
}


def build_snapshot(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Build a fake snapshot from scenario parameters."""
    return {
        "timestamp_utc": iso_now(),
        "gcp_consciousness": scenario["gcp_consciousness"],
        "narrative_velocity": scenario["narrative_velocity"],
        "options_greeks": scenario.get("options_greeks", {}),
        "market_microstructure": scenario.get("market_microstructure", {}),
        "data_freshness": {k: True for k in ["gcp_consciousness", "narrative_velocity", "options_greeks", "market_microstructure"]},
        "fallback_mode": False,
        "controls": {"kill_switch": False, "manual_veto": False},
    }


def run_simulation(scenario_key: str):
    """Run a single scenario through the GSS engine."""
    scenario = SCENARIOS[scenario_key]

    print(f"\n{'='*70}")
    print(f"  SCENARIO: {scenario['name']}")
    print(f"  Date: {scenario['date']}")
    print(f"  {scenario['description']}")
    print(f"{'='*70}\n")

    # Field Layer
    gcp = scenario["gcp_consciousness"]
    print(f"  FIELD LAYER (Leading)")
    print(f"    Max Z-Score:      {gcp['max_z']}")
    print(f"    Coherence Level:  {gcp['coherence_level']}")
    print(f"    Regional Z:       {json.dumps(gcp['regional_z'])}")
    print(f"    Spikes:           {len(gcp.get('regional_spikes', []))}")
    print()

    # Narrative Layer
    nv = scenario["narrative_velocity"]
    print(f"  NARRATIVE LAYER (Coinciding)")
    print(f"    Velocity Score:   {nv['velocity_score']}")
    print(f"    Dominant:         {nv['dominant_narrative']}")
    print(f"    Articles (1h):    {nv.get('article_count_1h', 'N/A')}")
    print(f"    Acceleration:     {nv.get('acceleration', 'N/A')}")
    print()

    # Execution Layer
    og = scenario.get("options_greeks", {}).get("aggregate", {})
    print(f"  EXECUTION LAYER (Lagging)")
    print(f"    VIX:              {og.get('vix_level', 'N/A')}")
    print(f"    Put/Call Ratio:   {og.get('avg_put_call_ratio', 'N/A')}")
    print(f"    IV Rank:          {og.get('iv_rank', 'N/A')}")
    print(f"    Gamma Squeeze:    {og.get('max_gamma_squeeze_risk', 'N/A')}")
    print()

    # Build snapshot and run GSS
    snapshot = build_snapshot(scenario)
    scorecard = scenario["scorecard"]

    print(f"  REGIME STATE")
    print(f"    Mode:             {scorecard['mode']}")
    print(f"    Regime P:         {scorecard['regime_shift_probability']}")
    print(f"    Confidence:       {scorecard['confidence']}")
    print()

    try:
        from src.alpha.gss_execution_engine import GSSExecutionEngine
        gss = GSSExecutionEngine(REPO_ROOT)
        result = gss.analyze(snapshot, scorecard)

        signal = result.get("gss_signal", "UNKNOWN")
        action = result.get("action", "UNKNOWN")
        reason = result.get("reason", "")
        confidence = result.get("confidence", 0)
        hedges = result.get("hedge_recommendations", [])

        # Color code the signal
        signal_colors = {
            "BLACK_SWAN_SHIELD": "\033[91m",  # Red
            "GAMMA_SQUEEZE": "\033[93m",       # Yellow
            "NOISE_FILTER": "\033[94m",        # Blue
            "PRE_PULSE": "\033[95m",           # Magenta
            "NEUTRAL": "\033[90m",             # Gray
        }
        color = signal_colors.get(signal, "\033[0m")
        reset = "\033[0m"

        print(f"  {color}{'*'*50}")
        print(f"  GSS SIGNAL:    {signal}")
        print(f"  ACTION:        {action}")
        print(f"  CONFIDENCE:    {confidence:.0%}")
        print(f"  {'*'*50}{reset}")
        print(f"  REASON: {reason}")
        print()

        if hedges:
            print(f"  HEDGE RECOMMENDATIONS ({len(hedges)}):")
            for i, h in enumerate(hedges, 1):
                print(f"    {i}. {h.get('symbol', '?')} — {h.get('action', '?')}")
                print(f"       {h.get('reason', '')[:80]}")
            print()

        margin = result.get("margin_status", {})
        if margin:
            print(f"  MARGIN STATUS: {margin.get('status', 'ok')}")
            if margin.get("recommendation"):
                print(f"  MARGIN ACTION: {margin['recommendation']}")
            print()

        return result

    except ImportError:
        print("  [ERROR] GSS Execution Engine not yet available.")
        print("  Running manual decision matrix...\n")

        # Fallback: manual decision matrix
        z = gcp["max_z"]
        vel = nv["velocity_score"]
        vix = og.get("vix_level", 20)

        if z > 2.5 and vel > 1.2:
            print(f"  \033[91m{'*'*50}")
            print(f"  GSS SIGNAL:    BLACK_SWAN_SHIELD")
            print(f"  ACTION:        BUY PROTECTIVE PUTS")
            print(f"  {'*'*50}\033[0m")
            print(f"  REASON: Z={z} + Velocity={vel} = Systemic Field Shift")
            print(f"  HEDGE: SPY puts (5% OTM, 2-4wk), QQQ puts, long TLT/GLD")
        elif vel > 2.0 and z < 1.0:
            print(f"  \033[94m{'*'*50}")
            print(f"  GSS SIGNAL:    NOISE_FILTER")
            print(f"  ACTION:        FADE THE HYPE")
            print(f"  {'*'*50}\033[0m")
            print(f"  REASON: Velocity={vel} but Z={z} = No Field Coherence. Fake move.")
        elif z > 2.0 and vel < 0.5:
            print(f"  \033[95m{'*'*50}")
            print(f"  GSS SIGNAL:    PRE_PULSE")
            print(f"  ACTION:        ACCUMULATE BEFORE EVENT")
            print(f"  {'*'*50}\033[0m")
            print(f"  REASON: Z={z} with Velocity={vel} = Field active before narrative. Early entry window.")
        else:
            print(f"  \033[90m{'*'*50}")
            print(f"  GSS SIGNAL:    NEUTRAL")
            print(f"  ACTION:        HOLD")
            print(f"  {'*'*50}\033[0m")

        print()
        return None

    except Exception as e:
        print(f"  [ERROR] {e}")
        return None


def main():
    import argparse
    p = argparse.ArgumentParser(description="GSS Black Swan Simulator")
    p.add_argument("--scenario", default="all",
                   choices=list(SCENARIOS.keys()) + ["all"],
                   help="Scenario to simulate")
    args = p.parse_args()

    print("\n" + "="*70)
    print("  GLOBAL SENTINEL SYSTEM — BLACK SWAN SIMULATOR")
    print("  Three-Layer Consciousness-Market Axis Analysis")
    print("="*70)

    if args.scenario == "all":
        results = {}
        for key in SCENARIOS:
            results[key] = run_simulation(key)

        # Summary
        print("\n" + "="*70)
        print("  SIMULATION SUMMARY")
        print("="*70)
        for key, result in results.items():
            name = SCENARIOS[key]["name"]
            if result:
                signal = result.get("gss_signal", "MANUAL")
                action = result.get("action", "N/A")
                conf = result.get("confidence", 0)
                print(f"  {name[:45]:45s} | {signal:20s} | {action:15s} | {conf:.0%}")
            else:
                print(f"  {name[:45]:45s} | MANUAL ANALYSIS")
    else:
        run_simulation(args.scenario)


if __name__ == "__main__":
    main()
