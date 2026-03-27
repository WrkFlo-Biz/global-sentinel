#!/usr/bin/env python3
"""
TAPO Pattern Detector — Trump Always Pussies Out
Source: @barebone.ai analysis

The pattern:
1. Trump escalates (tariffs, war, CEO threats)
2. Markets break (oil spikes, yields spike, stocks drop)
3. His voters feel pain (mortgages, gas, car loans)
4. He reverses (pauses strikes, calls talks productive, delays tariffs)
5. Markets snap back violently (SPY rips, oil crashes, yields drop)

Trading strategy: When Trump escalation hits peak pain (10yr yield >4.5%, oil >$110),
position for the inevitable reversal. Buy the dip on the snapback.

Key indicators:
- 10yr yield approaching 4.5% = reversal imminent
- Oil above $110/barrel = political pain threshold
- Consumer confidence at recessionary lows = voter pressure
- Trump tone shift (from aggressive to "productive talks") = trigger
"""
import json, os, datetime, urllib.request
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/tapo_pattern.json"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def detect_tapo_phase():
    """Detect which phase of the TAPO cycle we are in."""
    # Load latest signals
    latest = {}
    try:
        latest = json.loads((REPO_ROOT / "data/quantum_feed/latest_signal.json").read_text())
    except: pass

    polymarket = {}
    try:
        polymarket = json.loads((REPO_ROOT / "data/quantum_feed/polymarket_geopolitical.json").read_text())
    except: pass

    # Key thresholds (from historical TAPO pattern)
    oil_price = latest.get("wti_price", 92)
    war_intensity = max(latest.get("bucket_scores", {}).get("GEOPOLITICAL", 5), 5)
    peace_prob = polymarket.get("peace_aggregate", {}).get("avg_probability", 0.30)

    # Determine TAPO phase
    if oil_price > 110 and war_intensity >= 8:
        phase = "PEAK_ESCALATION"
        description = "Maximum pain — oil above $110, war intensity high. Reversal imminent per TAPO pattern."
        trade_signal = "PREPARE_FOR_REVERSAL"
        direction = "bullish_setup"
        confidence = 0.85
        actions = [
            "Start building long SPY/QQQ positions",
            "Buy airline calls (JETS) for the snapback",
            "Short oil (USO puts) for the crash",
            "Buy VIX puts (SVXY) for vol compression",
        ]
    elif oil_price > 95 and war_intensity >= 7:
        phase = "ESCALATION"
        description = "Pain building — oil elevated, conflict intensifying. Not yet at reversal threshold."
        trade_signal = "WAIT_AND_WATCH"
        direction = "neutral_bearish"
        confidence = 0.6
        actions = [
            "Hold defensive positions (energy longs, airline shorts)",
            "Watch for Trump tone shift as trigger",
            "Monitor 10yr yield approaching 4.5%",
        ]
    elif peace_prob > 0.5:
        phase = "REVERSAL_IN_PROGRESS"
        description = "Trump is reversing — peace talks, strike pauses. The TAPO snapback is happening."
        trade_signal = "RIDE_THE_SNAPBACK"
        direction = "bullish"
        confidence = 0.9
        actions = [
            "Long SPY/QQQ calls aggressively",
            "Long airlines (JETS/DAL/UAL calls)",
            "Short oil (USO puts)",
            "Short VIX (SVXY calls)",
        ]
    elif oil_price < 80 and war_intensity < 4:
        phase = "POST_REVERSAL_CALM"
        description = "Calm after the storm. Market has snapped back. Wait for next escalation cycle."
        trade_signal = "TAKE_PROFITS"
        direction = "neutral"
        confidence = 0.7
        actions = [
            "Take profits on snapback trades",
            "Prepare for next escalation (it always comes)",
            "Build cash position",
        ]
    else:
        phase = "MID_CYCLE"
        description = f"Between escalation and reversal. Oil at ${oil_price}, war intensity {war_intensity}/10."
        trade_signal = "MAINTAIN_HEDGES"
        direction = "neutral"
        confidence = 0.5
        actions = [
            "Maintain current hedged positions",
            "Monitor for phase transition signals",
        ]

    # Historical TAPO instances for pattern confirmation
    historical_tapos = [
        {"date": "2025-05-12", "event": "China tariff truce", "spx_1d_return": "+2.8%"},
        {"date": "2025-08-15", "event": "Canada tariff delay", "spx_1d_return": "+1.5%"},
        {"date": "2026-03-23", "event": "Iran strike pause", "spx_1d_return": "+2.0%"},
    ]

    result = {
        "timestamp": iso_now(),
        "tapo_phase": phase,
        "description": description,
        "trade_signal": trade_signal,
        "direction": direction,
        "confidence": confidence,
        "recommended_actions": actions,
        "indicators": {
            "oil_price": oil_price,
            "war_intensity": war_intensity,
            "peace_probability": peace_prob,
            "reversal_threshold_oil": 110,
            "reversal_threshold_yield": 4.5,
        },
        "historical_pattern": historical_tapos,
        "pattern_description": "Trump escalates until voter pain forces reversal. Buy the reversal, sell the calm.",
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    return result

if __name__ == "__main__":
    result = detect_tapo_phase()
    print(json.dumps(result, indent=2))
