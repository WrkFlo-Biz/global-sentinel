#!/usr/bin/env python3
"""
Global Sentinel V4 — Manual Veto / Kill Switch MCP (Content-Length framed)

Implements MCP protocol with JSON-RPC over stdin/stdout using Content-Length framing.

Tools exposed:
- get_control_flags
- set_manual_veto
- set_kill_switch
- clear_all_flags
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.control_state_snapshot import read_control_state_snapshot
from src.core.orchestrator_control_guidance import (
    orchestrator_approval_command as build_orchestrator_approval_command,
)

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[2]))
CONTROL_DIR = REPO_ROOT / "control"
CONTROL_DIR.mkdir(parents=True, exist_ok=True)

MANUAL_VETO_PATH = CONTROL_DIR / "manual_veto.json"
KILL_SWITCH_PATH = CONTROL_DIR / "kill_switch.json"
APPROVAL_REQUIRED_ERROR = "orchestrator_approval_required"
APPROVAL_REQUIRED_MESSAGE = (
    "This MCP mutator is demoted. Route Tier-2 control changes through "
    "orchestrator approval tokens instead of writing local control files."
)


def read_message() -> Optional[Dict[str, Any]]:
    """Read a Content-Length framed JSON-RPC message from stdin."""
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.decode("utf-8", errors="replace")
        if line in ("\r\n", "\n", ""):
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    if "content-length" not in headers:
        return None
    try:
        length = int(headers["content-length"])
    except ValueError:
        return None

    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def write_message(msg: Dict[str, Any]) -> None:
    """Write a Content-Length framed JSON-RPC message to stdout."""
    raw = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def get_flags() -> Dict[str, Any]:
    control_snapshot = read_control_state_snapshot(REPO_ROOT)
    veto = _read_json(MANUAL_VETO_PATH, {"manual_veto": False, "set_at": None})
    kill = _read_json(KILL_SWITCH_PATH, {"kill_switch": False, "set_at": None})
    return {
        "manual_veto": control_snapshot["manual_veto"],
        "kill_switch": control_snapshot["kill_switch"],
        "manual_veto_updated_at": veto.get("set_at"),
        "kill_switch_updated_at": kill.get("set_at"),
        "control_dir": str(CONTROL_DIR),
    }


def _approval_command(kind: str, target: str) -> str:
    return build_orchestrator_approval_command(
        kind,
        target,
        include_reason_placeholder=True,
    )


def _approval_guidance_payload(
    *,
    tool_name: str,
    requested_change: Dict[str, Any] | None = None,
    commands: list[str],
    target: str = "",
    kind: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": False,
        "status": "approval_required",
        "error": APPROVAL_REQUIRED_ERROR,
        "message": APPROVAL_REQUIRED_MESSAGE,
        "tool": tool_name,
        "commands": commands,
    }
    if requested_change is not None:
        payload["requested_change"] = requested_change
    if kind:
        payload["kind"] = kind
    if target:
        payload["target"] = target
    return payload


def set_manual_veto(enabled: bool, reason: Optional[str] = None, set_by: str = "human") -> Dict[str, Any]:
    del set_by
    target = f"global-sentinel/control/manual-veto/{'on' if enabled else 'off'}"
    kind = "gs.control.manual_veto.set"
    return _approval_guidance_payload(
        tool_name="set_manual_veto",
        requested_change={"manual_veto": bool(enabled), "reason": reason or ""},
        commands=[_approval_command(kind, target)],
        kind=kind,
        target=target,
    )


def set_kill_switch(enabled: bool, reason: Optional[str] = None, set_by: str = "human") -> Dict[str, Any]:
    del set_by
    target = f"global-sentinel/control/kill-switch/{'on' if enabled else 'off'}"
    kind = "gs.control.kill_switch.set"
    return _approval_guidance_payload(
        tool_name="set_kill_switch",
        requested_change={"kill_switch": bool(enabled), "reason": reason or ""},
        commands=[_approval_command(kind, target)],
        kind=kind,
        target=target,
    )


def clear_all_flags() -> Dict[str, Any]:
    return _approval_guidance_payload(
        tool_name="clear_all_flags",
        requested_change={"manual_veto": False, "kill_switch": False},
        commands=[
            _approval_command(
                "gs.control.manual_veto.set",
                "global-sentinel/control/manual-veto/off",
            ),
            _approval_command(
                "gs.control.kill_switch.set",
                "global-sentinel/control/kill-switch/off",
            ),
        ],
    )


TOOLS = [
    {
        "name": "get_control_flags",
        "description": "Read manual_veto and kill_switch flags for Global Sentinel.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set_manual_veto",
        "description": (
            "Demoted mutator. Returns orchestrator approval guidance for "
            "manual_veto changes instead of writing local control files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_kill_switch",
        "description": (
            "Demoted mutator. Returns orchestrator approval guidance for "
            "kill_switch changes instead of writing local control files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
    },
    {
        "name": "clear_all_flags",
        "description": (
            "Demoted mutator. Returns orchestrator approval guidance for "
            "clearing manual_veto and kill_switch instead of writing local "
            "control files."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def tool_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "structuredContent": payload,
    }


def handle_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "global-sentinel-manual-veto-mcp", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        try:
            if name == "get_control_flags":
                payload = get_flags()
            elif name == "set_manual_veto":
                payload = set_manual_veto(bool(arguments["enabled"]), arguments.get("reason"))
            elif name == "set_kill_switch":
                payload = set_kill_switch(bool(arguments["enabled"]), arguments.get("reason"))
            elif name == "clear_all_flags":
                payload = clear_all_flags()
            else:
                raise ValueError(f"Unknown tool: {name}")
            return {"jsonrpc": "2.0", "id": req_id, "result": tool_result(payload)}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


def main() -> None:
    while True:
        msg = read_message()
        if msg is None:
            break
        resp = handle_request(msg)
        if resp is not None:
            write_message(resp)


if __name__ == "__main__":
    main()
