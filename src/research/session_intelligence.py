#!/usr/bin/env python3
"""
Session Intelligence Module - Intraday session-aware trading signals.
Asia builds the range. London manipulates. New York expands.
"""
import json, os, datetime, urllib.request
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/session_intelligence.json"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def get_et_time():
    utc = datetime.datetime.now(datetime.timezone.utc)
    return utc - datetime.timedelta(hours=4)

def get_current_session():
    et = get_et_time()
    t = et.hour * 60 + et.minute
    if t >= 19 * 60 or t < 3 * 60:
        return "asia"
    elif t < 9 * 60 + 30:
        return "london"
    elif t < 11 * 60:
        return "ny_open"
    elif t < 14 * 60:
        return "ny_midday"
    elif t < 15 * 60 + 45:
        return "ny_power_hour"
    elif t < 16 * 60 + 15:
        return "ny_close"
    return "after_hours"

STRATEGIES = {
    "asia": {
        "behavior": "consolidation", "action": "OBSERVE - mark Asia high/low",
        "best_assets": ["USDJPY","AUDJPY","AUDUSD","BTC","ETH"],
        "entry_quality": 0.3, "size_multiplier": 0.5,
        "signals": {"mark_asia_range": True, "expect_breakout": False, "mean_reversion_preferred": True}
    },
    "london": {
        "behavior": "manipulation", "action": "WAIT - first Asia range break is often fake (stop hunt)",
        "best_assets": ["EURUSD","GBPUSD","EURGBP","GLD","XLE"],
        "entry_quality": 0.6, "size_multiplier": 0.7,
        "signals": {"asia_range_break_is_fake": True, "wait_for_reversal_after_stop_hunt": True}
    },
    "ny_open": {
        "behavior": "expansion", "action": "TRADE - follow London direction. Best window of the day.",
        "best_assets": ["SPY","QQQ","TSLA","NVDA","AMD","EURUSD","XLE"],
        "entry_quality": 1.0, "size_multiplier": 1.0,
        "signals": {"follow_london_direction": True, "breakout_trades_preferred": True, "momentum_confirmation_required": True}
    },
    "ny_midday": {
        "behavior": "consolidation", "action": "REDUCE SIZE - lunch lull, theta kills 0DTE",
        "best_assets": [],
        "entry_quality": 0.3, "size_multiplier": 0.5,
        "signals": {"avoid_new_entries": True, "theta_decay_accelerating": True, "wait_for_power_hour": True}
    },
    "ny_power_hour": {
        "behavior": "expansion", "action": "TRADE - second best window. Institutions execute here.",
        "best_assets": ["SPY","QQQ","TSLA","NVDA","AMD","IWM"],
        "entry_quality": 0.9, "size_multiplier": 0.8,
        "signals": {"trend_acceleration_likely": True, "institutional_flow_visible": True}
    },
    "ny_close": {
        "behavior": "closing", "action": "EXIT all day trades. No new entries.",
        "best_assets": [],
        "entry_quality": 0.1, "size_multiplier": 0.0,
        "signals": {"flatten_day_trades": True, "no_new_entries": True}
    },
    "after_hours": {
        "behavior": "thin", "action": "OBSERVE - catalyst only trading",
        "best_assets": ["BTC","ETH"],
        "entry_quality": 0.2, "size_multiplier": 0.3,
        "signals": {"catalyst_only": True, "wide_spreads_expected": True}
    },
}

def generate_session_report():
    session = get_current_session()
    strategy = STRATEGIES.get(session, STRATEGIES["after_hours"])
    et = get_et_time()
    report = {
        "timestamp": iso_now(),
        "et_time": et.strftime("%Y-%m-%d %H:%M ET"),
        "session": session,
        "strategy": strategy,
        "should_trade": strategy["entry_quality"] >= 0.6,
        "best_assets": strategy["best_assets"],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2))
    report["intra_session"] = get_intra_session_phase()
    return report



# === INTRA-SESSION PHASE MODEL (@alves.trader_) ===
INTRA_SESSION_PHASES = {
    "asia": [
        {"name": "continuation", "hours": (19, 21), "action": "Observe - continues NY direction"},
        {"name": "consolidation", "hours": (21, 1), "action": "Mark range, do NOT trade"},
        {"name": "reversal", "hours": (1, 3), "action": "Watch for pre-London reversal"},
    ],
    "london": [
        {"name": "continuation", "hours": (3, 5), "action": "Do NOT chase first Asia range break"},
        {"name": "consolidation", "hours": (5, 7), "action": "Identify true direction after stop hunt"},
        {"name": "reversal", "hours": (7, 9), "action": "Prepare for NY handoff"},
    ],
    "ny_open": [
        {"name": "expansion", "hours": (9, 11), "action": "TRADE - follow London direction"},
    ],
    "ny_midday": [
        {"name": "consolidation", "hours": (11, 14), "action": "REDUCE SIZE - lunch lull"},
    ],
    "ny_power_hour": [
        {"name": "expansion", "hours": (14, 16), "action": "TRADE - power hour final push"},
    ],
}

SESSION_BEST_PAIRS = {
    "asia": {"forex": ["AUDJPY","AUDUSD","NZDUSD","USDJPY"], "crypto": ["BTC-USD","ETH-USD"]},
    "london": {"forex": ["EURUSD","GBPUSD","EURGBP","EURJPY"], "commodities": ["GLD","XLE"]},
    "ny_open": {"indices": ["SPY","QQQ","IWM"], "stocks": ["NVDA","TSLA","AMD","META"], "forex": ["EURUSD","GBPUSD"]},
    "ny_midday": {},
    "ny_power_hour": {"indices": ["SPY","QQQ"], "stocks": ["NVDA","TSLA","AMD"]},
}

def get_intra_session_phase():
    et = get_et_time()
    h = et.hour
    session = get_current_session()
    phases = INTRA_SESSION_PHASES.get(session, [])
    current_phase = "unknown"
    current_action = ""
    for p in phases:
        s, e = p["hours"]
        if s <= e:
            if s <= h < e:
                current_phase = p["name"]
                current_action = p["action"]
        else:
            if h >= s or h < e:
                current_phase = p["name"]
                current_action = p["action"]
    return {"session": session, "intra_phase": current_phase, "action": current_action, "best_pairs": SESSION_BEST_PAIRS.get(session, {})}


if __name__ == "__main__":
    print(json.dumps(generate_session_report(), indent=2))
