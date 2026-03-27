"""Live chokepoint-risk scenarios for the 2026 Iran-US war regime.

This module is research-only and provides:
- static definitions for the three chokepoints under active monitoring
- combined scenario playbooks for additive trade-analysis metadata
- analog entries that can be merged into the crisis analog library
- a per-cycle composite risk score from live bridge outputs
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .crisis_training_dataset import event_to_analog_entry


EXECUTION_BOUNDARY: Dict[str, Any] = {
    "informational_only": True,
    "not_for_direct_execution": True,
    "execution_influence_forbidden": True,
    "decision_role": "context_only",
    "authority": "advisory_monitoring_only",
}


CHOKEPOINTS: Dict[str, Dict[str, Any]] = {
    "hormuz": {
        "name": "Strait of Hormuz",
        "region": "Persian Gulf -> Arabian Sea",
        "global_oil_pct": 20.0,
        "daily_barrels_million": 17.0,
        "key_exporters": ["Saudi Arabia", "UAE", "Kuwait", "Iraq", "Qatar"],
        "threat_actor": "Iran",
        "threat_methods": [
            "naval_mines",
            "anti_ship_missiles",
            "fast_attack_boats",
            "drone_swarms",
        ],
        "iran_position": "northern_shore_direct_control",
        "disruption_severity": 1.0,
        "monitoring_signals": {
            "oil_price_spike": {"asset": "CL", "threshold_pct": 5.0, "timeframe": "1d"},
            "tanker_rerouting": {
                "source": "maritime_bridge",
                "field": "hormuz_traffic",
            },
            "iran_naval_activity": {
                "source": "gdelt_bridge",
                "keywords": ["iran", "navy", "hormuz", "strait"],
            },
            "insurance_rates": {"indicator": "war_risk_premium_persian_gulf"},
            "us_naval_presence": {
                "source": "aviation_bridge",
                "keywords": ["carrier_group", "persian_gulf"],
            },
        },
        "trading_playbook": {
            "disruption_confirmed": {
                "immediate_longs": [
                    {
                        "symbol": "USO",
                        "reason": "direct oil price exposure",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "XLE",
                        "reason": "energy sector broad",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "XOP",
                        "reason": "E&P companies benefit most from oil spike",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "OXY",
                        "reason": "US shale producer repricing",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "GLD",
                        "reason": "flight to safety",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "GDX",
                        "reason": "gold miners leveraged to gold",
                        "target_allocation_pct": 1.0,
                    },
                    {
                        "symbol": "UVXY",
                        "reason": "volatility spike",
                        "target_allocation_pct": 1.0,
                    },
                    {
                        "symbol": "LMT",
                        "reason": "defense contractor",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "RTX",
                        "reason": "missile defense systems",
                        "target_allocation_pct": 1.5,
                    },
                ],
                "immediate_shorts": [
                    {
                        "symbol": "JETS",
                        "reason": "airlines hit by fuel costs",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "UAL",
                        "reason": "airline fuel exposure",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "DAL",
                        "reason": "airline fuel exposure",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "EEM",
                        "reason": "emerging-market capital flight",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "EZU",
                        "reason": "Europe energy dependence",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "SPY",
                        "reason": "broad market risk-off",
                        "target_allocation_pct": 1.0,
                    },
                ],
                "hold_period": "3-10 days for initial spike, reassess weekly",
                "exit_signals": [
                    "ceasefire announcement",
                    "US-Iran diplomatic channel opened",
                    "oil falls 10% from peak",
                    "VIX drops below 25 from peak",
                ],
                "intraday_pattern": (
                    "gap_up_energy_at_open_momentum_continues_first_2_hours_"
                    "then_profit_taking"
                ),
            },
            "threat_escalation": {
                "immediate_longs": [
                    {
                        "symbol": "USO",
                        "reason": "oil fear premium building",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "GLD",
                        "reason": "safe-haven bid",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "UVXY",
                        "reason": "vol expansion",
                        "target_allocation_pct": 1.0,
                    },
                ],
                "hold_period": "1-3 days, tight stops",
                "exit_signals": [
                    "de-escalation rhetoric",
                    "threat assessment downgraded",
                ],
            },
        },
    },
    "bab_el_mandeb": {
        "name": "Bab el-Mandeb Strait",
        "region": "Red Sea -> Gulf of Aden",
        "global_trade_pct": 12.0,
        "suez_gateway": True,
        "alternative_route": "Cape of Good Hope (+2-3 weeks, +$1M per voyage)",
        "threat_actor": "Houthis (Iran-backed)",
        "threat_methods": [
            "anti_ship_missiles",
            "naval_mines",
            "drone_boats",
            "ballistic_missiles",
        ],
        "disruption_severity": 0.85,
        "adjacent_nations": ["Yemen", "Djibouti", "Eritrea"],
        "monitoring_signals": {
            "shipping_rerouting": {
                "source": "maritime_bridge",
                "field": "suez_traffic_decline",
            },
            "houthi_attacks": {
                "source": "gdelt_bridge",
                "keywords": ["houthi", "red sea", "shipping", "attack"],
            },
            "freight_rates": {"indicator": "container_freight_rate_spike"},
            "insurance_war_risk": {"indicator": "war_risk_premium_red_sea"},
            "us_naval_ops": {
                "source": "aviation_bridge",
                "keywords": ["Prosperity_Guardian", "Red_Sea"],
            },
        },
        "trading_playbook": {
            "disruption_confirmed": {
                "immediate_longs": [
                    {
                        "symbol": "ZIM",
                        "reason": "shipping rate spike",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "SBLK",
                        "reason": "dry-bulk rerouting demand",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "USO",
                        "reason": "oil tanker rerouting tightens supply",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "XLE",
                        "reason": "energy on supply fear",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "GLD",
                        "reason": "geopolitical safe haven",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "LMT",
                        "reason": "naval defense systems",
                        "target_allocation_pct": 1.0,
                    },
                ],
                "immediate_shorts": [
                    {
                        "symbol": "EZU",
                        "reason": "Europe exposed to Suez disruption",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "EEM",
                        "reason": "trade-route disruption",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "JETS",
                        "reason": "fuel cost and route disruption",
                        "target_allocation_pct": 1.0,
                    },
                ],
                "hold_period": "weeks; shipping disruptions are slow-burn",
                "exit_signals": [
                    "Houthi attacks cease for 2+ weeks",
                    "shipping insurance rates normalize",
                    "container freight rates decline 20% from peak",
                ],
                "intraday_pattern": "shipping_stocks_grind_up_over_days_not_gap_events",
            },
        },
    },
    "eastern_med_gas": {
        "name": "Eastern Mediterranean Gas Fields",
        "region": "Eastern Mediterranean Sea",
        "key_fields": ["Leviathan", "Tamar", "Zohr"],
        "supply_destinations": ["Israel", "Egypt", "Europe"],
        "europe_strategy": "reduce_russian_gas_dependence",
        "threat_actors": ["Hezbollah", "Iran-backed naval forces"],
        "threat_methods": [
            "missile_strikes_on_platforms",
            "pipeline_sabotage",
            "naval_blockade",
        ],
        "disruption_severity": 0.7,
        "monitoring_signals": {
            "hezbollah_escalation": {
                "source": "gdelt_bridge",
                "keywords": ["hezbollah", "lebanon", "israel", "gas", "mediterranean"],
            },
            "gas_price_spike": {"asset": "NG", "threshold_pct": 10.0, "timeframe": "1d"},
            "platform_evacuation": {
                "source": "exa_ai_bridge",
                "keywords": ["gas platform evacuation", "Leviathan", "Tamar"],
            },
            "european_gas_crisis": {"indicator": "ttf_gas_price_spike"},
            "naval_activity_med": {
                "source": "maritime_bridge",
                "field": "med_naval_activity",
            },
        },
        "trading_playbook": {
            "disruption_confirmed": {
                "immediate_longs": [
                    {
                        "symbol": "UNG",
                        "reason": "natural gas price spike",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "LNG",
                        "reason": "LNG shipping demand surge",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "XLE",
                        "reason": "broad energy repricing",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "GLD",
                        "reason": "safe haven",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "URA",
                        "reason": "alternative-energy bid",
                        "target_allocation_pct": 1.0,
                    },
                ],
                "immediate_shorts": [
                    {
                        "symbol": "EZU",
                        "reason": "Europe energy shock",
                        "target_allocation_pct": 2.0,
                    },
                    {
                        "symbol": "EIS",
                        "reason": "Israel ETF direct exposure",
                        "target_allocation_pct": 1.5,
                    },
                    {
                        "symbol": "EUFN",
                        "reason": "European financials on gas stress",
                        "target_allocation_pct": 1.0,
                    },
                ],
                "hold_period": "weeks to months if pipeline damage is confirmed",
                "exit_signals": [
                    "ceasefire on northern Israel/Lebanon border",
                    "gas platforms resume operations",
                    "European gas reserves above 80%",
                ],
            },
        },
    },
}


COMBINED_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "hormuz_only": {
        "name": "Hormuz Disruption Only",
        "probability_current": 0.35,
        "severity": 0.8,
        "oil_impact_pct": 15.0,
        "sp500_impact_pct": -5.0,
        "vix_target": 30,
        "duration_estimate_days": 14,
        "playbook_ref": "hormuz.disruption_confirmed",
    },
    "hormuz_plus_bab_el_mandeb": {
        "name": "Dual Chokepoint: Hormuz + Bab el-Mandeb",
        "probability_current": 0.20,
        "severity": 0.95,
        "oil_impact_pct": 30.0,
        "sp500_impact_pct": -10.0,
        "vix_target": 40,
        "gas_impact_pct": 20.0,
        "shipping_disruption": "severe",
        "duration_estimate_days": 30,
        "combined_playbook": {
            "immediate_longs": [
                {"symbol": "USO", "allocation": 2.0},
                {"symbol": "XLE", "allocation": 2.0},
                {"symbol": "XOP", "allocation": 2.0},
                {"symbol": "ZIM", "allocation": 2.0},
                {"symbol": "GLD", "allocation": 2.0},
                {"symbol": "UVXY", "allocation": 1.0},
                {"symbol": "LMT", "allocation": 1.0},
            ],
            "immediate_shorts": [
                {"symbol": "JETS", "allocation": 2.0},
                {"symbol": "EZU", "allocation": 2.0},
                {"symbol": "EEM", "allocation": 1.5},
                {"symbol": "SPY", "allocation": 1.0},
            ],
            "max_gross_exposure_pct": 10.0,
            "hold_period": "1-4 weeks, reassess as the situation develops",
            "critical_exit": "UN Security Council resolution or US-Iran direct talks",
        },
    },
    "triple_chokepoint": {
        "name": "Triple Chokepoint Crisis: Hormuz + Bab el-Mandeb + Med Gas",
        "probability_current": 0.10,
        "severity": 1.0,
        "oil_impact_pct": 50.0,
        "sp500_impact_pct": -15.0,
        "vix_target": 55,
        "gas_impact_pct": 40.0,
        "shipping_disruption": "global",
        "inflation_impact": "immediate 2-3% CPI spike within 2 months",
        "duration_estimate_days": 60,
        "combined_playbook": {
            "immediate_longs": [
                {"symbol": "USO", "allocation": 2.0, "note": "oil to $150+"},
                {"symbol": "XLE", "allocation": 2.0, "note": "energy sector repricing"},
                {"symbol": "UNG", "allocation": 1.5, "note": "nat gas crisis"},
                {"symbol": "GLD", "allocation": 2.0, "note": "ultimate safe haven"},
                {"symbol": "GDX", "allocation": 1.0, "note": "gold miners leveraged"},
                {"symbol": "UVXY", "allocation": 1.0, "note": "VIX to 55+"},
                {"symbol": "LMT", "allocation": 1.0, "note": "defense"},
                {"symbol": "RTX", "allocation": 1.0, "note": "missile defense"},
                {"symbol": "TLT", "allocation": 1.0, "note": "flight to treasuries"},
            ],
            "immediate_shorts": [
                {"symbol": "JETS", "allocation": 2.0, "note": "airlines destroyed"},
                {"symbol": "EZU", "allocation": 2.0, "note": "Europe energy crisis"},
                {"symbol": "EEM", "allocation": 2.0, "note": "EM capital flight"},
                {"symbol": "EIS", "allocation": 1.0, "note": "Israel direct exposure"},
                {"symbol": "EUFN", "allocation": 1.0, "note": "European banks on energy shock"},
            ],
            "max_gross_exposure_pct": 10.0,
            "position_sizing": "1% per position; extreme uncertainty",
            "hold_period": "weeks to months; regime-change event",
            "critical_exit": (
                "comprehensive ceasefire or naval escort guarantee "
                "for all three chokepoints"
            ),
            "warning": "Capital preservation mode. Protect capital first.",
        },
    },
    "bab_el_mandeb_only": {
        "name": "Bab el-Mandeb / Red Sea Disruption Only",
        "probability_current": 0.50,
        "severity": 0.6,
        "oil_impact_pct": 5.0,
        "sp500_impact_pct": -2.0,
        "vix_target": 22,
        "shipping_disruption": "moderate",
        "duration_estimate_days": 90,
        "playbook_ref": "bab_el_mandeb.disruption_confirmed",
    },
    "med_gas_only": {
        "name": "Eastern Med Gas Disruption Only",
        "probability_current": 0.15,
        "severity": 0.5,
        "gas_impact_pct": 15.0,
        "sp500_impact_pct": -3.0,
        "vix_target": 25,
        "duration_estimate_days": 45,
        "playbook_ref": "eastern_med_gas.disruption_confirmed",
    },
}


CHOKEPOINT_CRISIS_EVENTS: List[Dict[str, Any]] = [
    {
        "id": "hormuz_blockade_2026",
        "name": "Strait of Hormuz Blockade (Iran-US War 2026)",
        "category": "war",
        "date_start": "2026-02-15",
        "date_peak_impact": "2026-03-01",
        "date_recovery": None,
        "duration_days": None,
        "severity": 0.95,
        "sp500_drawdown_pct": -12.0,
        "vix_peak": 45.0,
        "oil_move_pct": 40.0,
        "gold_move_pct": 20.0,
        "usd_move": "strengthen",
        "winners": ["USO", "XLE", "XOP", "GLD", "LMT", "RTX"],
        "losers": ["JETS", "EZU", "EEM"],
        "intraday_pattern": "gap_up_energy_open_follow_through_then_profit_taking",
        "optimal_day_trade": (
            "Long USO/XLE/XOP at the open, pair with GLD/LMT, short JETS/EZU, "
            "and trade volatility spikes intraday."
        ),
        "regime_signature": {
            "hormuz_risk": True,
            "oil_above_100": True,
            "vix_above": 30,
            "defense_momentum": True,
            "energy_supply_shock": True,
            "iran_naval_activity": True,
            "tanker_rerouting": True,
        },
        "recovery_shape": "event_driven_with_energy_grind",
        "lesson": (
            "Hormuz closure means immediate oil repricing. Energy longs lead for "
            "one to four weeks while airlines are the immediate short."
        ),
    },
    {
        "id": "dual_chokepoint_2026",
        "name": "Dual Chokepoint: Hormuz + Red Sea (2026)",
        "category": "war",
        "date_start": "2026-03-01",
        "date_peak_impact": "2026-03-15",
        "date_recovery": None,
        "duration_days": None,
        "severity": 1.0,
        "sp500_drawdown_pct": -15.0,
        "vix_peak": 55.0,
        "oil_move_pct": 50.0,
        "gold_move_pct": 25.0,
        "usd_move": "strengthen",
        "winners": ["USO", "XLE", "XOP", "ZIM", "GLD", "UVXY"],
        "losers": ["JETS", "EZU", "EEM", "SPY"],
        "intraday_pattern": "global_gap_dislocation_then_commodity_momentum",
        "optimal_day_trade": (
            "Max long energy and shipping, long gold, short Europe, EM, and airlines; "
            "keep exposure capped."
        ),
        "regime_signature": {
            "hormuz_risk": True,
            "bab_el_mandeb_risk": True,
            "dual_chokepoint": True,
            "global_shipping_crisis": True,
            "oil_above_120": True,
            "vix_above": 40,
        },
        "recovery_shape": "policy_driven_relief_then_grind",
        "lesson": (
            "Dual chokepoint stress is a 2008-scale energy shock. Position sizing "
            "matters more than direction."
        ),
    },
    {
        "id": "triple_chokepoint_2026",
        "name": "Triple Chokepoint Crisis (2026 - Maximum Scenario)",
        "category": "war",
        "date_start": "2026-03-15",
        "date_peak_impact": "2026-03-20",
        "date_recovery": None,
        "duration_days": None,
        "severity": 1.0,
        "sp500_drawdown_pct": -20.0,
        "vix_peak": 65.0,
        "oil_move_pct": 70.0,
        "gold_move_pct": 30.0,
        "usd_move": "strengthen_then_volatile",
        "winners": ["USO", "XLE", "UNG", "GLD", "GDX", "UVXY", "LMT", "RTX"],
        "losers": ["JETS", "EZU", "EEM", "EIS", "EUFN"],
        "intraday_pattern": "capital_preservation_mode_extreme_volatility",
        "optimal_day_trade": (
            "1% positions only. Long energy, gold, vol, and defense while shorting "
            "risk assets. Capital preservation first."
        ),
        "regime_signature": {
            "hormuz_risk": True,
            "bab_el_mandeb_risk": True,
            "med_gas_risk": True,
            "triple_chokepoint": True,
            "global_energy_crisis": True,
            "oil_above_150": True,
            "vix_above": 50,
            "global_shipping_halt": True,
            "inflation_spike": True,
        },
        "recovery_shape": "regime_change_multi_month",
        "lesson": (
            "Triple chokepoint stress is once-in-generation. The goal is survival, "
            "not maximizing profit."
        ),
    },
]


def build_chokepoint_analog_library(
    events: Iterable[Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Map chokepoint events into the HistoricalAnalogEngine library shape."""
    return [event_to_analog_entry(dict(event)) for event in (events or CHOKEPOINT_CRISIS_EVENTS)]


def merge_chokepoint_analog_library(
    existing_library: Sequence[Mapping[str, Any]] | None,
    events: Iterable[Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Append chokepoint analogs without duplicating prior entries."""
    merged = [dict(entry) for entry in (existing_library or [])]
    existing_ids = {
        entry.get("source_event_id") or entry.get("label")
        for entry in merged
        if isinstance(entry, Mapping)
    }
    for entry in build_chokepoint_analog_library(events):
        dedupe_key = entry.get("source_event_id") or entry.get("label")
        if dedupe_key not in existing_ids:
            merged.append(entry)
            existing_ids.add(dedupe_key)
    return merged


def _bridge_mapping(
    bridge_results: Mapping[str, Any],
    *aliases: str,
) -> Mapping[str, Any]:
    for alias in aliases:
        payload = bridge_results.get(alias)
        if isinstance(payload, Mapping):
            return payload
    return {}


def _bridge_sequence(
    bridge_results: Mapping[str, Any],
    *aliases: str,
) -> List[Mapping[str, Any]]:
    for alias in aliases:
        payload = bridge_results.get(alias)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            events = payload.get("events")
            if isinstance(events, list):
                return [item for item in events if isinstance(item, Mapping)]
    return []


def _contains_keywords(value: Any, keywords: Iterable[str]) -> bool:
    haystack = str(value).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def compute_chokepoint_risk_score(bridge_results: Mapping[str, Any]) -> Dict[str, Any]:
    """Score single, dual, and triple chokepoint stress from live bridge data."""
    scores: Dict[str, Any] = {
        "hormuz": 0.0,
        "bab_el_mandeb": 0.0,
        "eastern_med": 0.0,
        "composite": 0.0,
        "active_chokepoints": 0,
        "scenario_match": None,
        "recommended_playbook": None,
        "recommended_playbook_metadata": None,
        "monitoring_mode": "live_bridge_context",
        "refresh_expectation": "every_monitor_cycle",
        "source_aliases": [
            "maritime_bridge",
            "maritime",
            "gdelt_bridge",
            "gdelt",
            "eia_bridge",
            "eia",
            "exa_ai_bridge",
            "exa",
            "options_greeks_bridge",
            "options_greeks",
            "sentiment_bridge",
            "sentiment",
        ],
    }
    scores.update(EXECUTION_BOUNDARY)

    eia = _bridge_mapping(bridge_results, "eia_bridge", "eia")
    maritime = _bridge_mapping(bridge_results, "maritime_bridge", "maritime")
    options = _bridge_mapping(
        bridge_results,
        "options_greeks_bridge",
        "options_greeks",
    )
    exa = bridge_results.get("exa_ai_bridge") or bridge_results.get("exa") or {}
    sentiment = _bridge_mapping(bridge_results, "sentiment_bridge", "sentiment")
    gdelt_events = _bridge_sequence(bridge_results, "gdelt_bridge", "gdelt")
    gdelt_map = _bridge_mapping(bridge_results, "gdelt_bridge", "gdelt")

    hormuz_signals = 0.0
    inventory_change = eia.get("crude_inventory_change", eia.get("inventory_change", 0))
    if isinstance(inventory_change, (int, float)) and inventory_change < -5.0:
        hormuz_signals += 0.2

    maritime_disruption = maritime.get("disruption_score", maritime.get("hormuz_traffic", 0))
    if isinstance(maritime_disruption, (int, float)):
        hormuz_signals += min(abs(float(maritime_disruption)) / 10.0, 0.3)

    gdelt_severity = gdelt_map.get("max_severity", gdelt_map.get("severity", 0))
    if isinstance(gdelt_severity, (int, float)):
        hormuz_signals += min(float(gdelt_severity) / 10.0, 0.3)
    elif gdelt_events:
        iran_events = [
            event
            for event in gdelt_events
            if _contains_keywords(event, ["iran", "hormuz", "persian gulf"])
        ]
        hormuz_signals += min(len(iran_events) * 0.05, 0.3)

    put_call_ratio = options.get("put_call_ratio", 1.0)
    if isinstance(put_call_ratio, (int, float)) and put_call_ratio < 0.7:
        hormuz_signals += 0.1
    gamma_risk = options.get("gamma_squeeze_risk", 0)
    if isinstance(gamma_risk, (int, float)) and gamma_risk > 0.5:
        hormuz_signals += 0.1
    scores["hormuz"] = min(hormuz_signals, 1.0)

    bab_signals = 0.2  # baseline risk: Red Sea disruption is already an active background regime
    suez_traffic = maritime.get("suez_traffic", maritime.get("suez_traffic_decline", 0))
    if isinstance(suez_traffic, (int, float)):
        bab_signals += min(abs(float(suez_traffic)) / 50.0, 0.3)
    rerouting = maritime.get("rerouting_count", 0)
    if isinstance(rerouting, (int, float)):
        bab_signals += min(float(rerouting) * 0.05, 0.2)

    if gdelt_events:
        houthi_events = [
            event
            for event in gdelt_events
            if _contains_keywords(event, ["houthi", "red sea", "bab", "mandeb", "yemen"])
        ]
        bab_signals += min(len(houthi_events) * 0.05, 0.3)
    elif gdelt_map.get("supply_chain_relevance", 0) > 0.5:
        bab_signals += 0.2

    sentiment_score = sentiment.get("aggregate_score", sentiment.get("market_fear", 0))
    if isinstance(sentiment_score, (int, float)) and sentiment_score < -0.3:
        bab_signals += 0.05
    scores["bab_el_mandeb"] = min(bab_signals, 1.0)

    med_signals = 0.0
    if gdelt_events:
        med_events = [
            event
            for event in gdelt_events
            if _contains_keywords(
                event,
                ["hezbollah", "lebanon", "leviathan", "tamar", "mediterranean gas"],
            )
        ]
        med_signals += min(len(med_events) * 0.05, 0.3)

    if isinstance(exa, Mapping):
        article_count = exa.get("article_count", exa.get("total_articles", 0))
        if isinstance(article_count, (int, float)):
            med_signals += min(float(article_count) * 0.01, 0.2)
    elif isinstance(exa, list):
        gas_articles = [
            article
            for article in exa
            if _contains_keywords(article, ["gas field", "leviathan", "mediterranean", "pipeline"])
        ]
        med_signals += min(len(gas_articles) * 0.05, 0.2)

    med_nav_activity = maritime.get("med_naval_activity", 0)
    if isinstance(med_nav_activity, (int, float)):
        med_signals += min(float(med_nav_activity) / 10.0, 0.2)
    scores["eastern_med"] = min(med_signals, 1.0)

    active = sum(
        1
        for key in ("hormuz", "bab_el_mandeb", "eastern_med")
        if scores[key] > 0.3
    )
    scores["active_chokepoints"] = active

    base = (
        scores["hormuz"] * 0.45
        + scores["bab_el_mandeb"] * 0.35
        + scores["eastern_med"] * 0.20
    )
    multiplier = {0: 1.0, 1: 1.0, 2: 1.4, 3: 1.8}
    scores["composite"] = min(base * multiplier.get(active, 1.8), 1.0)

    if active >= 3 and scores["composite"] > 0.7:
        scores["scenario_match"] = "triple_chokepoint"
        scores["recommended_playbook"] = COMBINED_SCENARIOS["triple_chokepoint"]["combined_playbook"]
    elif scores["hormuz"] > 0.5 and scores["bab_el_mandeb"] > 0.5:
        scores["scenario_match"] = "hormuz_plus_bab_el_mandeb"
        scores["recommended_playbook"] = COMBINED_SCENARIOS[
            "hormuz_plus_bab_el_mandeb"
        ]["combined_playbook"]
    elif scores["hormuz"] > 0.5:
        scores["scenario_match"] = "hormuz_only"
        scores["recommended_playbook"] = CHOKEPOINTS["hormuz"]["trading_playbook"][
            "disruption_confirmed"
        ]
    elif scores["bab_el_mandeb"] > 0.5:
        scores["scenario_match"] = "bab_el_mandeb_only"
        scores["recommended_playbook"] = CHOKEPOINTS["bab_el_mandeb"][
            "trading_playbook"
        ]["disruption_confirmed"]
    elif scores["eastern_med"] > 0.5:
        scores["scenario_match"] = "med_gas_only"
        scores["recommended_playbook"] = CHOKEPOINTS["eastern_med_gas"][
            "trading_playbook"
        ]["disruption_confirmed"]

    if scores["scenario_match"]:
        scores["recommended_playbook_metadata"] = {
            **EXECUTION_BOUNDARY,
            "scenario": scores["scenario_match"],
            "source": "chokepoint_scenarios",
            "source_type": "live_real_time_bridge_context",
        }

    return scores


def get_chokepoint_telegram_summary(scores: Mapping[str, Any]) -> str:
    """Format chokepoint scores for a digest message."""
    lines = [
        (
            "🌊 Chokepoints: "
            f"H={float(scores.get('hormuz', 0.0)):.2f} "
            f"B={float(scores.get('bab_el_mandeb', 0.0)):.2f} "
            f"M={float(scores.get('eastern_med', 0.0)):.2f} | "
            f"Composite={float(scores.get('composite', 0.0)):.2f}"
        )
    ]
    active = int(scores.get("active_chokepoints", 0) or 0)
    if active >= 2:
        lines.append(
            f"⚠️ {active} chokepoints active - {scores.get('scenario_match', 'monitoring')}"
        )
    scenario_key = scores.get("scenario_match")
    if isinstance(scenario_key, str):
        scenario = COMBINED_SCENARIOS.get(scenario_key, {})
        lines.append(
            "📋 Scenario: "
            f"{scenario.get('name', scenario_key)} "
            f"(est. severity {scenario.get('severity', '?')})"
        )
    return "\n".join(lines)
