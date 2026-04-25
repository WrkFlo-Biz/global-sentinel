from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.control_state_snapshot import read_control_state_snapshot


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_read_control_state_snapshot_reads_explicit_keys_with_precedence(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "control" / "manual_veto.json",
        {"manual_veto": True, "active": False},
    )
    _write_json(
        tmp_path / "control" / "kill_switch.json",
        {"kill_switch": False, "active": True},
    )

    assert read_control_state_snapshot(tmp_path) == {
        "manual_veto": True,
        "kill_switch": False,
    }


def test_read_control_state_snapshot_falls_back_to_legacy_active_key(tmp_path: Path) -> None:
    _write_json(tmp_path / "control" / "manual_veto.json", {"active": True})
    _write_json(tmp_path / "control" / "kill_switch.json", {"active": False})

    assert read_control_state_snapshot(tmp_path) == {
        "manual_veto": True,
        "kill_switch": False,
    }


def test_read_control_state_snapshot_defaults_false_for_missing_or_invalid_payloads(tmp_path: Path) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "manual_veto.json").write_text("{bad-json", encoding="utf-8")
    (control_dir / "kill_switch.json").write_text("[]", encoding="utf-8")

    assert read_control_state_snapshot(tmp_path) == {
        "manual_veto": False,
        "kill_switch": False,
    }
