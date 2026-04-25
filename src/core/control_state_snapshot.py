from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _control_payloads(repo_root: Path | str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    control_dir = Path(repo_root) / "control"
    return (
        control_dir,
        _read_json_object(control_dir / "manual_veto.json"),
        _read_json_object(control_dir / "kill_switch.json"),
    )


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


def _control_updated_at(payload: dict[str, Any]) -> str | None:
    updated_at = payload.get("set_at")
    return updated_at if isinstance(updated_at, str) else None


def read_control_state_snapshot(repo_root: Path | str) -> dict[str, bool]:
    _, manual_veto, kill_switch = _control_payloads(repo_root)
    return {
        "manual_veto": _control_flag(manual_veto, "manual_veto"),
        "kill_switch": _control_flag(kill_switch, "kill_switch"),
    }


def read_control_metadata_snapshot(repo_root: Path | str) -> dict[str, str | None]:
    control_dir, manual_veto, kill_switch = _control_payloads(repo_root)
    return {
        "manual_veto_updated_at": _control_updated_at(manual_veto),
        "kill_switch_updated_at": _control_updated_at(kill_switch),
        "control_dir": str(control_dir),
    }
