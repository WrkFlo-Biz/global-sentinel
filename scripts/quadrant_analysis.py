#!/usr/bin/env python3
"""
GSS Tech Quadrant Analysis — March 5, 2026

Runs filtered signals for AMD, AMZN, TSLA, and BTC using current
market intelligence and the three-layer decision matrix.

Usage:
    python3 scripts/quadrant_analysis.py
    python3 scripts/quadrant_analysis.py --ticker AMD
    python3 scripts/quadrant_analysis.py --stress-test oil_100
    python3 scripts/quadrant_analysis.py --btc-gamma-gap
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.alpha.gss_execution_engine import GSSExecutionEngine


# ============================================================
# MARCH 5 2026 — TECH QUADRANT DATA
# ============================================================

QUADRANT = {
    "AMD": {
        "spot": 202.07,
        "z_score": 2.9,
        "narrative_vel": 2.1,
        "gex": -1500000,
        "rsi": 58,
        "gamma_state": "Short Gamma (Dealers buying on upticks)",
        "put_call_ratio": 0.83,
        "iv_rank": 72,
        "catalyst": "12 analysts revised earnings up; AI infrastructure surge",
        "strategy": "Aggressive Long Calls — target $235",
        "risk": "Exit if Z-Score drops < 1.5",
    },
    "AMZN": {
        "spot": 216.78,
        "z_score": 2.8,
        "narrative_vel": 1.9,
        "gex": 500000,
        "rsi": 34,
        "gamma_state": "Long Gamma (Stabilizing)",
        "put_call_ratio": 1.05,
        "iv_rank": 55,
        "catalyst": "$200B CapEx forecast; RSI turning from oversold",
        "strategy": "Bull Put Spreads — income on stabilization",
        "risk": "Stop loss at $198 (technical floor)",
    },
    "TSLA": {
        "spot": 392.43,
        "z_score": 1.2,
        "narrative_vel": 2.7,
        "gex": 100000,
        "rsi": 28,
        "gamma_state": "Neutral (High Put Wall at $385)",
        "put_call_ratio": 1.45,
        "iv_rank": 65,
        "catalyst": "Patchy European recovery; Strong Sell technicals",
        "strategy": "Put Debit Spreads — bearish structure",
        "risk": "Iron Condor if volatility compresses",
    },
    "BTC": {
        "spot": 71000,
        "z_score": 3.1,
        "narrative_vel": 2.8,
        "gex": -2000000,
        "rsi": 50,
        "gamma_state": "High Volatility — Gamma Gap",
        "put_call_ratio": 0.92,
        "iv_rank": 82,
        "catalyst": "Iran conflict driving safe haven flows; high volatility regime",
        "strategy": "Long Straddle — bet on massive move",
        "risk": "Hedge with Puts if Z-Score > 3.5",
    },
}

# Geopolitical stress test: $100/barrel oil
OIL_STRESS_MODIFIERS = {
    "AMD":  {"z_mod": 0.0, "vel_mod": 0.3, "note": "Indirect: energy cost headwind for data centers"},
    "AMZN": {"z_mod": -0.2, "vel_mod": 0.5, "note": "Logistics costs surge; delivery margin compressed"},
    "TSLA": {"z_mod": 0.3, "vel_mod": 0.8, "note": "EV demand spike (oil substitute); but supply chain stress"},
    "BTC":  {"z_mod": 0.5, "vel_mod": 0.6, "note": "Dollar weakness from oil shock drives crypto inflows"},
}


def build_ticker_snapshot(ticker: str, data: dict, z_override=None, vel_override=None) -> dict:
    """Build a GSS-compatible snapshot for a single ticker."""
    z = z_override if z_override is not None else data["z_score"]
    vel = vel_override if vel_override is not None else data["narrative_vel"]

    return {
        "gcp_consciousness": {
            "max_z": z,
            "mean_z": z * 0.85,
            "coherence_level": "extreme" if z > 3.0 else "high" if z > 2.0 else "moderate",
            "node_count": 125,
            "regional_z": {"north_america": z * 0.9, "asia": z * 1.05},
            "regional_spikes": [],
        },
        "narrative_velocity": {
            "velocity_score": vel,
            "dominant_narrative": "tech_momentum" if ticker in ("AMD", "AMZN") else "geopolitical",
            "article_count_1h": 2500,
            "acceleration": vel * 0.8,
        },
        "options_greeks": {
            "symbols": {},
            "aggregate": {
                "avg_put_call_ratio": data["put_call_ratio"],
                "max_gamma_squeeze_risk": abs(data["gex"]) / 2000000,
                "vix_level": 21.15,
                "iv_rank": data["iv_rank"],
            },
        },
        "market_microstructure": {
            ticker: {"last_price": data["spot"], "sigma_daily_pct": 2.5},
        },
        "vix": 21.15,
    }


def run_quadrant(tickers=None, stress_test=None, btc_gamma=False):
    gss = GSSExecutionEngine(REPO_ROOT)
    selected = tickers or list(QUADRANT.keys())

    print(f"\n{'='*70}")
    print(f"  GSS TECH QUADRANT ANALYSIS — MARCH 5, 2026")
    if stress_test:
        print(f"  STRESS TEST: {stress_test.upper()}")
    print(f"{'='*70}")

    results = []

    for ticker in selected:
        data = QUADRANT[ticker]
        z = data["z_score"]
        vel = data["narrative_vel"]

        # Apply stress test modifiers
        stress_note = ""
        if stress_test == "oil_100" and ticker in OIL_STRESS_MODIFIERS:
            mod = OIL_STRESS_MODIFIERS[ticker]
            z += mod["z_mod"]
            vel += mod["vel_mod"]
            stress_note = f"  [OIL STRESS] {mod['note']}"

        snapshot = build_ticker_snapshot(ticker, data, z_override=z, vel_override=vel)
        scorecard = {"mode": "ELEVATED", "regime_shift_probability": 0.68, "confidence": 0.79}

        result = gss.analyze(snapshot, scorecard)
        signal = result["gss_signal"]
        action = result["action"]
        confidence = result["confidence"]
        reason = result["reason"]

        # Color codes
        colors = {
            "BLACK_SWAN_SHIELD": "\033[91m",
            "GAMMA_SQUEEZE": "\033[93m",
            "NOISE_FILTER": "\033[94m",
            "PRE_PULSE": "\033[95m",
            "NEUTRAL": "\033[90m",
        }
        c = colors.get(signal, "\033[0m")
        r = "\033[0m"

        print(f"\n  {'─'*60}")
        print(f"  {c}${ticker}{r} — ${data['spot']:,.2f}")
        print(f"  Gamma: {data['gamma_state']}")
        print(f"  Field Z: {z:.1f} | Narrative: {vel:.1f} | RSI: {data['rsi']}")
        if stress_note:
            print(stress_note)
        print()
        print(f"  {c}SIGNAL:     {signal}{r}")
        print(f"  {c}ACTION:     {action}{r}")
        print(f"  {c}CONFIDENCE: {confidence:.0%}{r}")
        print()
        print(f"  Strategy:  {data['strategy']}")
        print(f"  Risk:      {data['risk']}")
        print(f"  Catalyst:  {data['catalyst']}")
        print()

        # Hedge recommendations
        hedges = result.get("hedge_recommendations", [])
        if hedges:
            print(f"  Hedge Recommendations ({len(hedges)}):")
            for i, h in enumerate(hedges[:4], 1):
                inst = h.get("instrument", "?")
                act = h.get("action", "?")
                sizing = h.get("sizing", "")
                rationale = h.get("rationale", "")
                advisory = " [ADVISORY]" if h.get("advisory_only") else " [EXECUTABLE]"
                print(f"    {i}. {inst} — {act} ({sizing}){advisory}")
                if rationale:
                    print(f"       {rationale}")
            print()

        results.append((ticker, signal, action, confidence))

    # BTC Gamma Gap Analysis
    if btc_gamma:
        print(f"\n  {'='*60}")
        print(f"  BTC GAMMA GAP ANALYSIS")
        print(f"  {'='*60}")
        btc = QUADRANT["BTC"]
        spot = btc["spot"]
        # Estimate liquidation levels based on typical BTC derivatives structure
        short_liq_levels = [
            (spot * 1.05, "5% above spot — first wave of short squeezes"),
            (spot * 1.10, "10% above — major short liquidation cluster"),
            (spot * 1.15, "15% above — cascading liquidations"),
        ]
        long_liq_levels = [
            (spot * 0.95, "5% below spot — leveraged long unwinds"),
            (spot * 0.90, "10% below — heavy long liquidation zone"),
            (spot * 0.85, "15% below — capitulation cascade"),
        ]
        print(f"\n  BTC Spot: ${spot:,.0f} | GEX: {btc['gex']:,.0f} | Z-Score: {btc['z_score']}")
        print(f"\n  SHORT LIQUIDATION LEVELS (upside gamma gap):")
        for price, note in short_liq_levels:
            print(f"    ${price:,.0f} — {note}")
        print(f"\n  LONG LIQUIDATION LEVELS (downside gamma gap):")
        for price, note in long_liq_levels:
            print(f"    ${price:,.0f} — {note}")
        print(f"\n  VERDICT: With GEX at {btc['gex']:,.0f} and Z-Score {btc['z_score']},")
        print(f"  the gamma gap favors a VIOLENT move. Straddle recommended.")
        print()

    # Summary table
    print(f"\n  {'='*60}")
    print(f"  QUADRANT SUMMARY")
    print(f"  {'='*60}")
    print(f"  {'Ticker':<8} {'Signal':<22} {'Action':<18} {'Conf':>6}")
    print(f"  {'─'*56}")
    for ticker, signal, action, conf in results:
        print(f"  {ticker:<8} {signal:<22} {action:<18} {conf:>5.0%}")
    print()


def main():
    parser = argparse.ArgumentParser(description="GSS Tech Quadrant Analysis")
    parser.add_argument("--ticker", type=str, help="Specific ticker (AMD, AMZN, TSLA, BTC)")
    parser.add_argument("--stress-test", type=str, choices=["oil_100"], help="Run geopolitical stress test")
    parser.add_argument("--btc-gamma-gap", action="store_true", help="Run BTC gamma gap analysis")
    parser.add_argument("--all", action="store_true", help="Run full analysis with all extras")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else None
    stress = args.stress_test
    btc_gamma = args.btc_gamma_gap

    if args.all:
        stress = "oil_100"
        btc_gamma = True

    run_quadrant(tickers=tickers, stress_test=stress, btc_gamma=btc_gamma)


if __name__ == "__main__":
    main()
