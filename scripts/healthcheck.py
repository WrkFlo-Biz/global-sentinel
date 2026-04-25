#!/usr/bin/env python3
"""Global Sentinel V4 — Healthcheck Watchdog

Checks heartbeat file, process health, disk space, and service status.
Returns exit code 0 if healthy, 1 if degraded, 2 if critical.
"""

import json
import os
import sys
import time  # noqa: F401
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.control_state_snapshot import read_control_state_snapshot

HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", str(PROJECT_ROOT / "logs" / "heartbeat.json"))
MAX_HEARTBEAT_AGE_SECONDS = 1800  # 30 minutes


def check_heartbeat() -> dict:
    hb = Path(HEARTBEAT_FILE)
    if not hb.exists():
        return {"status": "critical", "message": "No heartbeat file found"}
    try:
        raw = hb.read_text().strip()
        try:
            data = json.loads(raw)
            ts = data.get("timestamp_utc", raw)
        except (json.JSONDecodeError, TypeError):
            ts = raw
        last_beat = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - last_beat).total_seconds()
        if age > MAX_HEARTBEAT_AGE_SECONDS:
            return {"status": "critical", "message": f"Heartbeat stale ({age:.0f}s old)", "age_seconds": age}
        return {"status": "ok", "age_seconds": age}
    except Exception as e:
        return {"status": "critical", "message": str(e)}


def check_disk() -> dict:
    if not psutil:
        return {"status": "unknown", "message": "psutil not installed"}
    usage = psutil.disk_usage("/")
    pct = usage.percent
    if pct > 95:
        return {"status": "critical", "message": f"Disk {pct}% full", "percent": pct}
    if pct > 85:
        return {"status": "degraded", "message": f"Disk {pct}% full", "percent": pct}
    return {"status": "ok", "percent": pct}


def check_memory() -> dict:
    if not psutil:
        return {"status": "unknown", "message": "psutil not installed"}
    mem = psutil.virtual_memory()
    if mem.percent > 95:
        return {"status": "critical", "message": f"Memory {mem.percent}% used", "percent": mem.percent}
    if mem.percent > 85:
        return {"status": "degraded", "message": f"Memory {mem.percent}% used", "percent": mem.percent}
    return {"status": "ok", "percent": mem.percent}


def check_controls() -> dict:
    control_snapshot = read_control_state_snapshot(PROJECT_ROOT)
    alerts = []
    if control_snapshot["manual_veto"]:
        alerts.append("MANUAL VETO ACTIVE")
    if control_snapshot["kill_switch"]:
        alerts.append("KILL SWITCH ACTIVE")
    return {"status": "alert" if alerts else "ok", "alerts": alerts}


def main():
    checks = {
        "heartbeat": check_heartbeat(),
        "disk": check_disk(),
        "memory": check_memory(),
        "controls": check_controls(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    statuses = [c.get("status", "unknown") for c in checks.values() if isinstance(c, dict)]
    if "critical" in statuses:
        checks["overall"] = "CRITICAL"
        exit_code = 2
    elif "degraded" in statuses or "alert" in statuses:
        checks["overall"] = "DEGRADED"
        exit_code = 1
    else:
        checks["overall"] = "HEALTHY"
        exit_code = 0

    print(json.dumps(checks, indent=2, default=str))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
