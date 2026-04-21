from __future__ import annotations

from src.research.training.chokepoint_scenarios import (
    CHOKEPOINT_CRISIS_EVENTS,
    build_chokepoint_analog_library,
    compute_chokepoint_risk_score,
    get_chokepoint_telegram_summary,
    merge_chokepoint_analog_library,
)


def test_chokepoint_analog_library_entries_are_normalized():
    analogs = build_chokepoint_analog_library()

    assert len(analogs) == len(CHOKEPOINT_CRISIS_EVENTS)
    assert {entry["source_event_id"] for entry in analogs} == {
        event["id"] for event in CHOKEPOINT_CRISIS_EVENTS
    }
    assert all("regime_markers" in entry for entry in analogs)


def test_merge_chokepoint_analog_library_is_idempotent():
    merged_once = merge_chokepoint_analog_library([])
    merged_twice = merge_chokepoint_analog_library(merged_once)

    assert len(merged_once) == len(CHOKEPOINT_CRISIS_EVENTS)
    assert len(merged_twice) == len(merged_once)


def test_compute_chokepoint_risk_score_detects_dual_chokepoint():
    bridge_results = {
        "eia_bridge": {"crude_inventory_change": -8.0},
        "maritime_bridge": {
            "disruption_score": 8.0,
            "suez_traffic_decline": -18.0,
            "rerouting_count": 5,
        },
        "gdelt_bridge": [
            {"title": "Iran navy activity near Hormuz spikes"},
            {"title": "Houthi attack in Red Sea shipping lane"},
            {"title": "Red Sea insurers raise war premiums"},
        ],
        "options_greeks_bridge": {
            "put_call_ratio": 0.6,
            "gamma_squeeze_risk": 0.8,
        },
    }

    scores = compute_chokepoint_risk_score(bridge_results)

    assert scores["hormuz"] > 0.5
    assert scores["bab_el_mandeb"] > 0.5
    assert scores["scenario_match"] == "hormuz_plus_bab_el_mandeb"
    assert scores["recommended_playbook"] is not None
    assert scores["informational_only"] is True
    assert scores["not_for_direct_execution"] is True
    assert scores["execution_influence_forbidden"] is True
    assert scores["monitoring_mode"] == "live_bridge_context"
    assert scores["recommended_playbook_metadata"]["scenario"] == "hormuz_plus_bab_el_mandeb"


def test_telegram_summary_mentions_scenario_when_active():
    scores = {
        "hormuz": 0.75,
        "bab_el_mandeb": 0.65,
        "eastern_med": 0.15,
        "composite": 0.88,
        "active_chokepoints": 2,
        "scenario_match": "hormuz_plus_bab_el_mandeb",
    }

    summary = get_chokepoint_telegram_summary(scores)

    assert "Chokepoints:" in summary
    assert "2 chokepoints active" in summary
    assert "Dual Chokepoint" in summary
