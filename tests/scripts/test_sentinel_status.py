from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "ops" / "sentinel_status.py"
SPEC = importlib.util.spec_from_file_location("sentinel_status_module", MODULE_PATH)
sentinel_status = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sentinel_status)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_format_status_text_reads_helper_backed_control_flags(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "logs" / "heartbeat.json",
        {
            "mode": "ELEVATED",
            "cycle": 12,
            "timestamp_utc": "2026-04-25T11:00:00+00:00",
            "status": "ok",
        },
    )
    _write_json(
        tmp_path / "logs" / "scorecards" / "scorecard_latest.json",
        {
            "regime_shift_probability": 0.42,
            "confidence": 0.77,
            "bridge_summary": {"alpaca": "healthy"},
            "shadow_execution_eligible": True,
        },
    )
    _write_json(tmp_path / "control" / "kill_switch.json", {"kill_switch": True})
    _write_json(tmp_path / "control" / "manual_veto.json", {"manual_veto": False})

    output = sentinel_status.format_status(tmp_path, fmt="text")

    assert "Mode: ELEVATED (cycle 12)" in output
    assert "Shadow eligible: True" in output
    assert "Kill switch: True" in output
    assert "Manual veto: False" in output
    assert 'Bridges: {"alpaca": "healthy"}' in output


def test_format_status_telegram_reads_helper_backed_control_flags(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "logs" / "heartbeat.json",
        {
            "mode": "NORMAL",
            "cycle": 3,
            "timestamp_utc": "2026-04-25T11:05:00+00:00",
            "status": "ok",
        },
    )
    _write_json(
        tmp_path / "logs" / "scorecards" / "scorecard_latest.json",
        {
            "regime_shift_probability": 0.11,
            "confidence": 0.64,
            "bridge_summary": {"ibkr": "connected"},
            "shadow_execution_eligible": False,
        },
    )
    _write_json(tmp_path / "control" / "manual_veto.json", {"manual_veto": True})
    (tmp_path / "control" / "kill_switch.json").write_text("{bad-json", encoding="utf-8")

    output = sentinel_status.format_status(tmp_path, fmt="telegram")

    assert "Mode: <b>NORMAL</b> | Cycle: 3" in output
    assert "Kill switch: ✅ Off" in output
    assert "Manual veto: 🟠 ACTIVE" in output
    assert "  • ibkr: connected" in output
