from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.control_state_snapshot import (
    read_control_metadata_snapshot,
    read_control_state_snapshot,
    read_control_wrapper_snapshot,
)


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


def test_read_control_metadata_snapshot_reads_updated_at_fields_and_control_dir(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "control" / "manual_veto.json",
        {"manual_veto": True, "set_at": "2026-04-25T00:00:00Z"},
    )
    _write_json(
        tmp_path / "control" / "kill_switch.json",
        {"kill_switch": False, "set_at": "2026-04-25T00:05:00Z"},
    )

    assert read_control_metadata_snapshot(tmp_path) == {
        "manual_veto_updated_at": "2026-04-25T00:00:00Z",
        "kill_switch_updated_at": "2026-04-25T00:05:00Z",
        "control_dir": str(tmp_path / "control"),
    }


def test_read_control_metadata_snapshot_defaults_none_for_missing_invalid_or_non_string_values(
    tmp_path: Path,
) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "manual_veto.json").write_text(
        json.dumps({"manual_veto": True, "set_at": 123}),
        encoding="utf-8",
    )
    (control_dir / "kill_switch.json").write_text("{bad-json", encoding="utf-8")

    assert read_control_metadata_snapshot(tmp_path) == {
        "manual_veto_updated_at": None,
        "kill_switch_updated_at": None,
        "control_dir": str(control_dir),
    }


def test_read_control_wrapper_snapshot_preserves_metadata_and_normalizes_booleans(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "control" / "kill_switch.json",
        {
            "kill_switch": False,
            "active": True,
            "reason": "operator override",
            "set_at": "2026-04-25T11:00:00Z",
        },
    )
    _write_json(
        tmp_path / "control" / "manual_veto.json",
        {
            "active": True,
            "reason": "manual review",
            "set_at": "2026-04-25T11:05:00Z",
        },
    )

    assert read_control_wrapper_snapshot(tmp_path) == {
        "kill_switch": {
            "kill_switch": False,
            "active": False,
            "reason": "operator override",
            "set_at": "2026-04-25T11:00:00Z",
        },
        "manual_veto": {
            "manual_veto": True,
            "active": True,
            "reason": "manual review",
            "set_at": "2026-04-25T11:05:00Z",
        },
    }


def test_read_control_wrapper_snapshot_defaults_to_boolean_false_when_payloads_are_missing_or_invalid(
    tmp_path: Path,
) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "manual_veto.json").write_text("{bad-json", encoding="utf-8")
    (control_dir / "kill_switch.json").write_text("[]", encoding="utf-8")

    assert read_control_wrapper_snapshot(tmp_path) == {
        "kill_switch": {
            "kill_switch": False,
            "active": False,
        },
        "manual_veto": {
            "manual_veto": False,
            "active": False,
        },
    }
