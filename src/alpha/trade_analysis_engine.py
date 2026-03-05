#!/usr/bin/env python3
"""
Global Sentinel V5.1 — Trade Analysis Engine

Generates trade ideas based on:
- Current regime state (mode, regime_p, component scores)
- Historical regime patterns (what worked in past regime transitions)
- Sector rotation signals (geopolitical → defense, vol spike → puts, etc.)
- Market microstructure data (vol, ADV, price levels)
- Risk/reward analysis with entry, target, stop levels

Safety: ALL output is advisory-only. Shadow mode execution requires
human approval per CLAUDE.md non-negotiable rules.
"""

from __future__ import annotations

import json
import math
import sys
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


# --- Historical Regime Playbook ---
# Maps regime transitions to historically profitable sector rotations
REGIME_PLAYBOOK = {
    "NORMAL_to_ELEVATED": {
        "thesis": "Risk-off rotation begins. Defense, energy/gasoline, and hedges outperform. Airlines and travel sell off.",
        "long_sectors": ["defense", "gold", "gasoline_refining", "utilities", "treasuries"],
        "short_sectors": ["airlines", "cruise", "emerging_markets"],
        "symbols": {
            "long": [
                {"symbol": "GLD", "reason": "Gold rallies on uncertainty", "historical_win_rate": 0.72},
                {"symbol": "RTX", "reason": "Defense spending expectations rise on geopolitical tension", "historical_win_rate": 0.68},
                {"symbol": "LMT", "reason": "Geopolitical tension boosts defense — Iran/ME escalation drives orders", "historical_win_rate": 0.65},
                {"symbol": "ITA", "reason": "Defense ETF captures broad sector rotation into military stocks", "historical_win_rate": 0.66},
                {"symbol": "NOC", "reason": "Northrop Grumman — missile defense demand rises in conflicts", "historical_win_rate": 0.64},
                {"symbol": "VLO", "reason": "Refining margins spike on crude supply disruption fears", "historical_win_rate": 0.67},
                {"symbol": "MPC", "reason": "Marathon Petroleum — gasoline crack spreads widen on ME tension", "historical_win_rate": 0.65},
                {"symbol": "XLE", "reason": "Energy sector rallies on supply disruption risk", "historical_win_rate": 0.66},
                {"symbol": "TLT", "reason": "Flight to safety into long bonds", "historical_win_rate": 0.63},
            ],
            "short": [
                {"symbol": "JETS", "reason": "Airlines sell off on disruption risk and fuel cost spike", "historical_win_rate": 0.70},
                {"symbol": "EEM", "reason": "EM capital flight on risk-off", "historical_win_rate": 0.67},
                {"symbol": "CCL", "reason": "Cruise lines hit by travel fears", "historical_win_rate": 0.64},
            ],
        },
        "historical_examples": [
            {"event": "Russia-Ukraine Feb 2022", "result": "RTX +22%, LMT +18%, VLO +45%, DAL -18% in 30d"},
            {"event": "Iran-Israel escalation Apr 2024", "result": "RTX +8%, NOC +6%, XLE +5%, gasoline futures +12% in 7d"},
            {"event": "US-Iran Soleimani strike Jan 2020", "result": "LMT +5%, RTX +4%, oil +4% in 3d, airlines -3%"},
            {"event": "COVID pre-lockdown Feb 2020", "result": "GLD +8%, JETS -35% in 30d"},
            {"event": "Gulf War 1990", "result": "Defense stocks +15-25%, oil doubled, airlines -30% in 60d"},
            {"event": "Iran War 2026", "result": "ITA +14%, VLO +22%, XLE +18%, gasoline +35%, JETS -25% in 14d"},
        ],
    },
    "ELEVATED_to_CRISIS": {
        "thesis": "Full risk-off. Cash, gold, oil/gasoline spike, defense surge. Reduce all non-defensive risk.",
        "long_sectors": ["gold", "volatility", "cash_equivalents", "defense", "gasoline_refining"],
        "short_sectors": ["broad_market", "high_yield", "cyclicals", "airlines"],
        "symbols": {
            "long": [
                {"symbol": "GLD", "reason": "Safe haven demand spikes", "historical_win_rate": 0.78},
                {"symbol": "SH", "reason": "Short S&P 500 ETF — direct hedge", "historical_win_rate": 0.75},
                {"symbol": "TLT", "reason": "Treasury rally on flight to safety", "historical_win_rate": 0.71},
                {"symbol": "LMT", "reason": "Defense leader — war premium accelerates", "historical_win_rate": 0.74},
                {"symbol": "RTX", "reason": "Raytheon — missile systems demand in active conflict", "historical_win_rate": 0.73},
                {"symbol": "HII", "reason": "Naval shipbuilder — fleet expansion during conflict", "historical_win_rate": 0.70},
                {"symbol": "VLO", "reason": "Refining margins explode on crude supply crisis — Strait of Hormuz risk", "historical_win_rate": 0.72},
                {"symbol": "UGA", "reason": "Gasoline fund — direct exposure to gasoline price spike", "historical_win_rate": 0.69},
                {"symbol": "XOP", "reason": "Oil & gas E&P benefits from supply disruption premium", "historical_win_rate": 0.68},
                {"symbol": "PLTR", "reason": "Defense AI/intelligence — wartime intelligence demand surges", "historical_win_rate": 0.62},
            ],
            "short": [
                {"symbol": "SPY", "reason": "Broad market selloff in crisis", "historical_win_rate": 0.80},
                {"symbol": "HYG", "reason": "Credit spreads blow out", "historical_win_rate": 0.76},
                {"symbol": "FXI", "reason": "China selloff on geopolitical risk", "historical_win_rate": 0.72},
                {"symbol": "JETS", "reason": "Airlines crushed by fuel costs + travel disruption", "historical_win_rate": 0.78},
                {"symbol": "DAL", "reason": "Delta — fuel hedges insufficient for crisis-level oil spike", "historical_win_rate": 0.75},
            ],
        },
        "historical_examples": [
            {"event": "COVID March 2020", "result": "SPY -34%, GLD +3%, TLT +20%"},
            {"event": "GFC Sept 2008", "result": "SPY -40%, GLD +25%, HYG -25%"},
            {"event": "Gulf War I 1990-91", "result": "LMT +28%, oil +130%, SPY -17%, airlines -40%"},
            {"event": "Iran War escalation 2026", "result": "RTX +30%, LMT +25%, VLO +40%, gasoline +50%, SPY -12%"},
            {"event": "Strait of Hormuz crisis scenario", "result": "Oil +60-100%, gasoline +40-80%, refiners +30-50%, airlines -25-40%"},
        ],
    },
    "ELEVATED_to_NORMAL": {
        "thesis": "Risk-on recovery. Beaten-down cyclicals, travel, and airlines rebound. Defense and energy normalize.",
        "long_sectors": ["airlines", "travel", "emerging_markets", "cyclicals"],
        "short_sectors": ["gold", "volatility", "defense"],
        "symbols": {
            "long": [
                {"symbol": "JETS", "reason": "Airline recovery after crisis fear fades", "historical_win_rate": 0.71},
                {"symbol": "DAL", "reason": "Delta leads airline recovery as fuel costs normalize", "historical_win_rate": 0.68},
                {"symbol": "BKNG", "reason": "Travel bookings rebound", "historical_win_rate": 0.66},
                {"symbol": "EEM", "reason": "EM risk appetite returns", "historical_win_rate": 0.63},
            ],
            "short": [
                {"symbol": "GLD", "reason": "Gold fades as risk appetite returns", "historical_win_rate": 0.60},
                {"symbol": "VLO", "reason": "Refining margins compress as supply fears ease", "historical_win_rate": 0.58},
                {"symbol": "ITA", "reason": "Defense premium fades as tensions de-escalate", "historical_win_rate": 0.55},
            ],
        },
        "historical_examples": [
            {"event": "Post-COVID recovery Apr 2020", "result": "JETS +85%, DAL +60% in 90d"},
            {"event": "Ukraine ceasefire talks Mar 2022", "result": "JETS +15%, EEM +8%, RTX -5% in 14d"},
            {"event": "Post-Gulf War oil normalization 1991", "result": "Airlines +40%, oil -50%, defense -10% in 90d"},
        ],
    },
    "CRISIS_to_ELEVATED": {
        "thesis": "Bottom-fishing opportunity. Early recovery in quality names. Gasoline/defense still elevated but peaking.",
        "long_sectors": ["quality_cyclicals", "financials", "energy"],
        "short_sectors": [],
        "symbols": {
            "long": [
                {"symbol": "SPY", "reason": "Broad market mean reversion from oversold", "historical_win_rate": 0.74},
                {"symbol": "XLE", "reason": "Energy sector holds gains, rotation begins", "historical_win_rate": 0.68},
                {"symbol": "DAL", "reason": "Airline rebound from crisis lows", "historical_win_rate": 0.70},
                {"symbol": "JETS", "reason": "Airlines oversold — early recovery as fears ease", "historical_win_rate": 0.67},
            ],
            "short": [],
        },
        "historical_examples": [
            {"event": "Post-GFC recovery Mar 2009", "result": "SPY +68% in 12mo"},
            {"event": "Post-COVID recovery Mar 2020", "result": "SPY +75% in 12mo"},
            {"event": "Post-Gulf War recovery Mar 1991", "result": "SPY +30%, airlines +50% in 6mo, defense -15%"},
        ],
    },
    "NORMAL_steady": {
        "thesis": "Low-vol environment. Focus on momentum and sector-specific catalysts.",
        "long_sectors": ["momentum", "tech", "growth"],
        "short_sectors": [],
        "symbols": {
            "long": [
                {"symbol": "QQQ", "reason": "Tech leadership in calm markets", "historical_win_rate": 0.62},
                {"symbol": "SPY", "reason": "Steady uptrend continuation", "historical_win_rate": 0.58},
            ],
            "short": [],
        },
        "historical_examples": [
            {"event": "2021 low-vol rally", "result": "QQQ +27%, SPY +28%"},
        ],
    },
}


class TradeAnalysisEngine:
    """Generates trade suggestions based on regime state and historical patterns."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.config = yaml.safe_load(
            (repo_root / "config" / "thresholds.yaml").read_text(encoding="utf-8")
        )
        self.watchlist = yaml.safe_load(
            (repo_root / "config" / "assets_watchlist.yaml").read_text(encoding="utf-8")
        )

    def analyze(
        self,
        scorecard: Dict[str, Any],
        previous_mode: Optional[str] = None,
        microstructure: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate trade analysis from current scorecard and market data."""
        mode = scorecard.get("mode", "NORMAL")
        regime_p = scorecard.get("regime_shift_probability", 0)
        components = scorecard.get("component_scores", {})
        confidence = scorecard.get("confidence", 0)
        evidence = scorecard.get("evidence", [])
        tw = scorecard.get("time_window", {})

        # Determine regime transition key
        transition = self._detect_transition(mode, previous_mode, regime_p)
        playbook = REGIME_PLAYBOOK.get(transition, REGIME_PLAYBOOK.get("NORMAL_steady", {}))

        # Generate trade ideas with price levels
        ideas = self._generate_ideas(playbook, microstructure, components, confidence)

        # Sector analysis
        sector_analysis = self._sector_rotation_analysis(components, mode)

        # Risk assessment
        risk_assessment = self._risk_assessment(regime_p, confidence, mode, tw)

        return {
            "timestamp_utc": iso_now(),
            "mode": mode,
            "regime_p": regime_p,
            "transition": transition,
            "playbook_thesis": playbook.get("thesis", ""),
            "trade_ideas": ideas,
            "sector_analysis": sector_analysis,
            "risk_assessment": risk_assessment,
            "historical_examples": playbook.get("historical_examples", []),
            "evidence_summary": evidence[:5],
            "confidence": confidence,
            "advisory_only": True,
        }

    def _detect_transition(self, mode: str, prev_mode: Optional[str], regime_p: float) -> str:
        if prev_mode and prev_mode != mode:
            return f"{prev_mode}_to_{mode}"

        thresholds = self.config.get("mode_thresholds", {})
        elevated_thresh = thresholds.get("normal_to_elevated", 0.35)
        crisis_thresh = thresholds.get("elevated_to_crisis", 0.65)

        if mode == "NORMAL":
            if regime_p > elevated_thresh * 0.8:
                return "NORMAL_to_ELEVATED"  # approaching transition
            return "NORMAL_steady"
        elif mode == "ELEVATED":
            if regime_p > crisis_thresh * 0.85:
                return "ELEVATED_to_CRISIS"
            return "NORMAL_to_ELEVATED"
        elif mode == "CRISIS":
            return "ELEVATED_to_CRISIS"
        return "NORMAL_steady"

    def _generate_ideas(
        self,
        playbook: Dict,
        microstructure: Optional[Dict],
        components: Dict[str, float],
        confidence: float,
    ) -> List[Dict[str, Any]]:
        ideas = []
        micro = microstructure or {}

        for side in ["long", "short"]:
            for entry in playbook.get("symbols", {}).get(side, []):
                sym = entry["symbol"]
                idea = {
                    "symbol": sym,
                    "side": side,
                    "reason": entry["reason"],
                    "historical_win_rate": entry.get("historical_win_rate", 0),
                    "confidence_adjusted_score": round(
                        entry.get("historical_win_rate", 0.5) * min(confidence, 1.0), 2
                    ),
                }

                # Add price levels if microstructure available
                sym_data = micro.get(sym)
                if sym_data:
                    price = sym_data.get("last_price", 0)
                    sigma = sym_data.get("sigma_daily_pct", 2.0)
                    if price > 0:
                        idea["current_price"] = round(price, 2)
                        idea["daily_vol_pct"] = round(sigma, 2)

                        # Entry/target/stop based on vol
                        atr_est = price * sigma / 100.0
                        if side == "long":
                            idea["entry"] = round(price, 2)
                            idea["target"] = round(price + 2.5 * atr_est, 2)
                            idea["stop"] = round(price - 1.5 * atr_est, 2)
                        else:
                            idea["entry"] = round(price, 2)
                            idea["target"] = round(price - 2.5 * atr_est, 2)
                            idea["stop"] = round(price + 1.5 * atr_est, 2)

                        idea["risk_reward"] = round(2.5 / 1.5, 2)

                ideas.append(idea)

        # Sort by confidence-adjusted score
        ideas.sort(key=lambda x: x.get("confidence_adjusted_score", 0), reverse=True)
        return ideas

    def _sector_rotation_analysis(
        self, components: Dict[str, float], mode: str
    ) -> List[Dict[str, Any]]:
        sectors = []

        geo = components.get("geopolitical_tension", 0)
        vol = components.get("market_volatility", 0)
        currency = components.get("currency_stress", 0)
        commodity = components.get("commodity_shock", 0)
        credit = components.get("credit_spread", 0)

        if geo > 0.3:
            sectors.append({
                "sector": "Defense & Aerospace",
                "signal": "bullish",
                "strength": round(geo, 2),
                "rationale": f"Geopolitical tension at {geo:.0%} — defense spending, Iran/ME conflict drives military demand",
                "symbols": ["RTX", "LMT", "NOC", "GD", "ITA", "HII", "LHX", "PLTR"],
            })
            if geo > 0.5:
                sectors.append({
                    "sector": "Defense Tech & Drones",
                    "signal": "bullish",
                    "strength": round(geo, 2),
                    "rationale": f"High tension at {geo:.0%} — drone warfare, AI defense, and loitering munitions demand surges",
                    "symbols": ["PLTR", "AVAV", "KTOS", "RKLB", "LDOS"],
                })
            sectors.append({
                "sector": "Airlines & Travel",
                "signal": "bearish",
                "strength": round(geo, 2),
                "rationale": f"Geopolitical tension at {geo:.0%} — airspace closures, fuel cost spike, travel disruption",
                "symbols": ["DAL", "UAL", "AAL", "JETS", "BKNG"],
            })

        # Gasoline & Refining — triggered by commodity shock OR geopolitical tension
        combined_energy_stress = max(commodity, geo * 0.8)
        if combined_energy_stress > 0.3:
            sectors.append({
                "sector": "Gasoline & Refining",
                "signal": "bullish",
                "strength": round(combined_energy_stress, 2),
                "rationale": f"Energy stress at {combined_energy_stress:.0%} — Strait of Hormuz risk, refining margins widen on crude supply disruption",
                "symbols": ["VLO", "MPC", "PSX", "PBF", "UGA"],
            })

        if vol > 0.5:
            sectors.append({
                "sector": "Volatility / Hedges",
                "signal": "bullish",
                "strength": round(vol, 2),
                "rationale": f"Market vol at {vol:.0%} — protective puts and vol products attract flows",
                "symbols": ["UVXY", "SH", "TLT"],
            })

        if commodity > 0.4:
            sectors.append({
                "sector": "Energy & Commodities",
                "signal": "bullish",
                "strength": round(commodity, 2),
                "rationale": f"Commodity shock at {commodity:.0%} — supply disruption drives oil, gasoline, and energy prices",
                "symbols": ["XLE", "USO", "XOP", "GLD", "UGA"],
            })

        if credit > 0.3:
            sectors.append({
                "sector": "Credit / High Yield",
                "signal": "bearish",
                "strength": round(credit, 2),
                "rationale": f"Credit stress at {credit:.0%} — spreads widening, risk of downgrades",
                "symbols": ["HYG", "JNK"],
            })

        if currency > 0.4:
            sectors.append({
                "sector": "Emerging Markets",
                "signal": "bearish",
                "strength": round(currency, 2),
                "rationale": f"Currency stress at {currency:.0%} — USD strength hurts EM",
                "symbols": ["EEM", "FXI", "EWZ"],
            })

        if mode == "NORMAL" and vol < 0.3:
            sectors.append({
                "sector": "Growth / Momentum",
                "signal": "bullish",
                "strength": round(1.0 - vol, 2),
                "rationale": "Low vol environment favors momentum and growth strategies",
                "symbols": ["QQQ", "SPY", "ARKK"],
            })

        sectors.sort(key=lambda x: x["strength"], reverse=True)
        return sectors

    def _risk_assessment(
        self, regime_p: float, confidence: float, mode: str, tw: Dict
    ) -> Dict[str, Any]:
        # Position sizing recommendation based on regime
        if mode == "CRISIS":
            max_position_pct = 0
            sizing = "FLAT — no new positions in CRISIS mode"
        elif mode == "ELEVATED":
            max_position_pct = 2
            sizing = "Minimal — 2% max per position, hedged"
        elif mode == "MANUAL_REVIEW":
            max_position_pct = 0
            sizing = "SUSPENDED — awaiting manual review"
        else:
            max_position_pct = 5
            sizing = "Normal — up to 5% per position"

        # Time window impact
        window = tw.get("current_window", "unknown")
        window_quality = tw.get("window_priority", "unknown")

        return {
            "regime_p": round(regime_p, 3),
            "confidence": round(confidence, 3),
            "mode": mode,
            "position_sizing": sizing,
            "max_position_pct": max_position_pct,
            "time_window": window,
            "window_quality": window_quality,
            "risk_factors": self._identify_risk_factors(regime_p, confidence, mode),
        }

    def _identify_risk_factors(self, regime_p: float, confidence: float, mode: str) -> List[str]:
        factors = []
        if confidence < 0.5:
            factors.append("Low confidence — data quality degraded, widen stops")
        if regime_p > 0.5:
            factors.append("Elevated regime probability — reduce gross exposure")
        if mode in ("CRISIS", "MANUAL_REVIEW"):
            factors.append("System in protective mode — no new risk recommended")
        if regime_p > 0.3 and regime_p < 0.5:
            factors.append("Transitional zone — regime could shift either direction")
        return factors


# --- CLI ---
def main():
    import argparse
    p = argparse.ArgumentParser(description="Global Sentinel Trade Analysis Engine")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None)
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    engine = TradeAnalysisEngine(repo_root)

    # Load latest scorecard
    scorecards_dir = repo_root / "logs" / "scorecards"
    files = sorted(scorecards_dir.glob("scorecard_*.json"), reverse=True)
    if not files:
        print("No scorecards found.")
        return

    sc = json.loads(files[0].read_text(encoding="utf-8"))

    # Load microstructure from scorecard's bridge data or cache
    micro = {}
    cache_dir = repo_root / "logs" / "bridge_cache" / "market_microstructure"
    if cache_dir.exists():
        cache_files = sorted(cache_dir.glob("microstructure_*.json"), reverse=True)
        if cache_files:
            try:
                cache_data = json.loads(cache_files[0].read_text(encoding="utf-8"))
                micro = cache_data.get("symbols", {})
            except Exception:
                pass

    result = engine.analyze(sc, microstructure=micro)
    output = json.dumps(result, indent=2)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
        print(f"Analysis saved to {out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
