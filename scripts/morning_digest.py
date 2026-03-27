#!/usr/bin/env python3
"""
Global Sentinel Morning Digest → Telegram
Sends top trade ideas to Moses for live account decisions.
Run at 9:15 AM ET on market days: python3 /opt/global-sentinel/scripts/morning_digest.py
"""

import json
import glob
import os
import sys
from datetime import datetime, timezone

import requests

TELEGRAM_TOKEN = "8415413828:AAH557N2gqzJlvwtO3S8FBxGArSdFdNl9qI"
CHAT_ID = "7091381625"
SCORECARDS_DIR = "/opt/global-sentinel/logs/scorecards"
SHADOW_ROUTER_LOG = "/opt/global-sentinel/logs/execution/shadow_order_router.jsonl"
LIVE_CASH = 177.59  # Update as needed


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_latest_scorecard():
    files = sorted(glob.glob(os.path.join(SCORECARDS_DIR, "scorecard_*.json")))
    if not files:
        return {}
    with open(files[-1], "r") as f:
        return json.load(f)


def get_latest_candidates():
    """Extract candidates from the last shadow router entry."""
    candidates = []
    if not os.path.exists(SHADOW_ROUTER_LOG):
        return candidates
    # Read last 50 lines
    with open(SHADOW_ROUTER_LOG, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        pos = max(0, size - 200000)
        f.seek(pos)
        lines = f.readlines()

    for line in reversed(lines):
        try:
            d = json.loads(line.strip())
            payload = d.get("payload", {})
            for c in payload.get("skipped_candidates", []):
                sym = c.get("symbol", "")
                reason = c.get("reason", "")
                cid = c.get("candidate_id", "")
                if sym:
                    candidates.append({
                        "symbol": sym,
                        "reason": reason,
                        "candidate_id": cid,
                        "strategy": payload.get("strategy_name", ""),
                        "selected": False,
                    })
            for c in payload.get("selected_candidates", []):
                sym = c.get("symbol", "")
                if sym:
                    candidates.append({
                        "symbol": sym,
                        "confidence": c.get("confidence_score", 0),
                        "direction": c.get("direction", ""),
                        "strategy_style": c.get("strategy_style", ""),
                        "strategy": payload.get("strategy_name", ""),
                        "selected": True,
                    })
            if candidates:
                break
        except Exception:
            continue
    return candidates


def format_digest():
    sc = get_latest_scorecard()
    candidates = get_latest_candidates()

    mode = sc.get("mode", "?")
    regime_prob = sc.get("regime_shift_probability", 0)
    confidence = sc.get("confidence", 0)
    bridge = sc.get("bridge_summary", {})
    pc_ratio = bridge.get("put_call_ratio", "?")
    gamma = bridge.get("gamma_squeeze_risk", "?")
    edge = sc.get("v6_edge_summary", "none")

    # Component scores
    components = sc.get("component_scores", {})
    commodity = components.get("commodity_shock", 0)
    vol = components.get("market_volatility", 0)
    geo = components.get("geopolitical_tension", 0)

    # Top candidates
    selected = [c for c in candidates if c.get("selected")]
    all_syms = list({c["symbol"] for c in candidates})[:10]

    msg = f"""<b>🔮 Global Sentinel Morning Digest</b>
<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>

<b>Market Regime:</b>
• Mode: <b>{mode}</b> | Shift Prob: {regime_prob:.1%}
• Confidence: {confidence:.1%}
• P/C Ratio: {pc_ratio} | Gamma: {gamma}
• Commodity Shock: {commodity:.2f} | Vol: {vol:.2f} | Geo: {geo:.2f}

<b>Edge:</b> {edge}

<b>Top Candidates Being Tracked:</b>
"""

    if selected:
        for c in selected[:5]:
            msg += f"✅ <b>{c['symbol']}</b> — {c.get('direction','')} | conf={c.get('confidence',0):.3f} | {c.get('strategy','')}\n"
    else:
        msg += "<i>No selected candidates (all below confidence or exposure limits)</i>\n"

    if all_syms:
        msg += f"\n<b>Watchlist:</b> {', '.join(all_syms[:10])}\n"

    msg += f"""
<b>💰 Live Account Cash: ${LIVE_CASH:.2f}</b>
Strikes: $1-2 OTM max | 100%+ = strong sell

⚠️ <i>Review before trading. No auto-execution on live account.</i>"""

    return msg


if __name__ == "__main__":
    try:
        msg = format_digest()
        result = send_telegram(msg)
        print(f"Digest sent: {result.get('ok')}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
