#!/usr/bin/env python3
"""Enhance session_intelligence.py with intra-session phases + forex pair routing"""
from pathlib import Path

path = Path("/opt/global-sentinel/src/research/session_intelligence.py")
content = path.read_text()

addition = '''

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

'''

if "INTRA_SESSION_PHASES" not in content:
    content = content.replace(
        'if __name__ == "__main__":',
        addition + '\nif __name__ == "__main__":'
    )
    content = content.replace(
        '    return report',
        '    report["intra_session"] = get_intra_session_phase()\n    return report',
        1
    )
    path.write_text(content)
    print("Enhanced!")
else:
    print("Already done")
