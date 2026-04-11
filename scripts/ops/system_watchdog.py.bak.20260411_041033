#!/usr/bin/env python3
"""
Global Sentinel System Watchdog
Runs every 15 minutes via gs-watchdog.timer.
- Checks all gs-* services, restarts failed ones
- Checks bridge signal freshness, re-runs stale ones
- Sends Telegram alert if multiple failures
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO / "data" / "quantum_feed"
LOG_FILE = REPO / "logs" / "watchdog.log"
ENV_FILE = REPO / ".env"

# 24/7 bridges (always checked)
BRIDGE_TTLS_24_7 = {
    "polymarket_geopolitical.json": 4 * 3600,
    "reddit_trending.json": 4 * 3600,
    "hmm_regime.json": 4 * 3600,
    "synthetic_trade_results.json": 2 * 3600,
    "quantum_mc_results.json": 6 * 3600,
    "self_improver_state.json": 48 * 3600,
    "quantum_regime_prediction.json": 72 * 3600,
}

# Market-hours-only bridges (skip checking outside 9:30 AM - 5 PM ET, weekdays)
BRIDGE_TTLS_MARKET = {
    "price_forecasts.json": 8 * 3600,
    "economic_surprise.json": 24 * 3600,
    "short_interest.json": 48 * 3600,
    "stress_test_results.json": 48 * 3600,
    "factor_exposure.json": 48 * 3600,
    "technical_analysis.json": 8 * 3600,
    "fundamental_scores.json": 24 * 3600,
    "insider_clusters.json": 48 * 3600,
    "news_impact.json": 8 * 3600,
    "social_trending.json": 8 * 3600,
    "ensemble_signals.json": 4 * 3600,
    "strategy_master.json": 4 * 3600,
}


def is_market_hours():
    """Check if we are in extended US market hours (8 AM - 6 PM ET, weekdays)."""
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Conservative fallback: treat as market hours.
        return True

    if et.weekday() >= 5:  # Saturday/Sunday
        return False
    return 8 <= et.hour < 18

# Bridge re-run commands (best-effort; some require credentials)
BRIDGE_RERUN = {
    "polymarket_geopolitical.json": "python3 src/bridges/polymarket_bridge.py",
    "reddit_trending.json": "python3 src/bridges/apewisdom_bridge.py",
    "hmm_regime.json": "python3 src/research/hmm_regime_detector.py",

    # Market bridges
    "short_interest.json": "python3 src/bridges/finra_short_interest_bridge.py",
    "technical_analysis.json": "python3 src/bridges/technical_analysis_bridge.py",
    "insider_clusters.json": "python3 src/bridges/openinsider_bridge.py",
    "social_trending.json": "python3 src/bridges/social_trending_bridge.py",
    "factor_exposure.json": "python3 src/research/factor_decomposition.py",
    "stress_test_results.json": "python3 src/research/stress_tester.py",
}

# Services that should always be running
ALWAYS_RUNNING = [
    "gs-data-gatherer.service",
    "gs-synthetic-simulator.service",
    "gs-quantum-learner.service",
    "gs-stop-loss.service",
    "gs-vol-trader.service",
    "gs-whatif-learner.service",
    "gs-broker-router.service",
    "gs-conditional-orders.service",
    "gs-paper-trader.service",
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] WATCHDOG: {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_env():
    """Load .env file for Telegram credentials."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def send_telegram_alert(message):
    """Send alert via Telegram."""
    if _send_topic:
        try:
            _send_topic(message[:4000] if isinstance(message, str) else str(message)[:4000], topic="system")
            return
        except Exception:
            pass
    env = load_env()
    bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        log("No Telegram credentials found, skipping alert")
        return
    try:
        import urllib.request
        import urllib.parse
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML", "message_thread_id": 74,
        }).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
        log("Telegram alert sent")
    except Exception as e:
        log(f"Telegram alert failed: {e}")


def check_services():
    """Check all gs-* services and restart failed ones."""
    failed = []
    restarted = []

    for svc in ALWAYS_RUNNING:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=10
            )
            status = result.stdout.strip()
            if status != "active":
                log(f"Service {svc} is {status}, restarting...")
                failed.append(svc)
                restart = subprocess.run(
                    ["sudo", "systemctl", "restart", svc],
                    capture_output=True, text=True, timeout=30
                )
                if restart.returncode == 0:
                    restarted.append(svc)
                    log(f"Service {svc} restarted successfully")
                else:
                    log(f"Service {svc} restart FAILED: {restart.stderr}")
        except Exception as e:
            log(f"Error checking {svc}: {e}")
            failed.append(svc)

    return failed, restarted


def check_bridges():
    """Check bridge signal freshness and re-run stale/missing ones (best effort)."""
    now = time.time()
    stale = []
    rerun_ok = []
    rerun_fail = []

    # Always check 24/7 bridges; only check market bridges during market hours
    ttls = dict(BRIDGE_TTLS_24_7)
    if is_market_hours():
        ttls.update(BRIDGE_TTLS_MARKET)

    for filename, ttl in ttls.items():
        filepath = QF / filename
        before_exists = filepath.exists()
        before_mtime = filepath.stat().st_mtime if before_exists else None

        status = None
        if not before_exists:
            status = "missing"
        else:
            age = now - (before_mtime or now)
            if age > ttl:
                status = f"{age / 3600:.1f}h old"

        if not status:
            continue

        # Try to re-run if we have a command
        if filename in BRIDGE_RERUN:
            cmd = BRIDGE_RERUN[filename]
            log(f"Re-running bridge: {filename} ({status}) -> {cmd}")
            try:
                result = subprocess.run(
                    cmd.split(),
                    capture_output=True, text=True, timeout=240,
                    cwd=str(REPO),
                    env={**os.environ, "PYTHONPATH": str(REPO)},
                )
                after_exists = filepath.exists()
                after_mtime = filepath.stat().st_mtime if after_exists else None
                wrote_file = after_exists and (
                    (not before_exists) or (before_mtime is not None and after_mtime is not None and after_mtime > before_mtime)
                )

                if result.returncode == 0 and wrote_file:
                    rerun_ok.append(filename)
                    log(f"Bridge {filename} re-run OK (file updated)")
                else:
                    rerun_fail.append(filename)
                    err_tail = (result.stderr or "")[-300:].strip().replace("\n", " | ")
                    out_tail = (result.stdout or "")[-300:].strip().replace("\n", " | ")
                    log(
                        f"Bridge {filename} re-run FAILED (code={result.returncode}, wrote_file={wrote_file}) "
                        f"stderr_tail={err_tail} stdout_tail={out_tail}"
                    )
            except Exception as e:
                rerun_fail.append(filename)
                log(f"Bridge {filename} re-run error: {e}")

        # Re-evaluate after best-effort rerun.
        if not filepath.exists():
            stale.append((filename, "missing"))
            continue

        final_age = time.time() - filepath.stat().st_mtime
        if final_age > ttl:
            stale.append((filename, f"{final_age / 3600:.1f}h old"))

    return stale, rerun_ok, rerun_fail


WATCHDOG_STATE_FILE = REPO / "logs" / "watchdog_state.json"
ALERT_REPEAT_SUPPRESSION_SECONDS = 6 * 3600


def _load_watchdog_state():
    try:
        if WATCHDOG_STATE_FILE.exists():
            raw = WATCHDOG_STATE_FILE.read_text(encoding="utf-8").strip()
            if raw:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    return obj
    except Exception:
        pass
    return {}


def _save_watchdog_state(state):
    try:
        WATCHDOG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHDOG_STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _alert_signature(failed_svcs, stale_bridges):
    stale_names = sorted([n for (n, _age) in stale_bridges])
    failed_names = sorted(list(failed_svcs))
    return json.dumps({"failed": failed_names, "stale": stale_names}, sort_keys=True)


def _should_send_alert(failed_svcs, stale_bridges):
    state = _load_watchdog_state()
    sig = _alert_signature(failed_svcs, stale_bridges)

    last_sig = str(state.get("last_alert_sig", ""))
    last_ts = float(state.get("last_alert_ts_epoch", 0.0) or 0.0)
    now_epoch = time.time()

    if sig == last_sig and (now_epoch - last_ts) < ALERT_REPEAT_SUPPRESSION_SECONDS:
        return False

    state["last_alert_sig"] = sig
    state["last_alert_ts_epoch"] = now_epoch
    _save_watchdog_state(state)
    return True



def main():
    log("=== Watchdog check started ===")

    # Check services
    failed_svcs, restarted_svcs = check_services()
    log(f"Services: {len(failed_svcs)} failed, {len(restarted_svcs)} restarted")

    # Check bridges
    stale_bridges, rerun_ok, rerun_fail = check_bridges()
    log(f"Bridges: {len(stale_bridges)} stale, {len(rerun_ok)} re-run OK, {len(rerun_fail)} re-run failed")

    # Alert if too many failures
    alert_lines = []
    if len(failed_svcs) >= 3:
        alert_lines.append(f"<b>SERVICES DOWN ({len(failed_svcs)}):</b>")
        for svc in failed_svcs:
            status = "restarted" if svc in restarted_svcs else "STILL DOWN"
            alert_lines.append(f"  - {svc} ({status})")

    if len(stale_bridges) >= 5:
        alert_lines.append(f"\n<b>STALE BRIDGES ({len(stale_bridges)}):</b>")
        for name, age in stale_bridges[:10]:
            alert_lines.append(f"  - {name}: {age}")

    if alert_lines:
        if _should_send_alert(failed_svcs, stale_bridges):
            msg = "🚨 <b>GS Watchdog Alert</b>\n" + "\n".join(alert_lines)
            send_telegram_alert(msg)
        else:
            log("Suppressing duplicate alert (same signature within cooldown).")

    log("=== Watchdog check complete ===")


if __name__ == "__main__":
    main()
