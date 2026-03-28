#!/usr/bin/env python3
"""
Global Sentinel — Service Health Dashboard + Automatic Recovery
Runs every 5 minutes via gs-health-dashboard.timer.

Features:
1. Comprehensive health check of all critical services
2. System resource monitoring (CPU, RAM, swap, disk)
3. Data freshness checks (quantum_feed bridges)
4. Connectivity checks (Telegram, Alpaca)
5. Health score 0-100 with trend tracking
6. Automatic service recovery with restart-loop protection
7. Emergency resource cleanup when thresholds breached
8. Telegram alerts only when health degrades below 70 or critical service dies
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO / "data" / "quantum_feed"
LOGS = REPO / "logs"
HEALTH_FILE = QF / "system_health.json"
RECOVERY_LOG = LOGS / "auto_recovery.jsonl"
HEALTH_HISTORY_FILE = LOGS / "health_history.jsonl"

# Critical always-on services
CRITICAL_SERVICES = [
    "gs-quantum-learner",
    "gs-data-gatherer",
    "gs-paper-trader",
    "gs-synthetic-simulator",
    # "gs-pnl-tracker",  # runs on timer, not a daemon
    "gs-fred-alerts",
    "gs-stop-loss",
    "gs-broker-router",
    "gs-conditional-orders",
]

# 24/7 bridges and their max age in hours
BRIDGES_24_7 = {
    "social_trending.json": 24,
    "defi_data.json": 24,
    "cboe_vix_data.json": 24,
    "treasury_fiscal.json": 24,
    "hmm_regime.json": 24,
    "polymarket_geopolitical.json": 6,
    "synthetic_trade_results.json": 4,
    "quantum_mc_results.json": 8,
    "news_impact.json": 12,
}

# Market-hours-only bridges
BRIDGES_MARKET = {
    "technical_analysis.json": 6,
    "short_interest.json": 48,
    "insider_clusters.json": 48,
    "price_forecasts.json": 8,
    "fundamental_scores.json": 24,
    "economic_surprise.json": 24,
}

# Load .env
_env = {}
_env_path = REPO / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.strip().split("=", 1)
            _env[_k.strip()] = _v.strip().strip('"').strip("'")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(cmd: str, timeout: int = 30) -> Tuple[str, int]:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return str(e), 1


def log_recovery(action: str, details: str, success: bool):
    """Append to auto_recovery.jsonl"""
    RECOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": iso_now(),
        "action": action,
        "details": details,
        "success": success,
    }
    with open(RECOVERY_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def send_telegram(msg: str):
    if _send_topic:
        try:
            _send_topic(msg[:4000], topic="system")
            return
        except Exception:
            pass
    # Fallback direct send
    try:
        import urllib.request
        token = _env.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return
        payload = json.dumps({
            "chat_id": "-1003898688720",
            "text": msg[:4000],
            "parse_mode": "HTML",
            "message_thread_id": 2200,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ============================================================
# Health Checks
# ============================================================

def check_services() -> Dict[str, Any]:
    """Check all critical services, return status map."""
    results = {}
    for svc in CRITICAL_SERVICES:
        out, rc = run(f"systemctl is-active {svc}")
        status = out.strip()
        results[svc] = {
            "status": status,
            "active": status == "active",
        }
        # Get uptime info
        if status == "active":
            uptime_out, _ = run(
                f"systemctl show {svc} --property=ActiveEnterTimestamp --value"
            )
            results[svc]["since"] = uptime_out
    return results


def check_resources() -> Dict[str, Any]:
    """Check CPU, RAM, swap, disk."""
    # CPU load
    load_out, _ = run("cat /proc/loadavg")
    load_1m = float(load_out.split()[0]) if load_out else 0.0
    cores_out, _ = run("nproc")
    cores = int(cores_out) if cores_out.isdigit() else 4

    # RAM
    mem_out, _ = run("free -m | grep Mem")
    mem_parts = mem_out.split()
    ram_total = int(mem_parts[1]) if len(mem_parts) > 1 else 1
    ram_used = int(mem_parts[2]) if len(mem_parts) > 2 else 0
    ram_pct = round(ram_used / ram_total * 100, 1) if ram_total > 0 else 0

    # Swap
    swap_out, _ = run("free -m | grep Swap")
    swap_parts = swap_out.split()
    swap_total = int(swap_parts[1]) if len(swap_parts) > 1 else 0
    swap_used = int(swap_parts[2]) if len(swap_parts) > 2 else 0
    swap_pct = round(swap_used / swap_total * 100, 1) if swap_total > 0 else 0

    # Disk
    disk_out, _ = run("df -h / | tail -1")
    disk_parts = disk_out.split()
    disk_pct = int(disk_parts[4].replace("%", "")) if len(disk_parts) > 4 else 0

    return {
        "cpu_load_1m": load_1m,
        "cpu_cores": cores,
        "ram_total_mb": ram_total,
        "ram_used_mb": ram_used,
        "ram_pct": ram_pct,
        "ram_available_mb": int(mem_parts[6]) if len(mem_parts) > 6 else ram_total - ram_used,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_used,
        "swap_pct": swap_pct,
        "disk_pct": disk_pct,
    }


def is_market_hours() -> bool:
    """Check if within US market hours (8 AM - 6 PM ET, weekdays)."""
    et = datetime.now(timezone(timedelta(hours=-4)))
    return 8 <= et.hour <= 18 and et.weekday() < 5


def check_data_freshness() -> Dict[str, Any]:
    """Check bridge data freshness."""
    now = time.time()
    stale = []
    fresh = []
    missing = []
    market = is_market_hours()

    bridges = dict(BRIDGES_24_7)
    if market:
        bridges.update(BRIDGES_MARKET)

    for fname, max_hours in bridges.items():
        fpath = QF / fname
        if not fpath.exists():
            missing.append(fname)
            continue
        age_h = (now - fpath.stat().st_mtime) / 3600
        if age_h > max_hours:
            stale.append({"file": fname, "age_hours": round(age_h, 1), "max_hours": max_hours})
        else:
            fresh.append(fname)

    return {
        "stale_count": len(stale),
        "fresh_count": len(fresh),
        "missing_count": len(missing),
        "stale_bridges": stale,
        "missing_bridges": missing,
    }


def check_last_trade() -> Dict[str, Any]:
    """Check when the last paper trade was placed."""
    trade_log = LOGS / "paper_trades.jsonl"
    if not trade_log.exists():
        # Try alternate locations
        for alt in [QF / "paper_trades.json", LOGS / "paper_trade_mirror.log"]:
            if alt.exists():
                age_h = (time.time() - alt.stat().st_mtime) / 3600
                return {"last_trade_age_hours": round(age_h, 1), "source": str(alt.name)}
        return {"last_trade_age_hours": None, "source": "not_found"}

    age_h = (time.time() - trade_log.stat().st_mtime) / 3600
    return {"last_trade_age_hours": round(age_h, 1), "source": "paper_trades.jsonl"}


def check_telegram() -> Dict[str, bool]:
    """Verify Telegram bot token is valid."""
    import urllib.request
    token = _env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"connected": False, "reason": "no_token"}
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        req = urllib.request.Request(url, headers={"User-Agent": "GS-HealthCheck/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return {"connected": data.get("ok", False), "bot_username": data.get("result", {}).get("username", "")}
    except Exception as e:
        return {"connected": False, "reason": str(e)[:100]}


def check_alpaca() -> Dict[str, bool]:
    """Quick Alpaca account ping."""
    import urllib.request
    api_key = _env.get("ALPACA_API_KEY", "")
    secret = _env.get("ALPACA_SECRET_KEY", "")
    base = _env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if not api_key or not secret:
        return {"connected": False, "reason": "no_credentials"}
    try:
        url = f"{base}/v2/account"
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret,
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return {
            "connected": True,
            "account_status": data.get("status", "unknown"),
            "equity": data.get("equity", "0"),
        }
    except Exception as e:
        return {"connected": False, "reason": str(e)[:100]}


# ============================================================
# Health Score Calculation
# ============================================================

def compute_health_score(
    services: Dict[str, Any],
    resources: Dict[str, Any],
    freshness: Dict[str, Any],
    telegram: Dict[str, Any],
    alpaca: Dict[str, Any],
) -> Tuple[int, str]:
    """Compute 0-100 health score."""
    score = 0

    # Each running critical service: +5 points (max 45 for 9 services)
    for svc, info in services.items():
        if info.get("active"):
            score += 5

    # CPU load < 4: +10
    if resources["cpu_load_1m"] < 4.0:
        score += 10

    # RAM < 80%: +10
    if resources["ram_pct"] < 80:
        score += 10

    # Disk < 85%: +10
    if resources["disk_pct"] < 85:
        score += 10

    # No stale bridges: +10
    if freshness["stale_count"] == 0:
        score += 10

    # Telegram working: +5
    if telegram.get("connected"):
        score += 5

    # Alpaca working: +5
    if alpaca.get("connected"):
        score += 5

    # Determine status
    if score >= 80:
        status = "healthy"
    elif score >= 50:
        status = "degraded"
    else:
        status = "critical"

    return score, status


# ============================================================
# Trend Tracking
# ============================================================

def get_historical_score(hours_ago: float) -> Optional[int]:
    """Get health score from N hours ago."""
    if not HEALTH_HISTORY_FILE.exists():
        return None
    target_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    best_entry = None
    best_diff = float("inf")
    try:
        with open(HEALTH_HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["timestamp"])
                    diff = abs((ts - target_time).total_seconds())
                    if diff < best_diff:
                        best_diff = diff
                        best_entry = entry
                except Exception:
                    continue
    except Exception:
        return None

    if best_entry and best_diff < 7200:  # Within 2 hours tolerance
        return best_entry.get("health_score")
    return None


def save_health_history(score: int):
    """Append current score to history. Keep last 48 hours."""
    HEALTH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": iso_now(), "health_score": score}
    with open(HEALTH_HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Trim to last 48 hours (576 entries at 5-min intervals)
    try:
        lines = HEALTH_HISTORY_FILE.read_text().strip().splitlines()
        if len(lines) > 600:
            HEALTH_HISTORY_FILE.write_text("\n".join(lines[-576:]) + "\n")
    except Exception:
        pass


# ============================================================
# Automatic Recovery (Feature #2)
# ============================================================

def get_restart_count(svc: str, window_seconds: int = 3600) -> int:
    """Count how many times a service was restarted in the last window."""
    if not RECOVERY_LOG.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    count = 0
    try:
        with open(RECOVERY_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("action") == "service_restart" and svc in entry.get("details", ""):
                        ts = datetime.fromisoformat(entry["timestamp"])
                        if ts > cutoff:
                            count += 1
                except Exception:
                    continue
    except Exception:
        pass
    return count


def auto_recover_services(services: Dict[str, Any]) -> List[str]:
    """Restart dead services with loop protection."""
    alerts = []
    for svc, info in services.items():
        if info.get("active"):
            continue

        # Check restart count in last hour
        restart_count = get_restart_count(svc)
        if restart_count >= 3:
            # Restart loop detected — disable and alert
            msg = f"Service {svc} has restarted {restart_count} times in 1 hour — disabling to prevent loop"
            alerts.append(msg)
            log_recovery("service_disabled", msg, True)
            run(f"sudo systemctl stop {svc}", timeout=15)
            # Don't actually disable (just stop) so daily audit can re-enable
            continue

        # Restart the dead service
        out, rc = run(f"sudo systemctl restart {svc}", timeout=30)
        time.sleep(2)
        check_out, _ = run(f"systemctl is-active {svc}")
        success = check_out.strip() == "active"
        detail = f"Restarted {svc}: {'OK' if success else 'FAILED'}"
        log_recovery("service_restart", detail, success)

        if success:
            alerts.append(f"Auto-restarted {svc} (OK)")
            # Update the services dict
            info["active"] = True
            info["status"] = "active"
        else:
            alerts.append(f"FAILED to restart {svc}")

    return alerts


def emergency_disk_cleanup(disk_pct: int) -> List[str]:
    """Emergency cleanup when disk > 90%."""
    alerts = []
    if disk_pct < 90:
        return alerts

    msg = f"EMERGENCY disk cleanup triggered at {disk_pct}%"
    alerts.append(msg)
    log_recovery("emergency_disk_cleanup", msg, True)

    cleanups = [
        # Truncate ALL logs > 50MB
        (f'find {LOGS}/ -name "*.log" -size +50M -exec truncate -s 5M {{}} \\;', "truncate logs >50MB"),
        (f'find {LOGS}/ -name "*.jsonl" -size +50M -exec sh -c \'tail -2000 "$1" > "$1.tmp" && mv "$1.tmp" "$1"\' _ {{}} \\;', "trim jsonl >50MB"),
        # Delete reports > 7 days
        (f"find {REPO}/reports/ -mtime +7 -type f -delete 2>/dev/null", "delete reports >7d"),
        # Delete dead letter > 1 day
        (f"find {LOGS}/dead_letter/ -mtime +1 -delete 2>/dev/null", "delete dead_letter >1d"),
        # Delete bridge cache > 3 days
        (f"find {LOGS}/bridge_cache/ -mtime +3 -delete 2>/dev/null", "delete bridge_cache >3d"),
        # Vacuum journal
        ("sudo journalctl --vacuum-size=200M 2>/dev/null", "vacuum journal to 200M"),
    ]

    for cmd, label in cleanups:
        run(cmd, timeout=60)

    disk_out, _ = run("df -h / | tail -1")
    new_pct = int(disk_out.split()[4].replace("%", "")) if disk_out else disk_pct
    alerts.append(f"Disk: {disk_pct}% -> {new_pct}%")
    log_recovery("emergency_disk_cleanup", f"Disk {disk_pct}% -> {new_pct}%", new_pct < disk_pct)

    return alerts


def emergency_swap_cleanup(resources: Dict[str, Any]) -> List[str]:
    """Kill largest non-critical process if swap > 90%."""
    alerts = []
    if resources["swap_pct"] < 90:
        return alerts

    msg = f"EMERGENCY swap cleanup triggered at {resources['swap_pct']}%"
    alerts.append(msg)
    log_recovery("emergency_swap_cleanup", msg, True)

    # Find top memory consumer that isn't critical
    out, _ = run("ps aux --sort=-%mem | head -15")
    critical_patterns = [
        "quantum_continuous_learner", "openclaw-gateway", "broker_state",
        "paper_trade", "data_gatherer", "stop_loss", "sshd", "systemd",
        "conditional_order", "broker_router", "fred_macro",
    ]

    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) > 10:
            pid, mem_pct, cmd = cols[1], float(cols[3]), " ".join(cols[10:])
            if mem_pct > 10 and not any(c in cmd for c in critical_patterns):
                kill_msg = f"Killing PID {pid} ({mem_pct}% mem): {cmd[:80]}"
                alerts.append(kill_msg)
                run(f"kill {pid}")
                log_recovery("swap_kill_process", kill_msg, True)
                break

    # If RAM available > 4GB, try swap reset
    if resources.get("ram_available_mb", 0) > 4096:
        run("sudo swapoff -a && sudo swapon -a", timeout=60)
        alerts.append("Cleared stale swap (RAM available > 4GB)")
        log_recovery("swap_reset", "swapoff/swapon with sufficient RAM", True)

    return alerts


# ============================================================
# Main
# ============================================================

def main():
    print(f"[{iso_now()}] Health Dashboard starting...")

    # Run all checks
    services = check_services()
    resources = check_resources()
    freshness = check_data_freshness()
    last_trade = check_last_trade()
    telegram = check_telegram()
    alpaca = check_alpaca()

    # Compute score
    score, status = compute_health_score(services, resources, freshness, telegram, alpaca)

    # Get trends
    score_1h = get_historical_score(1.0)
    score_24h = get_historical_score(24.0)

    # Save to history
    save_health_history(score)

    # ---- Automatic Recovery ----
    recovery_alerts: List[str] = []

    # 1. Restart dead services
    dead_services = [s for s, i in services.items() if not i.get("active")]
    if dead_services:
        recovery_alerts.extend(auto_recover_services(services))
        # Recompute score after recovery
        score, status = compute_health_score(services, resources, freshness, telegram, alpaca)

    # 2. Emergency disk cleanup
    recovery_alerts.extend(emergency_disk_cleanup(resources["disk_pct"]))

    # 3. Emergency swap cleanup
    recovery_alerts.extend(emergency_swap_cleanup(resources))

    # Build output JSON
    health_data = {
        "timestamp": iso_now(),
        "health_score": score,
        "status": status,
        "health_score_1h_ago": score_1h,
        "health_score_24h_ago": score_24h,
        "services": services,
        "resources": resources,
        "data_freshness": freshness,
        "last_trade": last_trade,
        "connectivity": {
            "telegram": telegram,
            "alpaca": alpaca,
        },
        "recovery_actions": recovery_alerts,
    }

    # Write health file
    QF.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(health_data, indent=2, default=str))
    print(f"[{iso_now()}] Health score: {score}/100 ({status})")
    print(f"[{iso_now()}] Written to {HEALTH_FILE}")

    # ---- Telegram alerting ----
    # Only alert if score < 70, a critical service died, or recovery actions taken
    should_alert = False
    alert_reasons = []

    if score < 70:
        should_alert = True
        alert_reasons.append(f"Health score {score}/100 ({status})")

    if dead_services:
        should_alert = True
        alert_reasons.append(f"Dead services: {', '.join(dead_services)}")

    if recovery_alerts:
        should_alert = True
        alert_reasons.append(f"{len(recovery_alerts)} recovery actions taken")

    if should_alert:
        msg = f"<b>System Health Alert</b>\n\n"
        msg += f"Score: <b>{score}/100</b> ({status})\n"
        if score_1h is not None:
            msg += f"1h ago: {score_1h}/100\n"
        if score_24h is not None:
            msg += f"24h ago: {score_24h}/100\n"
        msg += "\n"

        for reason in alert_reasons:
            msg += f"- {reason}\n"

        if recovery_alerts:
            msg += "\n<b>Recovery actions:</b>\n"
            for action in recovery_alerts[:8]:
                msg += f"  - {action}\n"

        # Service status summary
        msg += "\n<b>Services:</b>\n"
        for svc, info in services.items():
            icon = "OK" if info.get("active") else "DOWN"
            msg += f"  [{icon}] {svc}\n"

        send_telegram(msg)
        print(f"[{iso_now()}] Telegram alert sent")

    print(f"[{iso_now()}] Health Dashboard complete")


if __name__ == "__main__":
    main()
