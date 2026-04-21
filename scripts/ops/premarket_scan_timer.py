#!/usr/bin/env python3
"""Pre-market scan timer daemon for Global Sentinel.

Runs as a background process and fires scheduled actions on trading days:
  08:00 ET  — Touch crisis_monitor trigger file
  09:25 ET  — Send pre-market Telegram summary (positions, ideas, oil, scanner)
  15:50 ET  — Send EOD flatten warnings for day_trade positions
  16:05 ET  — Send daily P&L summary

Usage:
  python scripts/ops/premarket_scan_timer.py          # daemon mode
  python scripts/ops/premarket_scan_timer.py --once   # run all events once then exit
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path("/opt/global-sentinel")
sys.path.insert(0, str(REPO_ROOT))

ET = ZoneInfo("America/New_York")
LOG_DIR = REPO_ROOT / "logs" / "execution"
LOG_FILE = LOG_DIR / "premarket_timer.jsonl"
TRIGGER_FILE = REPO_ROOT / "control" / "crisis_monitor_trigger"

# Schedule: (hour, minute, handler_name)
SCHEDULE = [
    (8, 0, "crisis_trigger"),
    (9, 25, "premarket_summary"),
    (9, 31, "submit_queued_orders"),
    (15, 50, "eod_warning"),
    (16, 5, "daily_summary"),
]


# ---------------------------------------------------------------------------
# Env + logging
# ---------------------------------------------------------------------------

def load_env():
    """Parse .env at repo root and inject into os.environ."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val


def log_event(event: str, data: dict | None = None):
    """Append a JSON line to the timer log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(ET).isoformat(),
        "event": event,
    }
    if data:
        entry["data"] = data
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass
    print(f"[{entry['ts']}] {event}", flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_weekday(d: date) -> bool:
    """True if d is Mon-Fri (rough proxy for trading day)."""
    return d.weekday() < 5


def get_latest_scorecard() -> dict | None:
    """Load the most recent scorecard_*.json from logs/scorecards/."""
    pattern = str(REPO_ROOT / "logs" / "scorecards" / "scorecard_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    try:
        with open(files[-1]) as f:
            return json.load(f)
    except Exception as exc:
        log_event("scorecard_load_error", {"error": str(exc), "path": files[-1]})
        return None


def get_positions_and_account() -> tuple[list[dict], dict]:
    """Fetch live positions and account state from Alpaca adapter."""
    try:
        from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter
        adapter = AlpacaPaperAdapter()
        positions = adapter.list_positions()
        account = adapter.get_account_state()
        return positions, account
    except Exception as exc:
        log_event("broker_fetch_error", {"error": str(exc)})
        return [], {}


def get_oil_regime() -> dict:
    """Fetch current oil regime status."""
    try:
        from src.alpha.oil_shock_regime import OilShockRegime
        regime = OilShockRegime()
        return regime.assess() if hasattr(regime, "assess") else {"regime": "UNKNOWN"}
    except Exception as exc:
        log_event("oil_regime_error", {"error": str(exc)})
        return {"regime": "UNKNOWN", "error": str(exc)}


def send_telegram(text: str, topic: str = "v6_digest"):
    """Send message via TelegramTopicNotifier."""
    try:
        from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier
        notifier = TelegramTopicNotifier(topic=topic)
        result = notifier.send_message(text)
        log_event("telegram_sent", {"ok": result.ok, "reason": result.reason, "topic": topic})
        return result
    except Exception as exc:
        log_event("telegram_error", {"error": str(exc), "topic": topic})
        return None


# ---------------------------------------------------------------------------
# Scheduled handlers
# ---------------------------------------------------------------------------

def handle_crisis_trigger():
    """08:00 ET — Touch trigger file for crisis_monitor cycle."""
    TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRIGGER_FILE.touch()
    log_event("crisis_trigger_touched", {"path": str(TRIGGER_FILE)})


def handle_premarket_summary():
    """09:25 ET — Pre-market brief: positions, top ideas, oil regime, scanner."""
    scorecard = get_latest_scorecard()
    positions, account = get_positions_and_account()
    oil = get_oil_regime()

    lines = ["-- PRE-MARKET BRIEF --"]

    # Account + positions
    equity = account.get("equity", 0)
    cash = account.get("cash", 0)
    lines.append(f"Equity: ${equity:,.0f} | Cash: ${cash:,.0f}")

    if positions:
        total_pnl = sum(p.get("unrealized_pl", 0) or 0 for p in positions)
        lines.append(f"Open positions: {len(positions)} | Unrealized P&L: ${total_pnl:+,.2f}")
        for p in positions[:10]:
            sym = p.get("symbol", "?")
            qty = p.get("qty", 0)
            upl = p.get("unrealized_pl", 0) or 0
            entry = p.get("avg_entry_price", 0) or 0
            lines.append(f"  {sym}: {qty} @ ${entry:.2f} | P&L ${upl:+,.2f}")
    else:
        lines.append("No open positions.")

    # Top 10 strategy ideas from scorecard
    lines.append("")
    if scorecard:
        ideas = scorecard.get("strategy_ideas", scorecard.get("trade_ideas", []))
        if ideas:
            lines.append(f"Top ideas ({len(ideas)} total):")
            for idea in ideas[:10]:
                if isinstance(idea, dict):
                    sym = idea.get("symbol", idea.get("ticker", "?"))
                    strat = idea.get("strategy", idea.get("type", ""))
                    conf = idea.get("confidence", idea.get("score", 0))
                    lines.append(f"  {sym} | {strat} | conf={conf:.2f}")
                else:
                    lines.append(f"  {idea}")
        else:
            # Fall back to component scores summary
            comps = scorecard.get("component_scores", {})
            mode = scorecard.get("mode", "?")
            rsp = scorecard.get("regime_shift_probability", 0)
            lines.append(f"Scorecard mode={mode}, regime_shift={rsp:.2f}")
            if comps:
                top_comps = sorted(comps.items(), key=lambda x: x[1], reverse=True)[:5]
                for name, val in top_comps:
                    lines.append(f"  {name}: {val:.3f}")
    else:
        lines.append("No scorecard available.")

    # Oil regime
    lines.append("")
    regime_name = oil.get("regime", oil.get("current_regime", "?"))
    oil_price = oil.get("price", oil.get("wti_price", "?"))
    lines.append(f"Oil regime: {regime_name} | WTI: ${oil_price}")

    # Scanner discoveries (from scorecard evidence)
    if scorecard:
        evidence = scorecard.get("evidence", [])
        if evidence:
            lines.append("")
            lines.append("Scanner signals:")
            for ev in evidence[:5]:
                lines.append(f"  {ev}")

    msg = "\n".join(lines)
    send_telegram(msg, topic="v6_digest")
    log_event("premarket_summary_sent", {"positions": len(positions)})


def handle_eod_warning():
    """15:50 ET — Warn about open day_trade positions that must flatten."""
    positions, account = get_positions_and_account()

    if not positions:
        log_event("eod_warning_skipped", {"reason": "no_positions"})
        return

    lines = ["-- EOD FLATTEN WARNING (10 min to close) --"]
    lines.append(f"Open positions: {len(positions)}")

    total_pnl = 0
    for p in positions:
        sym = p.get("symbol", "?")
        qty = p.get("qty", 0)
        upl = p.get("unrealized_pl", 0) or 0
        mv = p.get("market_value", 0) or 0
        total_pnl += upl
        lines.append(f"  {sym}: {qty} shares | MV ${mv:,.0f} | P&L ${upl:+,.2f}")

    lines.append(f"Total unrealized P&L: ${total_pnl:+,.2f}")
    lines.append("")
    lines.append("Action: Review and flatten day-trade positions before 4:00 PM ET.")

    msg = "\n".join(lines)
    send_telegram(msg, topic="v6_digest")
    log_event("eod_warning_sent", {"positions": len(positions), "total_pnl": total_pnl})


def handle_daily_summary():
    """16:05 ET — Post-close daily P&L summary."""
    positions, account = get_positions_and_account()
    scorecard = get_latest_scorecard()

    equity = account.get("equity", 0)
    cash = account.get("cash", 0)

    lines = ["-- DAILY P&L SUMMARY (post-close) --"]
    lines.append(f"Equity: ${equity:,.0f} | Cash: ${cash:,.0f}")

    if positions:
        total_pnl = sum(p.get("unrealized_pl", 0) or 0 for p in positions)
        lines.append(f"Remaining positions: {len(positions)} | Unrealized: ${total_pnl:+,.2f}")
        for p in positions:
            sym = p.get("symbol", "?")
            qty = p.get("qty", 0)
            upl = p.get("unrealized_pl", 0) or 0
            lines.append(f"  {sym}: {qty} @ ${p.get('avg_entry_price', 0):.2f} | ${upl:+,.2f}")
    else:
        lines.append("All positions flat.")

    if scorecard:
        mode = scorecard.get("mode", "?")
        rsp = scorecard.get("regime_shift_probability", 0)
        lines.append(f"\nRegime: mode={mode}, shift_prob={rsp:.2f}")

    msg = "\n".join(lines)
    send_telegram(msg, topic="v6_digest")
    log_event("daily_summary_sent", {"equity": equity, "positions": len(positions)})


def handle_submit_queued_orders():
    """09:31 ET — Submit queued medium_long orders after DTBP resets at open."""
    queue_path = REPO_ROOT / "control" / "queued_orders.json"
    if not queue_path.exists():
        log_event("queued_orders_skip", {"reason": "no_queue_file"})
        return

    try:
        import requests
        queue = json.loads(queue_path.read_text())
        orders = queue.get("orders", [])
        if not orders:
            log_event("queued_orders_skip", {"reason": "empty"})
            return

        env = {}
        with open(REPO_ROOT / ".env") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"\'')

        key_map = {
            "medium_long": ("ALPACA_API_KEY_MEDLONG", "ALPACA_SECRET_KEY_MEDLONG"),
            "day_trade": ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"),
        }

        results = []
        for order in orders:
            acct = order.get("account", "medium_long")
            kv, sv = key_map.get(acct, key_map["medium_long"])
            headers = {
                "APCA-API-KEY-ID": env.get(kv, ""),
                "APCA-API-SECRET-KEY": env.get(sv, ""),
                "Content-Type": "application/json",
            }
            payload = {
                "symbol": order["symbol"],
                "side": order["side"],
                "type": "market",
                "time_in_force": "day",
            }
            if order["side"] == "buy":
                payload["notional"] = str(order["notional"])
            else:
                payload["qty"] = str(order["qty"])

            import time as _time
            r = requests.post(
                "https://paper-api.alpaca.markets/v2/orders",
                headers=headers, json=payload, timeout=15,
            )
            ok = r.status_code in (200, 201)
            results.append({
                "symbol": order["symbol"],
                "side": order["side"],
                "ok": ok,
                "status": r.status_code,
            })
            log_event("queued_order_submitted", {
                "symbol": order["symbol"],
                "side": order["side"],
                "ok": ok,
                "http": r.status_code,
                "detail": r.text[:200] if not ok else "",
            })
            _time.sleep(0.4)

        # Archive queue file after processing
        archive = queue_path.with_suffix(".done.json")
        queue_path.rename(archive)

        ok_count = sum(1 for r in results if r["ok"])
        err_count = len(results) - ok_count
        msg = f"QUEUED ORDERS SUBMITTED\n{ok_count} OK / {err_count} errors out of {len(results)}"
        for r in results:
            status = "OK" if r["ok"] else f"ERR {r['status']}"
            msg += f"\n  {r['symbol']} {r['side']} -> {status}"
        send_telegram(msg, topic="v6_digest")
        log_event("queued_orders_done", {"ok": ok_count, "errors": err_count})

    except Exception as exc:
        log_event("queued_orders_error", {"error": str(exc)})


HANDLERS = {
    "crisis_trigger": handle_crisis_trigger,
    "premarket_summary": handle_premarket_summary,
    "submit_queued_orders": handle_submit_queued_orders,
    "eod_warning": handle_eod_warning,
    "daily_summary": handle_daily_summary,
}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once():
    """Execute all handlers immediately (for testing)."""
    log_event("run_once_start")
    for _, _, name in SCHEDULE:
        log_event(f"run_once_{name}")
        try:
            HANDLERS[name]()
        except Exception as exc:
            log_event(f"handler_error_{name}", {"error": str(exc)})
    log_event("run_once_complete")


def run_daemon():
    """Sleep-loop daemon that fires handlers at their scheduled ET times."""
    log_event("daemon_start")
    fired_today: set[str] = set()
    last_date: date | None = None

    while True:
        now = datetime.now(ET)
        today = now.date()

        # Reset fired set on new day
        if last_date != today:
            fired_today.clear()
            last_date = today

        # Skip weekends
        if not is_weekday(today):
            time.sleep(60)
            continue

        for hour, minute, name in SCHEDULE:
            if name in fired_today:
                continue
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                log_event(f"firing_{name}", {"scheduled": f"{hour:02d}:{minute:02d} ET"})
                try:
                    HANDLERS[name]()
                except Exception as exc:
                    log_event(f"handler_error_{name}", {"error": str(exc)})
                fired_today.add(name)

        # Sleep 30s between checks — lightweight
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description="Pre-market scan timer daemon")
    parser.add_argument("--once", action="store_true", help="Run all events once then exit")
    args = parser.parse_args()

    load_env()
    log_event("init", {"mode": "once" if args.once else "daemon", "repo": str(REPO_ROOT)})

    if args.once:
        run_once()
    else:
        run_daemon()


if __name__ == "__main__":
    main()
