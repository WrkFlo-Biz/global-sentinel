from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _control_flag(payload: dict[str, Any], explicit_key: str) -> bool:
    if explicit_key in payload:
        return bool(payload.get(explicit_key))
    if "active" in payload:
        return bool(payload.get("active"))
    return False


def read_control_state_snapshot(repo_root: Path | str) -> dict[str, bool]:
    control_dir = Path(repo_root) / "control"
    manual_veto = _read_json_object(control_dir / "manual_veto.json")
    kill_switch = _read_json_object(control_dir / "kill_switch.json")
    return {
        "manual_veto": _control_flag(manual_veto, "manual_veto"),
        "kill_switch": _control_flag(kill_switch, "kill_switch"),
    }
