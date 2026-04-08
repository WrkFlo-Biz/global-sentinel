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


PRICE_FIELDS = ("last_price", "current_price", "close", "mark", "mid", "decision_price")
VOLATILITY_FIELDS = ("sigma_daily_pct", "realized_vol_pct", "volatility_pct", "daily_vol_pct")
TIMESTAMP_FIELDS = ("timestamp_utc", "fetched_at_utc", "pricing_timestamp_utc", "market_timestamp_utc")
STALE_MARKET_DATA_HOURS = 24.0
VERY_STALE_MARKET_DATA_HOURS = 72.0


def load_latest_microstructure_cache(repo_root: Path) -> Dict[str, Dict[str, Any]]:
    """Load the latest microstructure cache and support legacy/top-level shapes."""
    cache_dir = Path(repo_root) / "logs" / "bridge_cache" / "market_microstructure"
    if not cache_dir.exists():
        return {}

    cache_files = sorted(cache_dir.glob("microstructure_*.json"), reverse=True)
    if not cache_files:
        return {}

    for cache_file in cache_files:
        try:
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(cache_data, dict):
            continue

        nested_symbols = cache_data.get("symbols")
        if isinstance(nested_symbols, dict) and nested_symbols:
            return nested_symbols

        if cache_data and all(isinstance(value, dict) for value in cache_data.values()):
            return cache_data

    return {}


# --- Historical Regime Playbook ---
# Maps regime transitions to historically profitable sector rotations
REGIME_PLAYBOOK = {
    "NORMAL_to_ELEVATED": {
        "thesis": "AGGRESSIVE DAY TRADE: Risk-off rotation begins. Buy the dip on defense/oil/gold, sell the rip within hours. Tight 2:1 R:R on every trade. Flat by EOD.",
        "long_sectors": ["defense", "defense_tech", "oil_majors", "oil_ep", "gasoline_refining", "gold_safe_haven", "treasuries", "cybersecurity", "shipping", "uranium", "agriculture", "pharma", "leveraged_vol"],
        "short_sectors": ["airlines", "cruise", "emerging_markets", "transport"],
        "symbols": {
            "long": [
                # Defense primes — day trade momentum on escalation headlines
                {"symbol": "RTX", "reason": "Defense prime — buy dips on escalation fear, sell 1-2% rips", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "LMT", "reason": "Lockheed — intraday momentum on conflict headlines", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "NOC", "reason": "Northrop — missile defense demand, scalp intraday moves", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "GD", "reason": "General Dynamics — armor/vehicle demand on escalation", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "BA", "reason": "Boeing defense arm benefits from military orders", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "HII", "reason": "Naval shipbuilder — fleet expansion catalyst", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "LHX", "reason": "L3Harris — comms/electronics demand in conflict", "historical_win_rate": 0.61, "holding_period": "day"},
                # Defense tech/drones
                {"symbol": "PLTR", "reason": "Defense AI/intel — wartime intelligence demand surge", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "AVAV", "reason": "AeroVironment — drone warfare demand spike", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "KTOS", "reason": "Kratos — drone/unmanned systems, scalp on vol", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "RKLB", "reason": "Rocket Lab — space/satellite defense plays", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "LDOS", "reason": "Leidos — defense IT/cyber contracts surge", "historical_win_rate": 0.61, "holding_period": "day"},
                # Defense ETFs
                {"symbol": "ITA", "reason": "Defense ETF — broad sector rotation capture", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "PPA", "reason": "Aerospace & Defense ETF — diversified defense exposure", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "XAR", "reason": "SPDR Aerospace & Defense — equal weight defense", "historical_win_rate": 0.63, "holding_period": "day"},
                # Oil majors
                {"symbol": "XOM", "reason": "ExxonMobil — oil supply disruption premium", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "CVX", "reason": "Chevron — crude spike on ME tension", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "COP", "reason": "ConocoPhillips — E&P benefits from supply fear", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "OXY", "reason": "Occidental — leveraged oil upside", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "SLB", "reason": "Schlumberger — oilfield services demand", "historical_win_rate": 0.60, "holding_period": "day"},
                # Oil E&P ETFs
                {"symbol": "XOP", "reason": "Oil & gas E&P ETF — supply disruption premium", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "OIH", "reason": "Oil services ETF — drilling demand spike", "historical_win_rate": 0.62, "holding_period": "day"},
                # Gasoline/refining
                {"symbol": "VLO", "reason": "Valero — refining margins spike on crude disruption", "historical_win_rate": 0.67, "holding_period": "day"},
                {"symbol": "MPC", "reason": "Marathon Petroleum — crack spreads widen", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "PSX", "reason": "Phillips 66 — refining + midstream gains", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "PBF", "reason": "PBF Energy — high-beta refiner, big intraday moves", "historical_win_rate": 0.61, "holding_period": "day"},
                # Energy ETFs
                {"symbol": "XLE", "reason": "Energy sector ETF — broad energy rotation", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "UGA", "reason": "Gasoline fund — direct gasoline price exposure", "historical_win_rate": 0.63, "holding_period": "day"},
                # Gold/safe haven
                {"symbol": "GLD", "reason": "Gold rallies on uncertainty — scalp intraday", "historical_win_rate": 0.72, "holding_period": "day"},
                {"symbol": "GDX", "reason": "Gold miners — leveraged gold upside", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "SLV", "reason": "Silver — safe haven + industrial demand", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "GOLD", "reason": "Barrick Gold — major miner, flight to safety", "historical_win_rate": 0.62, "holding_period": "day"},
                # Treasury
                {"symbol": "TLT", "reason": "Long bonds — flight to safety", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "SHY", "reason": "Short-term treasury — parking cash safely", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "BIL", "reason": "T-bill ETF — ultra-safe haven", "historical_win_rate": 0.52, "holding_period": "day"},
                # Cybersecurity (cyberwar)
                {"symbol": "HACK", "reason": "Cyber ETF — cyberwar escalation drives demand", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "CIBR", "reason": "Cybersecurity ETF — nation-state threat premium", "historical_win_rate": 0.61, "holding_period": "day"},
                {"symbol": "PANW", "reason": "Palo Alto — enterprise cyber spending surge", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "CRWD", "reason": "CrowdStrike — endpoint security demand spike", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "FTNT", "reason": "Fortinet — network security on cyberwar fears", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "ZS", "reason": "Zscaler — zero trust demand in conflict", "historical_win_rate": 0.59, "holding_period": "day"},
                # Shipping/maritime (Strait of Hormuz)
                {"symbol": "ZIM", "reason": "ZIM — shipping rates spike on Hormuz risk", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "GOGL", "reason": "Golden Ocean — dry bulk shipping premium", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "INSW", "reason": "International Seaways — tanker rates spike", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "STNG", "reason": "Scorpio Tankers — product tanker demand surge", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "FRO", "reason": "Frontline — crude tanker rates on rerouting", "historical_win_rate": 0.61, "holding_period": "day"},
                # Uranium/nuclear
                {"symbol": "URA", "reason": "Uranium ETF — energy security drives nuclear", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "CCJ", "reason": "Cameco — uranium supply disruption premium", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "UUUU", "reason": "Energy Fuels — domestic uranium demand", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "LEU", "reason": "Centrus Energy — enrichment capacity premium", "historical_win_rate": 0.57, "holding_period": "day"},
                # Aerospace/satellite
                {"symbol": "IRDM", "reason": "Iridium — satellite comms demand in conflict", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "GSAT", "reason": "Globalstar — satellite connectivity plays", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "RDW", "reason": "Redwire — space infrastructure demand", "historical_win_rate": 0.54, "holding_period": "day"},
                # Agriculture/food disruption
                {"symbol": "DBA", "reason": "Agriculture ETF — food supply disruption", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "WEAT", "reason": "Wheat ETF — grain supply chain disruption", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "CORN", "reason": "Corn ETF — agricultural commodity spike", "historical_win_rate": 0.58, "holding_period": "day"},
                # Insurance/reinsurance
                {"symbol": "RE", "reason": "Everest Re — reinsurance pricing power on war risk", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "RNR", "reason": "RenaissanceRe — catastrophe reinsurance demand", "historical_win_rate": 0.57, "holding_period": "day"},
                {"symbol": "ACGL", "reason": "Arch Capital — specialty insurance on conflict", "historical_win_rate": 0.56, "holding_period": "day"},
                # Pharma/medical
                {"symbol": "XLV", "reason": "Healthcare ETF — wartime medical demand", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "JNJ", "reason": "J&J — defensive + medical supply demand", "historical_win_rate": 0.54, "holding_period": "day"},
                # Leveraged volatility
                {"symbol": "UVXY", "reason": "VIX spike — intraday volatility scalp", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "SQQQ", "reason": "3x bearish QQQ — tech selloff on risk-off", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "SPXU", "reason": "3x bearish S&P — broad market fear trade", "historical_win_rate": 0.54, "holding_period": "day"},
            ],
            "short": [
                # Use inverse ETFs for short exposure (day trades)
                {"symbol": "EEM", "reason": "EM capital flight on risk-off", "historical_win_rate": 0.67, "holding_period": "day"},
                {"symbol": "SPY", "reason": "Broad market risk-off via SH inverse", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "QQQ", "reason": "Tech selloff via PSQ inverse", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "IYT", "reason": "Transport disruption — freight logistics hit", "historical_win_rate": 0.62, "holding_period": "day"},
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
        "thesis": "AGGRESSIVE DAY TRADE: Full crisis mode. Heavy leveraged ETFs (UVXY, SQQQ, SPXU), defense, oil, gold. Faster moves = tighter targets. Flat by EOD.",
        "long_sectors": ["gold", "volatility", "leveraged_bearish", "defense", "oil_majors", "gasoline_refining", "shipping", "cybersecurity"],
        "short_sectors": ["broad_market", "high_yield", "cyclicals", "airlines", "transport"],
        "symbols": {
            "long": [
                # Leveraged volatility — PRIMARY in crisis, fast scalps
                {"symbol": "UVXY", "reason": "VIX spike — crisis vol scalp, quick 2-5% targets", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "SQQQ", "reason": "3x bearish QQQ — tech crash momentum", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "SPXU", "reason": "3x bearish S&P — broad market crash play", "historical_win_rate": 0.66, "holding_period": "day"},
                # Gold/safe haven — heavy allocation
                {"symbol": "GLD", "reason": "Safe haven demand spikes — scalp gold moves", "historical_win_rate": 0.78, "holding_period": "day"},
                {"symbol": "GDX", "reason": "Gold miners — leveraged gold upside in crisis", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "SLV", "reason": "Silver safe haven demand", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "GOLD", "reason": "Barrick — major gold miner crisis play", "historical_win_rate": 0.65, "holding_period": "day"},
                # Inverse market ETFs
                {"symbol": "SH", "reason": "Short S&P 500 ETF — direct hedge", "historical_win_rate": 0.75, "holding_period": "day"},
                {"symbol": "PSQ", "reason": "Short QQQ — tech hedge", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "EUM", "reason": "Short EM — emerging market flight", "historical_win_rate": 0.65, "holding_period": "day"},
                # Treasury flight to safety
                {"symbol": "TLT", "reason": "Treasury rally on flight to safety", "historical_win_rate": 0.71, "holding_period": "day"},
                {"symbol": "SHY", "reason": "Short-term treasuries — capital preservation", "historical_win_rate": 0.58, "holding_period": "day"},
                # Defense — war premium accelerates
                {"symbol": "LMT", "reason": "Defense leader — war premium accelerates", "historical_win_rate": 0.74, "holding_period": "day"},
                {"symbol": "RTX", "reason": "Raytheon — missile systems demand in conflict", "historical_win_rate": 0.73, "holding_period": "day"},
                {"symbol": "NOC", "reason": "Northrop — crisis defense demand", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "HII", "reason": "Naval shipbuilder — fleet expansion", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "ITA", "reason": "Defense ETF — broad crisis defense rotation", "historical_win_rate": 0.69, "holding_period": "day"},
                {"symbol": "PLTR", "reason": "Defense AI/intel — wartime demand surge", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "AVAV", "reason": "Drones — active conflict drone demand", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "KTOS", "reason": "Unmanned systems — crisis combat demand", "historical_win_rate": 0.62, "holding_period": "day"},
                # Oil/energy — heavy in crisis
                {"symbol": "XOM", "reason": "Oil major — supply crisis premium", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "CVX", "reason": "Chevron — crude supply disruption", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "VLO", "reason": "Refining margins explode in crisis", "historical_win_rate": 0.72, "holding_period": "day"},
                {"symbol": "MPC", "reason": "Marathon — crack spreads blow out", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "XOP", "reason": "Oil E&P — supply disruption premium", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "UGA", "reason": "Gasoline fund — direct gas price spike", "historical_win_rate": 0.69, "holding_period": "day"},
                {"symbol": "XLE", "reason": "Energy sector — crisis energy rotation", "historical_win_rate": 0.67, "holding_period": "day"},
                # Shipping — Hormuz disruption
                {"symbol": "ZIM", "reason": "Shipping rates explode on Hormuz closure", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "INSW", "reason": "Tanker rates spike on rerouting", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "STNG", "reason": "Product tankers — crisis freight premium", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "FRO", "reason": "Crude tankers — rerouting premium", "historical_win_rate": 0.63, "holding_period": "day"},
                # Cybersecurity — cyberwar escalation
                {"symbol": "PANW", "reason": "Palo Alto — crisis cyber spending surge", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "CRWD", "reason": "CrowdStrike — nation-state cyber attacks", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "HACK", "reason": "Cyber ETF — cyberwar premium", "historical_win_rate": 0.62, "holding_period": "day"},
                # Agriculture — supply chain crisis
                {"symbol": "WEAT", "reason": "Wheat — food supply chain disruption", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "DBA", "reason": "Agriculture — food security premium", "historical_win_rate": 0.62, "holding_period": "day"},
                # Uranium — energy security
                {"symbol": "CCJ", "reason": "Cameco — nuclear energy security", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "URA", "reason": "Uranium ETF — energy independence", "historical_win_rate": 0.58, "holding_period": "day"},
            ],
            "short": [
                {"symbol": "SPY", "reason": "Broad market selloff in crisis", "historical_win_rate": 0.80, "holding_period": "day"},
                {"symbol": "QQQ", "reason": "Tech selloff — risk-off flight", "historical_win_rate": 0.76, "holding_period": "day"},
                {"symbol": "HYG", "reason": "Credit spreads blow out", "historical_win_rate": 0.76, "holding_period": "day"},
                {"symbol": "FXI", "reason": "China selloff on geopolitical risk", "historical_win_rate": 0.72, "holding_period": "day"},
                {"symbol": "EEM", "reason": "EM capital flight in crisis", "historical_win_rate": 0.74, "holding_period": "day"},
                {"symbol": "IYT", "reason": "Transport disruption — supply chain breakdown", "historical_win_rate": 0.68, "holding_period": "day"},
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
        "thesis": "AGGRESSIVE DAY TRADE: De-escalation recovery. Flip: long beaten-down airlines/travel/EM, short defense/oil/gold. Quick scalps on mean reversion. Flat by EOD.",
        "long_sectors": ["airlines", "travel", "emerging_markets", "cyclicals", "tech_recovery"],
        "short_sectors": ["defense", "oil", "gold", "volatility"],
        "symbols": {
            "long": [
                # Recovery longs — beaten-down names bouncing
                {"symbol": "TQQQ", "reason": "3x bullish QQQ — tech recovery momentum", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "SPY", "reason": "Broad market recovery scalp", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "QQQ", "reason": "Tech rebound on de-escalation", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "DAL", "reason": "Delta leads airline recovery", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "BKNG", "reason": "Travel bookings rebound", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "EEM", "reason": "EM risk appetite returns — scalp bounce", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "FXI", "reason": "China recovery on de-escalation", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "HYG", "reason": "Credit spreads compress — junk recovery", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "IYT", "reason": "Transport recovery — supply chains normalize", "historical_win_rate": 0.60, "holding_period": "day"},
            ],
            "short": [
                # Fade the war premium — defense/oil/gold sell off
                {"symbol": "GLD", "reason": "Gold fades as risk appetite returns", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "GDX", "reason": "Gold miners fade on de-escalation", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "VLO", "reason": "Refining margins compress as supply fears ease", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "ITA", "reason": "Defense premium fades on de-escalation", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "XLE", "reason": "Energy premium fades — oil normalizes", "historical_win_rate": 0.56, "holding_period": "day"},
                {"symbol": "RTX", "reason": "Defense sell-off on peace talks", "historical_win_rate": 0.54, "holding_period": "day"},
                {"symbol": "LMT", "reason": "Lockheed war premium unwind", "historical_win_rate": 0.53, "holding_period": "day"},
                {"symbol": "XOM", "reason": "Oil major — crude normalizes", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "UVXY", "reason": "Vol crush — short vol on de-escalation", "historical_win_rate": 0.62, "holding_period": "day"},
            ],
        },
        "historical_examples": [
            {"event": "Post-COVID recovery Apr 2020", "result": "JETS +85%, DAL +60% in 90d"},
            {"event": "Ukraine ceasefire talks Mar 2022", "result": "JETS +15%, EEM +8%, RTX -5% in 14d"},
            {"event": "Post-Gulf War oil normalization 1991", "result": "Airlines +40%, oil -50%, defense -10% in 90d"},
        ],
    },
    "CRISIS_to_ELEVATED": {
        "thesis": "AGGRESSIVE DAY TRADE: Bottom fishing. Long beaten-down names at support. Quick scalps on oversold bounces. Tight targets, no overnight risk.",
        "long_sectors": ["quality_cyclicals", "financials", "energy", "airlines_recovery", "tech_bounce"],
        "short_sectors": [],
        "symbols": {
            "long": [
                # Oversold bounce scalps
                {"symbol": "SPY", "reason": "Broad market mean reversion from oversold", "historical_win_rate": 0.74, "holding_period": "day"},
                {"symbol": "QQQ", "reason": "Tech bounce from crisis lows", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "TQQQ", "reason": "3x bullish QQQ — leveraged tech bounce", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "DAL", "reason": "Airline rebound from crisis lows — scalp", "historical_win_rate": 0.70, "holding_period": "day"},
                {"symbol": "BKNG", "reason": "Travel recovery — oversold bounce", "historical_win_rate": 0.65, "holding_period": "day"},
                {"symbol": "EEM", "reason": "EM bounce from capitulation", "historical_win_rate": 0.63, "holding_period": "day"},
                {"symbol": "FXI", "reason": "China recovery scalp", "historical_win_rate": 0.60, "holding_period": "day"},
                {"symbol": "XLE", "reason": "Energy holds gains — rotation continues", "historical_win_rate": 0.68, "holding_period": "day"},
                {"symbol": "HYG", "reason": "Credit bounce from crisis spreads", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "IYT", "reason": "Transport recovery from crisis lows", "historical_win_rate": 0.60, "holding_period": "day"},
                # Still hold some defense/oil for momentum
                {"symbol": "RTX", "reason": "Defense momentum continues post-crisis", "historical_win_rate": 0.66, "holding_period": "day"},
                {"symbol": "LMT", "reason": "Lockheed — crisis momentum carry", "historical_win_rate": 0.64, "holding_period": "day"},
                {"symbol": "XOM", "reason": "Oil major — elevated but still moving", "historical_win_rate": 0.62, "holding_period": "day"},
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
        "thesis": "AGGRESSIVE DAY TRADE: Low-vol momentum plays. Tech if calm, energy if oil moving. Scalp trending sectors with tight targets. Flat by EOD.",
        "long_sectors": ["momentum", "tech", "growth", "energy_momentum"],
        "short_sectors": [],
        "symbols": {
            "long": [
                {"symbol": "QQQ", "reason": "Tech leadership in calm markets — scalp momentum", "historical_win_rate": 0.62, "holding_period": "day"},
                {"symbol": "SPY", "reason": "Steady uptrend — intraday momentum scalp", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "TQQQ", "reason": "3x QQQ — leveraged tech momentum in calm", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "XLE", "reason": "Energy momentum if oil trending", "historical_win_rate": 0.56, "holding_period": "day"},
                {"symbol": "XOM", "reason": "ExxonMobil — energy momentum play", "historical_win_rate": 0.55, "holding_period": "day"},
                {"symbol": "PANW", "reason": "Cybersecurity momentum — secular trend", "historical_win_rate": 0.58, "holding_period": "day"},
                {"symbol": "CRWD", "reason": "CrowdStrike — growth momentum", "historical_win_rate": 0.57, "holding_period": "day"},
                {"symbol": "PLTR", "reason": "Palantir — AI/defense secular trend", "historical_win_rate": 0.56, "holding_period": "day"},
            ],
            "short": [],
        },
        "historical_examples": [
            {"event": "2021 low-vol rally", "result": "QQQ +27%, SPY +28%"},
        ],
    },
}


# --- Medium/Long-Term Macro Playbook ---
# Maps same regime transitions to swing/position trades (2-12 week holds)
# Focused on ETFs and sector leaders with wider targets/stops
MEDIUM_LONG_PLAYBOOK = {
    "NORMAL_to_ELEVATED": {
        "thesis": "MEDIUM-TERM MACRO: Risk-off regime shift beginning. Build positions in defense ETFs, gold, oil majors, and treasuries. Reduce exposure to travel/airlines. Hold 2-8 weeks through the elevated regime.",
        "long_sectors": ["defense_etfs", "gold", "oil_majors", "treasuries"],
        "short_sectors": ["airlines"],
        "symbols": {
            "long": [
                {"symbol": "ITA", "reason": "Defense ETF — broad sector rotation into military spending, hold through elevated regime", "historical_win_rate": 0.70, "holding_period": "swing"},
                {"symbol": "PPA", "reason": "Aerospace & Defense ETF — diversified defense exposure for multi-week hold", "historical_win_rate": 0.68, "holding_period": "swing"},
                {"symbol": "GLD", "reason": "Gold — macro safe haven, accumulate on dips through risk-off period", "historical_win_rate": 0.75, "holding_period": "swing"},
                {"symbol": "GDX", "reason": "Gold miners — leveraged gold upside with 2-4 week holding horizon", "historical_win_rate": 0.67, "holding_period": "swing"},
                {"symbol": "XOM", "reason": "ExxonMobil — oil major benefiting from sustained supply disruption premium", "historical_win_rate": 0.69, "holding_period": "swing"},
                {"symbol": "CVX", "reason": "Chevron — crude supply risk premium builds over weeks, not hours", "historical_win_rate": 0.67, "holding_period": "swing"},
                {"symbol": "TLT", "reason": "Long treasuries — flight to safety trade, hold through uncertainty", "historical_win_rate": 0.66, "holding_period": "swing"},
                {"symbol": "XLE", "reason": "Energy sector ETF — broad energy rotation with multi-week momentum", "historical_win_rate": 0.65, "holding_period": "swing"},
            ],
            "short": [
                {"symbol": "JETS", "reason": "Airlines ETF — sustained travel demand destruction via inverse/puts, hold 2-6 weeks", "historical_win_rate": 0.70, "holding_period": "swing"},
                {"symbol": "EEM", "reason": "Emerging markets — capital flight persists through elevated regime", "historical_win_rate": 0.65, "holding_period": "swing"},
            ],
        },
        "historical_examples": [
            {"event": "Russia-Ukraine Feb 2022", "result": "ITA +14%, GLD +8%, XOM +25%, JETS -20% over 8 weeks"},
            {"event": "Iran-Israel escalation Apr 2024", "result": "GLD +6%, XLE +8%, TLT +3% over 4 weeks"},
            {"event": "Iran War 2026", "result": "ITA +14%, GLD +10%, XLE +18%, JETS -25% over 6 weeks"},
        ],
    },
    "ELEVATED_to_CRISIS": {
        "thesis": "MEDIUM-TERM MACRO: Full crisis positioning. Heavy gold and treasuries, inverse equity ETFs. Minimize equity exposure. Hold through crisis until regime begins to normalize.",
        "long_sectors": ["gold", "treasuries", "inverse_equity"],
        "short_sectors": ["broad_market"],
        "symbols": {
            "long": [
                {"symbol": "GLD", "reason": "Gold — primary safe haven, accumulate aggressively, hold through crisis", "historical_win_rate": 0.82, "holding_period": "swing"},
                {"symbol": "GDX", "reason": "Gold miners — leveraged gold upside, hold through entire crisis period", "historical_win_rate": 0.74, "holding_period": "swing"},
                {"symbol": "TLT", "reason": "Long treasuries — flight to safety accelerates, hold until crisis abates", "historical_win_rate": 0.76, "holding_period": "swing"},
                {"symbol": "SH", "reason": "Inverse S&P 500 — portfolio hedge, hold through crisis drawdown", "historical_win_rate": 0.78, "holding_period": "swing"},
                {"symbol": "PSQ", "reason": "Inverse QQQ — tech hedge for extended crisis, no leverage decay concern for weeks", "historical_win_rate": 0.73, "holding_period": "swing"},
                {"symbol": "ITA", "reason": "Defense ETF — war premium accelerates and sustains through crisis", "historical_win_rate": 0.72, "holding_period": "swing"},
            ],
            "short": [
                {"symbol": "SPY", "reason": "Broad market — sustained selloff through crisis via puts or inverse", "historical_win_rate": 0.80, "holding_period": "swing"},
                {"symbol": "QQQ", "reason": "Tech — growth multiple compression through crisis", "historical_win_rate": 0.76, "holding_period": "swing"},
                {"symbol": "EEM", "reason": "Emerging markets — sustained capital flight in crisis", "historical_win_rate": 0.74, "holding_period": "swing"},
            ],
        },
        "historical_examples": [
            {"event": "COVID March 2020", "result": "GLD +8%, TLT +20%, SPY -34% over 5 weeks"},
            {"event": "GFC Sept-Nov 2008", "result": "GLD +25%, TLT +30%, SPY -40% over 3 months"},
            {"event": "Gulf War I 1990-91", "result": "GLD +10%, defense +25%, SPY -17% over 3 months"},
        ],
    },
    "ELEVATED_to_NORMAL": {
        "thesis": "MEDIUM-TERM MACRO: De-escalation recovery positioning. Rotate into beaten-down equity ETFs, travel recovery, emerging markets. Sell defense/gold positions. Hold 4-12 weeks for full mean reversion.",
        "long_sectors": ["broad_equity", "travel_recovery", "emerging_markets"],
        "short_sectors": ["defense", "gold"],
        "symbols": {
            "long": [
                {"symbol": "SPY", "reason": "Broad market recovery — accumulate for multi-week rebound", "historical_win_rate": 0.72, "holding_period": "swing"},
                {"symbol": "QQQ", "reason": "Tech recovery — growth re-rates higher as risk normalizes", "historical_win_rate": 0.70, "holding_period": "swing"},
                {"symbol": "JETS", "reason": "Airlines ETF — travel demand recovery over 4-8 weeks", "historical_win_rate": 0.73, "holding_period": "swing"},
                {"symbol": "EEM", "reason": "Emerging markets — capital flows return as risk appetite recovers", "historical_win_rate": 0.66, "holding_period": "swing"},
                {"symbol": "XLK", "reason": "Tech sector ETF — secular growth resumes post de-escalation", "historical_win_rate": 0.68, "holding_period": "swing"},
                {"symbol": "HYG", "reason": "High yield — credit spreads compress over weeks as confidence returns", "historical_win_rate": 0.64, "holding_period": "swing"},
            ],
            "short": [
                {"symbol": "GLD", "reason": "Gold — safe haven premium unwinds over weeks", "historical_win_rate": 0.62, "holding_period": "swing"},
                {"symbol": "ITA", "reason": "Defense ETF — war premium fades on de-escalation, sell over 4-8 weeks", "historical_win_rate": 0.58, "holding_period": "swing"},
            ],
        },
        "historical_examples": [
            {"event": "Post-COVID recovery Apr-Jul 2020", "result": "SPY +40%, JETS +85%, EEM +25% over 12 weeks"},
            {"event": "Ukraine ceasefire talks Mar 2022", "result": "SPY +8%, JETS +15%, EEM +8% over 6 weeks"},
        ],
    },
    "CRISIS_to_ELEVATED": {
        "thesis": "MEDIUM-TERM MACRO: Crisis abating, begin building recovery positions. Long beaten-down quality equities and energy. Hold 4-12 weeks for recovery rally. Keep some hedges until regime fully normalizes.",
        "long_sectors": ["broad_equity", "energy", "quality"],
        "short_sectors": [],
        "symbols": {
            "long": [
                {"symbol": "SPY", "reason": "Broad market — buy the crisis bottom, hold 4-12 weeks for recovery", "historical_win_rate": 0.78, "holding_period": "swing"},
                {"symbol": "QQQ", "reason": "Tech — oversold quality rebounds hardest, hold for full recovery", "historical_win_rate": 0.75, "holding_period": "swing"},
                {"symbol": "XLE", "reason": "Energy — elevated prices sustain, hold through recovery phase", "historical_win_rate": 0.70, "holding_period": "swing"},
                {"symbol": "XLF", "reason": "Financials — rate normalization benefits banks, hold 4-8 weeks", "historical_win_rate": 0.66, "holding_period": "swing"},
                {"symbol": "JETS", "reason": "Airlines — early recovery position, hold 8-12 weeks for full rebound", "historical_win_rate": 0.68, "holding_period": "swing"},
                {"symbol": "EEM", "reason": "Emerging markets — capital flows resume post-crisis, hold 6-12 weeks", "historical_win_rate": 0.64, "holding_period": "swing"},
            ],
            "short": [],
        },
        "historical_examples": [
            {"event": "Post-GFC recovery Mar 2009", "result": "SPY +40%, XLF +80% over 12 weeks"},
            {"event": "Post-COVID recovery Mar 2020", "result": "SPY +30%, QQQ +35%, JETS +60% over 12 weeks"},
        ],
    },
    "NORMAL_steady": {
        "thesis": "MEDIUM-TERM MACRO: Low volatility, trend-following positioning. Long momentum sectors — tech and broad market. Hold 4-8 weeks, ride the trend with trailing stops.",
        "long_sectors": ["momentum", "tech", "broad_market"],
        "short_sectors": [],
        "symbols": {
            "long": [
                {"symbol": "QQQ", "reason": "Tech momentum — secular growth in calm regime, hold 4-8 weeks", "historical_win_rate": 0.65, "holding_period": "swing"},
                {"symbol": "SPY", "reason": "Broad market uptrend — accumulate on dips, hold 4-8 weeks", "historical_win_rate": 0.63, "holding_period": "swing"},
                {"symbol": "XLK", "reason": "Tech sector ETF — sector momentum in low-vol environment", "historical_win_rate": 0.64, "holding_period": "swing"},
                {"symbol": "SMH", "reason": "Semiconductor ETF — AI/chip cycle momentum, hold 4-8 weeks", "historical_win_rate": 0.62, "holding_period": "swing"},
                {"symbol": "XLE", "reason": "Energy — if oil trending, ride the momentum 4-6 weeks", "historical_win_rate": 0.58, "holding_period": "swing"},
            ],
            "short": [],
        },
        "historical_examples": [
            {"event": "2021 low-vol rally", "result": "QQQ +27%, SPY +28% over 12 months"},
            {"event": "2024 Q4 tech momentum", "result": "QQQ +12%, XLK +14% over 8 weeks"},
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
        strategy_type: str = "day_trade",
    ) -> Dict[str, Any]:
        """Generate trade analysis from current scorecard and market data.

        Args:
            strategy_type: "day_trade" for intraday scalps (default) or
                           "medium_long" for swing/position trades (2-12 week holds).
        """
        mode = scorecard.get("mode", "NORMAL")
        regime_p = scorecard.get("regime_shift_probability", 0)
        components = scorecard.get("component_scores", {})
        confidence = scorecard.get("confidence", 0)
        evidence = scorecard.get("evidence", [])
        tw = scorecard.get("time_window", {})

        # Determine regime transition key
        transition = self._detect_transition(mode, previous_mode, regime_p)
        market_data_assessment = self._assess_market_data(microstructure or {})

        # Select playbook based on strategy type
        if strategy_type == "medium_long":
            playbook_source = MEDIUM_LONG_PLAYBOOK
        else:
            playbook_source = REGIME_PLAYBOOK
        playbook = playbook_source.get(transition, playbook_source.get("NORMAL_steady", {}))

        # Generate trade ideas with price levels
        ideas = self._generate_ideas(
            playbook,
            microstructure,
            components,
            confidence,
            strategy_type,
            market_data_assessment,
        )

        # Sector analysis
        sector_analysis = self._sector_rotation_analysis(components, mode)

        # Risk assessment
        risk_assessment = self._risk_assessment(
            regime_p,
            confidence,
            mode,
            tw,
            strategy_type,
            market_data_assessment,
        )

        return {
            "timestamp_utc": iso_now(),
            "mode": mode,
            "regime_p": regime_p,
            "transition": transition,
            "strategy_type": strategy_type,
            "playbook_thesis": playbook.get("thesis", ""),
            "trade_ideas": ideas,
            "sector_analysis": sector_analysis,
            "risk_assessment": risk_assessment,
            "historical_examples": playbook.get("historical_examples", []),
            "evidence_summary": evidence[:5],
            "confidence": confidence,
            "market_data_assessment": market_data_assessment,
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

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _extract_symbol_timestamp(self, sym_data: Dict[str, Any]) -> Optional[datetime]:
        for field in TIMESTAMP_FIELDS:
            parsed = self._parse_timestamp(sym_data.get(field))
            if parsed:
                return parsed
        return None

    def _extract_market_data_snapshot(self, sym_data: Dict[str, Any]) -> Dict[str, Any]:
        price = None
        price_source = None
        for field in PRICE_FIELDS:
            raw_value = sym_data.get(field)
            if isinstance(raw_value, (int, float)) and raw_value > 0:
                price = float(raw_value)
                price_source = field
                break

        sigma = None
        sigma_source = None
        for field in VOLATILITY_FIELDS:
            raw_value = sym_data.get(field)
            if isinstance(raw_value, (int, float)) and raw_value > 0:
                sigma = float(raw_value)
                sigma_source = field
                break

        adv = sym_data.get("adv_shares")
        adv_val = float(adv) if isinstance(adv, (int, float)) and adv > 0 else None
        timestamp = self._extract_symbol_timestamp(sym_data)
        freshness_hours = None
        if timestamp:
            freshness_hours = max((datetime.now(timezone.utc) - timestamp).total_seconds() / 3600.0, 0.0)

        has_price_levels = bool(price is not None and sigma is not None and price > 0 and sigma > 0)
        stale = freshness_hours is not None and freshness_hours >= STALE_MARKET_DATA_HOURS
        very_stale = freshness_hours is not None and freshness_hours >= VERY_STALE_MARKET_DATA_HOURS

        warnings: List[str] = []
        if price is None:
            warnings.append("no usable price field")
        if sigma is None:
            warnings.append("no usable volatility field")
        if adv_val is None:
            warnings.append("no ADV field")
        if freshness_hours is None:
            warnings.append("no market-data timestamp")
        elif stale:
            warnings.append(f"market-data timestamp is stale ({freshness_hours:.1f}h old)")

        return {
            "has_price": price is not None,
            "has_volatility": sigma is not None,
            "has_price_levels": has_price_levels,
            "price": round(price, 2) if price is not None else None,
            "price_source": price_source,
            "sigma_daily_pct": round(sigma, 4) if sigma is not None else None,
            "sigma_source": sigma_source,
            "adv_shares": round(adv_val, 0) if adv_val is not None else None,
            "timestamp_utc": timestamp.isoformat() if timestamp else None,
            "freshness_hours": round(freshness_hours, 2) if freshness_hours is not None else None,
            "is_stale": stale,
            "is_very_stale": very_stale,
            "freshness_status": "fresh"
            if has_price_levels and not stale
            else "stale"
            if stale
            else "partial"
            if price is not None or sigma is not None
            else "missing",
            "warnings": warnings,
            "source": sym_data.get("source"),
        }

    def _assess_market_data(self, microstructure: Dict[str, Any]) -> Dict[str, Any]:
        symbols = microstructure if isinstance(microstructure, dict) else {}
        snapshots: Dict[str, Dict[str, Any]] = {}
        stale_symbols: List[str] = []
        missing_symbols: List[str] = []
        price_ready_symbols: List[str] = []

        for symbol, sym_data in symbols.items():
            if not isinstance(sym_data, dict):
                continue
            snapshot = self._extract_market_data_snapshot(sym_data)
            snapshots[symbol] = snapshot
            if snapshot["has_price_levels"]:
                price_ready_symbols.append(symbol)
            if snapshot["is_stale"] or snapshot["is_very_stale"]:
                stale_symbols.append(symbol)
            if snapshot["freshness_status"] == "missing":
                missing_symbols.append(symbol)

        total = len(snapshots)
        partial_count = sum(1 for v in snapshots.values() if v["freshness_status"] == "partial")
        status = "missing"
        if total == 0:
            status = "missing"
        elif stale_symbols:
            status = "stale"
        elif partial_count > 0:
            status = "partial"
        else:
            status = "fresh"

        warnings: List[str] = []
        if total == 0:
            warnings.append("no market microstructure data available")
        elif stale_symbols:
            warnings.append(f"{len(stale_symbols)} symbol(s) have stale microstructure data")
        elif partial_count > 0:
            warnings.append(f"{partial_count} symbol(s) are missing price or volatility inputs")

        return {
            "status": status,
            "total_symbols": total,
            "price_ready_symbols": len(price_ready_symbols),
            "partial_symbols": partial_count,
            "stale_symbols": len(stale_symbols),
            "missing_symbols": len(missing_symbols),
            "price_ready_ratio": round(len(price_ready_symbols) / total, 3) if total else 0.0,
            "warnings": warnings,
            "symbols": snapshots,
        }

    def _generate_ideas(
        self,
        playbook: Dict,
        microstructure: Optional[Dict],
        components: Dict[str, float],
        confidence: float,
        strategy_type: str = "day_trade",
        market_data_assessment: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        ideas = []
        micro = microstructure or {}
        market_data_assessment = market_data_assessment or self._assess_market_data(micro)

        for side in ["long", "short"]:
            for entry in playbook.get("symbols", {}).get(side, []):
                sym = entry["symbol"]
                sym_data = micro.get(sym) if isinstance(micro, dict) else None
                data_snapshot = self._extract_market_data_snapshot(sym_data) if isinstance(sym_data, dict) else {
                    "has_price": False,
                    "has_volatility": False,
                    "has_price_levels": False,
                    "price": None,
                    "price_source": None,
                    "sigma_daily_pct": None,
                    "sigma_source": None,
                    "adv_shares": None,
                    "timestamp_utc": None,
                    "freshness_hours": None,
                    "is_stale": False,
                    "is_very_stale": False,
                    "freshness_status": "missing",
                    "warnings": ["symbol not present in microstructure cache"],
                    "source": None,
                }

                quality_factor = 1.0
                if data_snapshot["freshness_status"] == "missing":
                    quality_factor = 0.70
                elif data_snapshot["freshness_status"] == "partial":
                    quality_factor = 0.82
                elif data_snapshot["is_stale"]:
                    quality_factor = 0.60

                base_score = entry.get("historical_win_rate", 0.5) * min(confidence, 1.0) * quality_factor
                data_backed = bool(data_snapshot["has_price_levels"])
                idea = {
                    "symbol": sym,
                    "side": side,
                    "reason": entry["reason"],
                    "historical_win_rate": entry.get("historical_win_rate", 0),
                    "confidence_adjusted_score": round(base_score, 2),
                    "holding_period": entry.get("holding_period", "day"),
                    "strategy_style": f"regime_playbook_{strategy_type}",
                    "advisory_only": True,
                    "data_backed": data_backed,
                    "market_data": data_snapshot,
                    "data_quality_factor": round(quality_factor, 2),
                }

                if data_snapshot["has_price_levels"]:
                    price = data_snapshot["price"] or 0.0
                    sigma = data_snapshot["sigma_daily_pct"] or 0.0
                    idea["current_price"] = round(price, 2)
                    idea["daily_vol_pct"] = round(sigma, 2)
                    idea["adv_shares"] = data_snapshot["adv_shares"]
                    idea["price_source"] = data_snapshot["price_source"]
                    idea["volatility_source"] = data_snapshot["sigma_source"]
                    idea["market_data_timestamp_utc"] = data_snapshot["timestamp_utc"]
                    idea["market_data_freshness_hours"] = data_snapshot["freshness_hours"]

                    atr_est = price * sigma / 100.0

                    if strategy_type == "medium_long":
                        # Wider targets/stops for swing trades: 3 ATR target, 1.5 ATR stop (2:1 R:R)
                        target_mult = 3.0
                        stop_mult = 1.5
                    else:
                        # Tight targets/stops for day trades: 1.5 ATR target, 0.75 ATR stop (2:1 R:R)
                        target_mult = 1.5
                        stop_mult = 0.75

                    if side == "long":
                        idea["entry"] = round(price, 2)
                        idea["target"] = round(price + target_mult * atr_est, 2)
                        idea["stop"] = round(price - stop_mult * atr_est, 2)
                    else:
                        idea["entry"] = round(price, 2)
                        idea["target"] = round(price - target_mult * atr_est, 2)
                        idea["stop"] = round(price + stop_mult * atr_est, 2)

                    idea["risk_reward"] = round(target_mult / stop_mult, 2)
                    idea["supporting_data"] = (
                        f"{sym}: {price:.2f} via {data_snapshot['price_source']}, "
                        f"sigma={sigma:.2f}% via {data_snapshot['sigma_source']}, "
                        f"ADV={data_snapshot['adv_shares'] or 0:,.0f}"
                    )
                else:
                    idea["market_data_note"] = (
                        "No full price envelope available; validate live quote and volatility "
                        "before sizing."
                    )
                    idea["supporting_data"] = f"{sym}: market data incomplete ({'; '.join(data_snapshot['warnings'])})"
                    idea["reason"] = (
                        f"{entry['reason']} | Market-data caveat: {', '.join(data_snapshot['warnings'])}"
                    )

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
        self,
        regime_p: float,
        confidence: float,
        mode: str,
        tw: Dict,
        strategy_type: str = "day_trade",
        market_data_assessment: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Position sizing recommendation based on regime and strategy type
        if mode == "CRISIS":
            max_position_pct = 25
            sizing = "Crisis volatility — up to 25% per position, aggressive upside capture"
        elif mode == "ELEVATED":
            if strategy_type == "medium_long":
                max_position_pct = 50
                sizing = "Elevated swing — up to 50% per position, capture macro dislocations"
            else:
                max_position_pct = 30
                sizing = "Elevated day — up to 30% per position, volatility premium"
        elif mode == "MANUAL_REVIEW":
            max_position_pct = 0
            sizing = "SUSPENDED — awaiting manual review"
        else:
            if strategy_type == "medium_long":
                max_position_pct = 100
                sizing = "Unlimited — full conviction swing, optimize for upside"
            else:
                max_position_pct = 50
                sizing = "Aggressive — up to 50% per position, maximize volatility upside"

        # Time window impact
        window = tw.get("current_window", "unknown")
        window_quality = tw.get("window_priority", "unknown")
        market_data_assessment = market_data_assessment or {}
        market_data_status = market_data_assessment.get("status", "missing")

        risk_factors = self._identify_risk_factors(regime_p, confidence, mode)
        if market_data_status in ("missing", "partial"):
            risk_factors.append("Market microstructure incomplete — size down or wait for fresh price levels")
        elif market_data_status == "stale":
            risk_factors.append("Market microstructure stale — use live quote confirmation before acting")

        return {
            "regime_p": round(regime_p, 3),
            "confidence": round(confidence, 3),
            "mode": mode,
            "position_sizing": sizing,
            "max_position_pct": max_position_pct,
            "time_window": window,
            "window_quality": window_quality,
            "market_data_status": market_data_status,
            "risk_factors": risk_factors,
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
    p.add_argument("--strategy", default="day_trade", choices=["day_trade", "medium_long"],
                    help="Strategy type: day_trade (intraday) or medium_long (swing 2-12 weeks)")
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
    micro = load_latest_microstructure_cache(repo_root)

    result = engine.analyze(sc, microstructure=micro, strategy_type=args.strategy)
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
