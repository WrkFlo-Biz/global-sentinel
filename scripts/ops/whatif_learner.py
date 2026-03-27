#!/usr/bin/env python3
"""What-If Learner — collects performance data, trains on patterns, recommends trades.

Runs continuously:
1. Every 5 min: snapshot what-if picks performance → append to daily log
2. Every 30 min: analyze patterns → compute pick quality scores
3. When confidence is high enough: recommend a trade via Telegram for approval
"""
import json
import urllib.request
import ssl
import time
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ctx = ssl.create_default_context()

# Add repo root to path for quantum bridge
REPO_ROOT = Path("/opt/global-sentinel")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def run_quantum_bridge():
    """Run the what-if quantum bridge and return quantum weights."""
    try:
        from src.research.whatif_quantum_bridge import run as qb_run
        result = qb_run(verbose=False)
        if result.get("status") == "success":
            return result
        return None
    except Exception as e:
        print(f"  [QBridge] Error: {e}")
        return None

def load_quantum_weights():
    """Load latest quantum weights from data dir."""
    qfile = REPO_ROOT / "data" / "whatif_learning" / "quantum_scores.json"
    if not qfile.exists():
        return {}
    try:
        d = __import__("json").loads(qfile.read_text())
        return d.get("quantum_weights", {})
    except Exception:
        return {}

# Load env
env = {}
with open("/opt/global-sentinel/.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"")

TG_TOKEN = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = env.get("TELEGRAM_CHAT_ID", "")
ALP_KEY = env.get("ALPACA_API_KEY_LIVE", "")
ALP_SECRET = env.get("ALPACA_SECRET_KEY_LIVE", "")
ALP_HEADERS = {"APCA-API-KEY-ID": ALP_KEY, "APCA-API-SECRET-KEY": ALP_SECRET, "Content-Type": "application/json"}
ALP_BASE = "https://api.alpaca.markets"

DATA_DIR = Path("/opt/global-sentinel/data/whatif_learning")
DATA_DIR.mkdir(parents=True, exist_ok=True)
APPROVAL_FILE = Path("/tmp/gs_learner_approved")
today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
OPENING_PRICES_FILE = DATA_DIR / f"opening_prices_{today_str}.json"

# Track daily opening prices
_opening_prices_captured = False

def capture_opening_prices(data):
    """Capture opening prices at market open. Run once per day at 9:31 ET."""
    global _opening_prices_captured
    if _opening_prices_captured:
        return
    if not data or not data.get("picks"):
        return

    opening = {
        "date": today_str,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "prices": {}
    }
    for p in data["picks"]:
        sym = p.get("symbol", "")
        if sym:
            opening["prices"][sym] = {
                "open_price": p.get("open_price", 0),
                "current_price": p.get("current_price", 0),
                "prev_close": p.get("prev_close", 0),
                "direction": p.get("direction", ""),
                "bucket": p.get("bucket", ""),
                "confidence": p.get("confidence", 0),
            }

    OPENING_PRICES_FILE.write_text(json.dumps(opening, indent=2))
    _opening_prices_captured = True
    print(f"  [OPEN] Captured opening prices for {len(opening['prices'])} symbols -> {OPENING_PRICES_FILE.name}")

    # Also append to historical opening prices log
    hist_file = DATA_DIR / "opening_prices_history.jsonl"
    with open(hist_file, "a") as f:
        f.write(json.dumps({"date": today_str, "prices": opening["prices"]}) + "\n")

today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
DAILY_LOG = DATA_DIR / f"snapshots_{today_str}.jsonl"
SCORES_FILE = DATA_DIR / "pick_quality_scores.json"
HISTORY_FILE = DATA_DIR / "historical_performance.json"


def _notifications_muted() -> bool:
    raw = env.get("TELEGRAM_UPDATES_MUTED_UNTIL", "")
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < deadline
    except Exception:
        return False

def send_telegram(msg):
    if _notifications_muted():
        return
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        _pd = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}
        if str(TG_CHAT).startswith("-100"):
            _dt = env.get("TELEGRAM_DEFAULT_THREAD_ID")
            if _dt:
                _pd["message_thread_id"] = int(_dt)
        payload = json.dumps(_pd).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f"  [TG] Error: {e}")


def fetch_whatif():
    """Fetch current what-if picks from dashboard API."""
    try:
        url = "http://localhost:8501/api/whatif-picks"
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  [FETCH] Error: {e}")
        return None


def fetch_account():
    """Get current account state."""
    try:
        req = urllib.request.Request(f"{ALP_BASE}/v2/account", headers=ALP_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return json.loads(r.read().decode())
    except:
        return {}


def fetch_positions():
    """Get current positions."""
    try:
        req = urllib.request.Request(f"{ALP_BASE}/v2/positions", headers=ALP_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return json.loads(r.read().decode())
    except:
        return []


def post_order(payload):
    """Submit an order to Alpaca."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{ALP_BASE}/v2/orders", data=data, headers=ALP_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.loads(r.read().decode())


def snapshot_picks(data):
    """Log a timestamped snapshot of all picks."""
    if not data or not data.get("picks"):
        return

    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "picks": []
    }
    for p in data["picks"]:
        snapshot["picks"].append({
            "sym": p["symbol"],
            "bucket": p.get("bucket", ""),
            "direction": p.get("direction", ""),
            "price": p.get("current_price", 0),
            "open": p.get("open_price", 0),
            "prev_close": p.get("prev_close", 0),
            "change_pct": p.get("change_pct", 0),
            "change_from_open": round(((p.get("current_price", 0) / p.get("open_price", 1)) - 1) * 100, 3) if p.get("open_price", 0) > 0 else 0,
            "hyp_pnl": p.get("hypothetical_pnl", 0),
            "hyp_pnl_pct": p.get("hypothetical_pnl_pct", 0),
            "confidence": p.get("confidence", 0),
        })

    with open(DAILY_LOG, "a") as f:
        f.write(json.dumps(snapshot) + "\n")

    return snapshot


def analyze_patterns():
    """Analyze accumulated snapshots to find consistent winners/losers.

    Returns dict of {symbol: quality_score} where:
    - score > 0 = consistently outperforming (good pick)
    - score < 0 = consistently underperforming (bad pick)

    Factors:
    1. Trend direction: is the pick getting better or worse over time?
    2. Consistency: how stable is the P&L trajectory?
    3. Magnitude: how large are the moves?
    4. Confidence alignment: does high engine confidence = high actual performance?
    """
    if not DAILY_LOG.exists():
        return {}

    snapshots = []
    with open(DAILY_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    snapshots.append(json.loads(line))
                except:
                    continue

    if len(snapshots) < 3:
        return {}

    # Track each symbol across time
    sym_series = {}  # {symbol: [(ts, pnl_pct, change_pct), ...]}
    for snap in snapshots:
        ts = snap["ts"]
        for p in snap["picks"]:
            sym = p["sym"]
            if sym not in sym_series:
                sym_series[sym] = []
            sym_series[sym].append({
                "ts": ts,
                "pnl_pct": p.get("hyp_pnl_pct", 0),
                "change_pct": p.get("change_pct", 0),
                "confidence": p.get("confidence", 0),
                "bucket": p.get("bucket", ""),
                "direction": p.get("direction", ""),
            })

    scores = {}
    for sym, series in sym_series.items():
        if len(series) < 3:
            continue

        pnls = [s["pnl_pct"] for s in series]
        latest_pnl = pnls[-1]
        avg_pnl = sum(pnls) / len(pnls)
        conf = series[-1]["confidence"]

        # Trend: is it improving? (compare last third vs first third)
        third = max(1, len(pnls) // 3)
        early_avg = sum(pnls[:third]) / third
        late_avg = sum(pnls[-third:]) / third
        trend = late_avg - early_avg  # positive = improving

        # Consistency: std dev of P&L (lower = more consistent)
        mean = sum(pnls) / len(pnls)
        variance = sum((x - mean) ** 2 for x in pnls) / len(pnls)
        std_dev = variance ** 0.5
        consistency = max(0, 1.0 - std_dev / 5.0)  # normalize: std<5% = good

        # Magnitude
        magnitude = abs(latest_pnl)

        # Quality score: weighted combo
        quality = (
            latest_pnl * 2.0          # current performance matters most
            + trend * 1.5             # improving trend is valuable
            + avg_pnl * 1.0           # average performance
            + (conf / 100) * magnitude * 0.5  # confidence-weighted magnitude
            + consistency * 2.0       # consistency bonus
        )

        scores[sym] = {
            "quality": round(quality, 2),
            "latest_pnl_pct": round(latest_pnl, 2),
            "avg_pnl_pct": round(avg_pnl, 2),
            "trend": round(trend, 2),
            "consistency": round(consistency, 2),
            "confidence": conf,
            "bucket": series[-1]["bucket"],
            "direction": series[-1]["direction"],
            "samples": len(series),
        }

    # Save scores with dashboard-compatible fields
    dashboard_scores = {}
    for sym, info in scores.items():
        dashboard_scores[sym] = {
            **info,
            "quality_score": info["quality"],
            "win_rate": len([s for s in sym_series.get(sym, []) if s["pnl_pct"] > 0]) / max(len(sym_series.get(sym, [])), 1),
            "avg_pnl": info["avg_pnl_pct"],
            "days_tracked": 1,
        }

    with open(SCORES_FILE, "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(), "scores": dashboard_scores}, f, indent=2)

    return scores


def generate_recommendation(scores, data):
    """Based on quality scores, recommend a trade if confidence is high enough.

    Criteria for recommendation:
    - Quality score > 3.0 (strong consistent performer)
    - Not already held as a position
    - Available buying power
    - At least 6 snapshots of data (30+ minutes of tracking)
    """
    if not scores:
        return None

    # Get current positions
    positions = fetch_positions()
    held_symbols = {p["symbol"] for p in positions}

    # Get account
    acct = fetch_account()
    cash = float(acct.get("cash", 0))

    if cash < 10:
        return None

    # Rank by quality score
    ranked = sorted(scores.items(), key=lambda x: x[1]["quality"], reverse=True)

    for sym, info in ranked:
        if sym in held_symbols:
            continue
        if info["samples"] < 6:  # need at least 30 min of data
            continue
        if info["quality"] < 3.0:
            break  # no more good picks

        # Found a recommendation
        # Size: use available cash but cap at $30 per position
        size = min(cash, 30.0)

        return {
            "symbol": sym,
            "direction": info["direction"],
            "amount": round(size, 2),
            "quality_score": info["quality"],
            "latest_pnl_pct": info["latest_pnl_pct"],
            "trend": info["trend"],
            "consistency": info["consistency"],
            "confidence": info["confidence"],
            "bucket": info["bucket"],
            "samples": info["samples"],
            "reasoning": (
                f"{sym} has quality score {info['quality']:.1f} "
                f"(pnl: {info['latest_pnl_pct']:+.1f}%, "
                f"trend: {info['trend']:+.1f}%, "
                f"consistency: {info['consistency']:.0%}). "
                f"Engine confidence: {info['confidence']}%. "
                f"Tracked for {info['samples']} snapshots."
            ),
        }

    return None


def save_historical(scores):
    """Append today's final scores to historical file for cross-day learning."""
    history = {}
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except:
            history = {}

    # Compute daily summary stats
    winners = {s: v for s, v in scores.items() if v.get("latest_pnl_pct", 0) > 0}
    losers = {s: v for s, v in scores.items() if v.get("latest_pnl_pct", 0) <= 0}
    total_pnl = sum(v.get("latest_pnl_pct", 0) for v in scores.values())
    best = max(scores.items(), key=lambda x: x[1].get("latest_pnl_pct", 0)) if scores else ("", {})
    worst = min(scores.items(), key=lambda x: x[1].get("latest_pnl_pct", 0)) if scores else ("", {})

    history[today_str] = {
        "scores": scores,
        "total_hyp_pnl": round(total_pnl, 2),
        "winners": len(winners),
        "losers": len(losers),
        "best_pick": {"sym": best[0], "pnl_pct": best[1].get("latest_pnl_pct", 0)} if best[0] else {},
        "worst_pick": {"sym": worst[0], "pnl_pct": worst[1].get("latest_pnl_pct", 0)} if worst[0] else {},
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    # Keep last 30 days
    dates = sorted(history.keys())
    if len(dates) > 30:
        for old in dates[:-30]:
            del history[old]

    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# === MAIN LOOP ===

print(f"{'='*55}")
print(f"  WHAT-IF LEARNER STARTED")
print(f"  Log: {DAILY_LOG}")
print(f"  Scores: {SCORES_FILE}")
print(f"{'='*55}")

snapshot_count = 0
last_analysis = 0
last_recommendation = 0
recommendation_sent = False

while True:
    try:
        now = time.time()
        now_et = datetime.now(timezone(timedelta(hours=-4)))

        # Market session windows (ET = CDT UTC-4)
        hour = now_et.hour
        minute = now_et.minute
        # Pre-market: 4:00 AM – 9:30 AM ET (extended hours)
        premarket = (hour >= 4) and (hour < 9 or (hour == 9 and minute < 30))
        # Regular session: 9:30 AM – 4:00 PM ET
        market_open = (hour > 9 or (hour == 9 and minute >= 30)) and hour < 16

        if not market_open and not premarket:
            if snapshot_count > 0:
                # End of day: save historical data
                scores = analyze_patterns()
                if scores:
                    save_historical(scores)
                    print(f"\n  [EOD] Saved historical data. {len(scores)} symbols tracked today.")
                print(f"  [EOD] Market closed. {snapshot_count} snapshots collected today.")
                snapshot_count = 0
            time.sleep(60)
            continue

        if premarket:
            print(f"  [{now_et.strftime('%H:%M')}] PRE-MARKET — tracking extended hours until 9:30 AM ET")

        # === SNAPSHOT (every 5 min) ===
        data = fetch_whatif()
        if data:
            # Capture opening prices once at market open
            if market_open and not _opening_prices_captured:
                capture_opening_prices(data)
            snap = snapshot_picks(data)
            snapshot_count += 1
            n_picks = len(snap["picks"]) if snap else 0
            top = max(snap["picks"], key=lambda x: x["hyp_pnl"]) if snap and snap["picks"] else None
            top_str = f" | Best: {top['sym']} {top['hyp_pnl']:+.2f}" if top else ""
            print(f"  [{now_et.strftime('%H:%M')}] Snapshot #{snapshot_count} ({n_picks} picks){top_str}")

        # === ANALYSIS (every 30 min, after 6+ snapshots) ===
        if snapshot_count >= 6 and (now - last_analysis) >= 1800:
            last_analysis = now
            scores = analyze_patterns()
            if scores:
                ranked = sorted(scores.items(), key=lambda x: x[1]["quality"], reverse=True)
                print(f"\n  [ANALYSIS] Quality scores ({len(scores)} symbols):")
                for sym, info in ranked[:5]:
                    print(f"    {sym:<6s} Q:{info['quality']:+6.1f}  pnl:{info['latest_pnl_pct']:+5.1f}%  trend:{info['trend']:+5.1f}  cons:{info['consistency']:.0%}  [{info['bucket']}]")

                # === QUANTUM BRIDGE — feed performance data into quantum optimizer ===
                print(f"\n  [QBridge] Running quantum analysis on {len(scores)} picks...")
                qresult = run_quantum_bridge()
                if qresult:
                    rscore = qresult.get("research_score", 0)
                    influence = qresult.get("recommended_influence", "none")
                    top_w = qresult.get("top_weights", {})
                    print(f"  [QBridge] Research score: {rscore:.3f} ({influence})")
                    print(f"  [QBridge] Top quantum weights: {list(top_w.items())[:3]}")
                    # Boost quality scores for quantum top-weighted picks
                    q_weights = load_quantum_weights()
                    for sym in scores:
                        if sym in q_weights:
                            boost = q_weights[sym] * 2.0  # quantum weight boosts quality
                            scores[sym]["quality"] += boost
                            scores[sym]["quantum_boost"] = round(boost, 3)
                            scores[sym]["quantum_weight"] = q_weights[sym]
                    if q_weights:
                        print(f"  [QBridge] Boosted {len(q_weights)} picks with quantum weights")

                # === RECOMMENDATION ===
                if not recommendation_sent and (now - last_recommendation) >= 3600:
                    rec = generate_recommendation(scores, data)
                    if rec:
                        last_recommendation = now
                        print(f"\n  [RECOMMEND] {rec['symbol']} {rec['direction']} ${rec['amount']}")
                        print(f"    {rec['reasoning']}")

                        msg = (
                            "<b>WHAT-IF LEARNER RECOMMENDATION</b>\n\n"
                            f"<b>{rec['symbol']}</b> {rec['direction']} — ${rec['amount']:.2f}\n\n"
                            f"Quality Score: {rec['quality_score']:.1f}\n"
                            f"Today P&L: {rec['latest_pnl_pct']:+.1f}%\n"
                            f"Trend: {rec['trend']:+.1f}% (improving)\n"
                            f"Consistency: {rec['consistency']:.0%}\n"
                            f"Engine Confidence: {rec['confidence']}%\n"
                            f"Data Points: {rec['samples']} snapshots\n\n"
                            f"<i>{rec['reasoning']}</i>\n\n"
                            f"To approve: <code>touch {APPROVAL_FILE}</code>\n"
                            f"Or reply here and your operator will execute."
                            + (f"\n\nQuantum weight: {rec.get('quantum_weight', 0):.3f} | Boost: +{rec.get('quantum_boost', 0):.2f}" if rec.get('quantum_weight') else "")
                        )
                        send_telegram(msg)

                        # Check for approval
                        for _ in range(12):  # check for 1 minute
                            if APPROVAL_FILE.exists():
                                APPROVAL_FILE.unlink()
                                print(f"\n  [APPROVED] Executing {rec['symbol']} buy...")

                                try:
                                    order = post_order({
                                        "symbol": rec["symbol"],
                                        "notional": str(rec["amount"]),
                                        "side": "buy",
                                        "type": "market",
                                        "time_in_force": "day",
                                    })
                                    result_msg = (
                                        f"<b>ORDER EXECUTED</b>\n\n"
                                        f"{rec['symbol']} BUY ${rec['amount']:.2f}\n"
                                        f"Status: {order.get('status', '?')}\n"
                                        f"Order ID: {order.get('id', '?')[:12]}"
                                    )
                                    send_telegram(result_msg)
                                    print(f"  [ORDER] {order.get('status', '?')} — {order.get('id', '?')[:12]}")
                                    recommendation_sent = True
                                except Exception as e:
                                    send_telegram(f"Order failed: {e}")
                                    print(f"  [ORDER ERROR] {e}")
                                break
                            time.sleep(5)

        time.sleep(300)  # 5 minutes between snapshots

    except KeyboardInterrupt:
        print("\nLearner stopped.")
        break
    except Exception as e:
        print(f"  [ERROR] {e}")
        time.sleep(60)
