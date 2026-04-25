#!/usr/bin/env python3
"""
Global Sentinel — Remote Control CLI

Simple CLI for OpenClaw bots to control Global Sentinel via the dashboard API.
Designed for use via `az vm run-command` or direct SSH execution.

Usage:
    python3 gs_control.py status
    python3 gs_control.py portfolio
    python3 gs_control.py gss
    python3 gs_control.py kill [--reason "emergency"]
    python3 gs_control.py unkill
    python3 gs_control.py veto [--reason "review needed"]
    python3 gs_control.py unveto
    python3 gs_control.py mode auto|manual [--strategy day_trade|medium_long]
    python3 gs_control.py alerts [--limit 10]
    python3 gs_control.py orders [--limit 10]
    python3 gs_control.py scorecard
    python3 gs_control.py refresh
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

API_BASE = "http://localhost:8501"


def api_get(path):
    try:
        req = urllib.request.Request(f"{API_BASE}{path}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def api_post(path, data):
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{API_BASE}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def orchestrator_approval_command(kind: str, target: str) -> str:
    return f"wrkflo-orchestrator approve --kind {kind} --target {target}"


def orchestrator_approval_guidance(kind: str, target: str) -> str:
    return (
        "This command now requires orchestrator approval. Use: "
        f"{orchestrator_approval_command(kind, target)}"
    )


def fmt_status(d):
    if "error" in d:
        return f"ERROR: {d['error']}"
    lines = [
        f"MODE: {d.get('mode', '?')}  |  Cycle #{d.get('cycle', 0)}",
        f"Regime P: {d.get('regime_p', 0):.1%}  |  Confidence: {d.get('confidence', 0):.1%}",
        f"Kill Switch: {'ON' if d.get('kill_switch') else 'OFF'}  |  Veto: {'ON' if d.get('manual_veto') else 'OFF'}",
        f"Shadow Eligible: {'Yes' if d.get('shadow_eligible') else 'No'}  |  Fallback: {'Yes' if d.get('fallback_mode') else 'No'}",
    ]
    exec_mode = d.get("execution_mode", {})
    if exec_mode:
        lines.append(f"Execution: day_trade={exec_mode.get('day_trade', '?')}  medium_long={exec_mode.get('medium_long', '?')}")
    evidence = d.get("evidence", [])
    if evidence:
        lines.append("Evidence:")
        for e in evidence[:5]:
            lines.append(f"  - {e}")
    return "\n".join(lines)


def fmt_portfolio(d):
    if "error" in d:
        return f"ERROR: {d['error']}"
    lines = [
        f"Equity: ${float(d.get('equity', 0)):,.2f}  |  Cash: ${float(d.get('cash', 0)):,.2f}",
        f"Buying Power: ${float(d.get('buying_power', 0)):,.2f}",
    ]
    positions = d.get("positions", [])
    if positions:
        lines.append(f"\nPositions ({len(positions)}):")
        for p in positions:
            pnl = float(p.get("pnl", 0))
            pnl_pct = float(p.get("pnl_pct", 0)) * 100
            symbol = p.get("symbol", "?")
            qty = p.get("qty", 0)
            sign = "+" if pnl >= 0 else ""
            lines.append(f"  {symbol:6s} x{qty}  {sign}${pnl:,.2f} ({sign}{pnl_pct:.1f}%)")
    else:
        lines.append("No open positions")
    return "\n".join(lines)


def fmt_gss(d):
    if "error" in d:
        return f"ERROR: {d['error']}"
    signal = d.get("signal", "UNKNOWN")
    conf = d.get("confidence", 0)
    action = d.get("action", "?")
    reason = d.get("reason", "")
    return f"GSS Signal: {signal}\nAction: {action}  |  Confidence: {conf:.0%}\nReason: {reason}"


def fmt_scorecard(d):
    if "error" in d:
        return f"ERROR: {d['error']}"
    lines = [
        f"Mode: {d.get('mode', '?')}  |  Cycle #{d.get('cycle', 0)}",
        f"Regime P: {d.get('regime_shift_probability', 0):.1%}  |  Confidence: {d.get('confidence', 0):.1%}",
    ]
    cs = d.get("component_scores", {})
    if cs:
        lines.append("Components:")
        for k, v in cs.items():
            lines.append(f"  {k}: {v:.0%}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Global Sentinel Remote Control")
    parser.add_argument("command", choices=[
        "status", "portfolio", "gss", "kill", "unkill",
        "veto", "unveto", "mode", "alerts", "orders",
        "scorecard", "refresh",
    ])
    parser.add_argument("--reason", default="", help="Reason for kill/veto")
    parser.add_argument("--strategy", default="", help="Strategy for mode change")
    parser.add_argument("--limit", type=int, default=10, help="Limit for alerts/orders")
    parser.add_argument("args", nargs="*", help="Additional args (e.g., auto/manual for mode)")

    args = parser.parse_args()
    cmd = args.command

    if cmd == "status":
        print(fmt_status(api_get("/api/control/status")))

    elif cmd == "portfolio":
        print(fmt_portfolio(api_get("/api/control/portfolio-summary")))

    elif cmd == "gss":
        print(fmt_gss(api_get("/api/control/gss-signal")))

    elif cmd == "kill":
        print(
            orchestrator_approval_guidance(
                "gs.control.kill_switch.set",
                "global-sentinel/control/kill-switch/on",
            )
        )

    elif cmd == "unkill":
        print(
            orchestrator_approval_guidance(
                "gs.control.kill_switch.set",
                "global-sentinel/control/kill-switch/off",
            )
        )

    elif cmd == "veto":
        print(
            orchestrator_approval_guidance(
                "gs.control.manual_veto.set",
                "global-sentinel/control/manual-veto/on",
            )
        )

    elif cmd == "unveto":
        print(
            orchestrator_approval_guidance(
                "gs.control.manual_veto.set",
                "global-sentinel/control/manual-veto/off",
            )
        )

    elif cmd == "mode":
        mode_val = args.args[0] if args.args else "auto"
        strategy = args.strategy or "day_trade"
        print(
            orchestrator_approval_guidance(
                "gs.control.execution_mode.set",
                f"global-sentinel/control/execution-mode/{strategy}/{mode_val}",
            )
        )

    elif cmd == "alerts":
        data = api_get(f"/api/alerts?limit={args.limit}")
        if isinstance(data, list):
            for a in data[:args.limit]:
                ts = a.get("timestamp_utc", "")[:19]
                level = a.get("level", "info")
                title = a.get("title", a.get("event", ""))
                print(f"[{ts}] {level.upper()}: {title}")
        else:
            print(json.dumps(data, indent=2))

    elif cmd == "orders":
        data = api_get(f"/api/execution/orders?limit={args.limit}")
        if isinstance(data, list):
            for o in data[:args.limit]:
                ts = o.get("timestamp_utc", "")[:19]
                event = o.get("event_type", "?")
                payload = o.get("payload", {})
                submitted = payload.get("submit_attempt_count", 0)
                print(f"[{ts}] {event}: {submitted} orders")
        else:
            print(json.dumps(data, indent=2))

    elif cmd == "scorecard":
        print(fmt_scorecard(api_get("/api/scorecard/latest")))

    elif cmd == "refresh":
        # Trigger a fresh data fetch by hitting key endpoints
        api_get("/api/consciousness")
        api_get("/api/politician-alpha")
        api_get("/api/scorecard/latest")
        print("Refresh triggered — consciousness, politician alpha, and scorecard endpoints polled")


if __name__ == "__main__":
    main()
