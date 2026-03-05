#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Status Reporter

Quick status report for bot integrations (OpenClaw, Telegram, Slack).
Called by the bot to answer /regime, /scorecard, /status commands.

Usage:
    python3 scripts/ops/sentinel_status.py --repo-root /opt/global-sentinel
    python3 scripts/ops/sentinel_status.py --repo-root /opt/global-sentinel --format telegram
    python3 scripts/ops/sentinel_status.py --repo-root /opt/global-sentinel --command scorecard
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def latest_scorecard(scorecards_dir: Path) -> Optional[Dict[str, Any]]:
    if not scorecards_dir.exists():
        return None
    files = sorted(scorecards_dir.glob("scorecard_*.json"), reverse=True)
    if not files:
        return None
    return load_json(files[0])


def mode_emoji(mode: str) -> str:
    return {"NORMAL": "🟢", "ELEVATED": "🟡", "CRISIS": "🔴", "MANUAL_REVIEW": "🟠"}.get(mode, "⚪")


def format_status(repo_root: Path, fmt: str = "text") -> str:
    heartbeat = load_json(repo_root / "logs" / "heartbeat.json")
    sc = latest_scorecard(repo_root / "logs" / "scorecards")
    kill = load_json(repo_root / "control" / "kill_switch.json")
    veto = load_json(repo_root / "control" / "manual_veto.json")

    mode = heartbeat.get("mode", "UNKNOWN")
    cycle = heartbeat.get("cycle", 0)
    hb_ts = heartbeat.get("timestamp_utc", "unknown")
    hb_status = heartbeat.get("status", "unknown")

    if sc:
        regime_p = sc.get("regime_shift_probability", 0)
        confidence = sc.get("confidence", 0)
        bridge_summary = sc.get("bridge_summary", {})
        shadow_eligible = sc.get("shadow_execution_eligible", False)
    else:
        regime_p = confidence = 0
        bridge_summary = {}
        shadow_eligible = False

    if fmt == "telegram":
        emoji = mode_emoji(mode)
        lines = [
            f"{emoji} <b>Global Sentinel Status</b>",
            f"",
            f"Mode: <b>{mode}</b> | Cycle: {cycle}",
            f"Regime P: <b>{regime_p:.3f}</b> | Confidence: {confidence:.3f}",
            f"Shadow eligible: {'Yes' if shadow_eligible else 'No'}",
            f"Kill switch: {'🛑 ACTIVE' if kill.get('active') else '✅ Off'}",
            f"Manual veto: {'🟠 ACTIVE' if veto.get('active') else '✅ Off'}",
            f"",
            f"Bridges:",
        ]
        for k, v in bridge_summary.items():
            lines.append(f"  • {k}: {v}")
        lines.append(f"\nLast heartbeat: {hb_ts}")
        return "\n".join(lines)
    else:
        lines = [
            f"Mode: {mode} (cycle {cycle})",
            f"Regime P: {regime_p:.3f} | Confidence: {confidence:.3f}",
            f"Shadow eligible: {shadow_eligible}",
            f"Kill switch: {kill.get('active', False)}",
            f"Manual veto: {veto.get('active', False)}",
            f"Bridges: {json.dumps(bridge_summary)}",
            f"Heartbeat: {hb_ts} ({hb_status})",
        ]
        return "\n".join(lines)


def format_scorecard(repo_root: Path, fmt: str = "text") -> str:
    sc = latest_scorecard(repo_root / "logs" / "scorecards")
    if not sc:
        return "No scorecards found."

    mode = sc.get("mode", "UNKNOWN")
    regime_p = sc.get("regime_shift_probability", 0)
    confidence = sc.get("confidence", 0)
    components = sc.get("component_scores", {})
    evidence = sc.get("evidence", [])
    bridge_summary = sc.get("bridge_summary", {})
    tw = sc.get("time_window", {})

    if fmt == "telegram":
        emoji = mode_emoji(mode)
        lines = [
            f"{emoji} <b>Scorecard #{sc.get('cycle', '?')}</b>",
            f"",
            f"Mode: <b>{mode}</b>",
            f"Regime P: <b>{regime_p:.3f}</b> | Confidence: {confidence:.3f}",
            f"Time window: {tw.get('current_window', 'unknown')}",
            f"",
            f"<b>Component Scores:</b>",
        ]
        top = sorted(components.items(), key=lambda x: x[1], reverse=True)
        for k, v in top:
            bar = "█" * int(v * 10) + "░" * (10 - int(v * 10))
            lines.append(f"  {bar} {v:.2f} {k}")
        if evidence:
            lines.append(f"\n<b>Evidence:</b>")
            for e in evidence[:5]:
                lines.append(f"  • {e[:80]}")
        lines.append(f"\nBridges: {json.dumps(bridge_summary)}")
        return "\n".join(lines)
    else:
        lines = [
            f"Scorecard #{sc.get('cycle', '?')} — {mode}",
            f"Regime P: {regime_p:.3f} | Confidence: {confidence:.3f}",
            f"Time window: {tw.get('current_window', 'unknown')}",
            "Components:",
        ]
        for k, v in sorted(components.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {k}: {v:.3f}")
        if evidence:
            lines.append("Evidence:")
            for e in evidence[:5]:
                lines.append(f"  - {e[:100]}")
        return "\n".join(lines)


def format_graduation(repo_root: Path, fmt: str = "text") -> str:
    report_path = repo_root / "reports" / "weekly" / "graduation_assessment.json"
    if not report_path.exists():
        return "No graduation assessment found. Run: python3 scripts/ops/check_graduation_criteria.py"

    report = load_json(report_path)
    summary = report.get("summary", {})
    overall = "ELIGIBLE ✅" if report.get("overall_pass") else "NOT READY ❌"

    if fmt == "telegram":
        lines = [
            f"<b>Graduation Assessment</b>",
            f"Stage: {report.get('stage', 'unknown')}",
            f"Status: <b>{overall}</b>",
            f"Checks: {summary.get('passed', 0)}/{summary.get('total_checks', 0)} passed",
            "",
        ]
        for check in report.get("checks", []):
            icon = "✓" if check["pass"] else ("?" if check.get("insufficient_data") else "✗")
            lines.append(f"  {icon} {check['check']}: {check.get('actual', 'n/a')}")
        return "\n".join(lines)
    else:
        lines = [f"Graduation: {report.get('stage')} — {overall}"]
        lines.append(f"Checks: {summary.get('passed', 0)}/{summary.get('total_checks', 0)}")
        for check in report.get("checks", []):
            status = "PASS" if check["pass"] else "FAIL"
            lines.append(f"  [{status}] {check['check']}: {check.get('actual', 'n/a')}")
        return "\n".join(lines)


COMMANDS = {
    "status": format_status,
    "scorecard": format_scorecard,
    "graduation": format_graduation,
}


def main():
    p = argparse.ArgumentParser(description="Global Sentinel Status Reporter")
    p.add_argument("--repo-root", default="/opt/global-sentinel")
    p.add_argument("--command", default="status", choices=list(COMMANDS.keys()))
    p.add_argument("--format", default="text", choices=["text", "telegram", "json"])
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    formatter = COMMANDS[args.command]
    output = formatter(repo_root, fmt=args.format)
    print(output)


if __name__ == "__main__":
    main()
