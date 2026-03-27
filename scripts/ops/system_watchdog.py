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
    "stress_test.json": 48 * 3600,
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
    """Check if we're in extended US market hours (8 AM - 6 PM ET, weekdays)."""
    from datetime import timedelta
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    if et.weekday() >= 5:  # Saturday/Sunday
        return False
    return 8 <= et.hour < 18

# Bridge re-run commands (only for 24/7 sources)
BRIDGE_RERUN = {
    "polymarket_geopolitical.json": "python3 src/bridges/polymarket_bridge.py",
    "reddit_trending.json": "python3 src/bridges/apewisdom_bridge.py",
    "hmm_regime.json": "python3 src/research/hmm_regime_detector.py",
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
            "parse_mode": "HTML",
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
    """Check bridge signal freshness and re-run stale ones."""
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
        if not filepath.exists():
            stale.append((filename, "missing"))
            continue
        mtime = filepath.stat().st_mtime
        age = now - mtime
        if age > ttl:
            age_hours = age / 3600
            stale.append((filename, f"{age_hours:.1f}h old"))

            # Try to re-run if we have a command
            if filename in BRIDGE_RERUN:
                cmd = BRIDGE_RERUN[filename]
                log(f"Re-running stale bridge: {filename} ({age_hours:.1f}h old)")
                try:
                    result = subprocess.run(
                        cmd.split(),
                        capture_output=True, text=True, timeout=120,
                        cwd=str(REPO),
                        env={**os.environ, "PYTHONPATH": str(REPO)},
                    )
                    if result.returncode == 0:
                        rerun_ok.append(filename)
                        log(f"Bridge {filename} re-run OK")
                    else:
                        rerun_fail.append(filename)
                        log(f"Bridge {filename} re-run FAILED: {result.stderr[-200:]}")
                except Exception as e:
                    rerun_fail.append(filename)
                    log(f"Bridge {filename} re-run error: {e}")

    return stale, rerun_ok, rerun_fail


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
        msg = "🚨 <b>GS Watchdog Alert</b>\n" + "\n".join(alert_lines)
        send_telegram_alert(msg)

    log("=== Watchdog check complete ===")


if __name__ == "__main__":
    main()
