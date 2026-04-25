from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.risk import manual_veto_mcp


def _bind_control_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control_dir = tmp_path / "control"
    monkeypatch.setattr(manual_veto_mcp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(manual_veto_mcp, "CONTROL_DIR", control_dir)


def _tool_call(name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    response = manual_veto_mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
            },
        }
    )
    assert response is not None
    assert "error" not in response
    return response


def test_get_control_flags_remains_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_control_paths(tmp_path, monkeypatch)
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "manual_veto.json").write_text(
        json.dumps({"manual_veto": True, "set_at": "2026-04-25T00:00:00Z"}),
        encoding="utf-8",
    )
    (control_dir / "kill_switch.json").write_text(
        json.dumps({"kill_switch": False, "set_at": "2026-04-25T00:05:00Z"}),
        encoding="utf-8",
    )

    response = _tool_call("get_control_flags")
    payload = response["result"]["structuredContent"]

    assert payload["manual_veto"] is True
    assert payload["kill_switch"] is False
    assert payload["manual_veto_updated_at"] == "2026-04-25T00:00:00Z"
    assert payload["kill_switch_updated_at"] == "2026-04-25T00:05:00Z"
    assert payload["control_dir"] == str(control_dir)


def test_get_control_flags_uses_shared_snapshot_for_legacy_active_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind_control_paths(tmp_path, monkeypatch)
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "manual_veto.json").write_text(
        json.dumps({"active": True, "set_at": "2026-04-25T00:10:00Z"}),
        encoding="utf-8",
    )
    (control_dir / "kill_switch.json").write_text(
        json.dumps({"active": False, "set_at": "2026-04-25T00:15:00Z"}),
        encoding="utf-8",
    )

    response = _tool_call("get_control_flags")
    payload = response["result"]["structuredContent"]

    assert payload["manual_veto"] is True
    assert payload["kill_switch"] is False
    assert payload["manual_veto_updated_at"] == "2026-04-25T00:10:00Z"
    assert payload["kill_switch_updated_at"] == "2026-04-25T00:15:00Z"
    assert payload["control_dir"] == str(control_dir)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_kind", "expected_target"),
    [
        (
            "set_manual_veto",
            {"enabled": True, "reason": "review needed"},
            "gs.control.manual_veto.set",
            "global-sentinel/control/manual-veto/on",
        ),
        (
            "set_kill_switch",
            {"enabled": False, "reason": "resume operations"},
            "gs.control.kill_switch.set",
            "global-sentinel/control/kill-switch/off",
        ),
    ],
)
def test_mutating_tools_return_demoted_guidance_without_control_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, object],
    expected_kind: str,
    expected_target: str,
) -> None:
    _bind_control_paths(tmp_path, monkeypatch)

    response = _tool_call(tool_name, arguments)
    payload = response["result"]["structuredContent"]
    content_text = response["result"]["content"][0]["text"]

    assert payload["ok"] is False
    assert payload["status"] == "approval_required"
    assert payload["error"] == manual_veto_mcp.APPROVAL_REQUIRED_ERROR
    assert payload["kind"] == expected_kind
    assert payload["target"] == expected_target
    assert payload["commands"] == [
        manual_veto_mcp._approval_command(expected_kind, expected_target)
    ]
    assert '--reason "<reason>"' in payload["commands"][0]
    assert "orchestrator approval" in payload["message"]
    assert expected_target in content_text

    assert not (tmp_path / "control" / "manual_veto.json").exists()
    assert not (tmp_path / "control" / "kill_switch.json").exists()
    assert not (tmp_path / "logs" / "risk_checks").exists()


def test_clear_all_flags_returns_two_scoped_commands_without_control_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind_control_paths(tmp_path, monkeypatch)

    response = _tool_call("clear_all_flags")
    payload = response["result"]["structuredContent"]
    content_text = response["result"]["content"][0]["text"]

    assert payload["ok"] is False
    assert payload["status"] == "approval_required"
    assert payload["error"] == manual_veto_mcp.APPROVAL_REQUIRED_ERROR
    assert payload["requested_change"] == {
        "manual_veto": False,
        "kill_switch": False,
    }
    assert payload["commands"] == [
        manual_veto_mcp._approval_command(
            "gs.control.manual_veto.set",
            "global-sentinel/control/manual-veto/off",
        ),
        manual_veto_mcp._approval_command(
            "gs.control.kill_switch.set",
            "global-sentinel/control/kill-switch/off",
        ),
    ]
    assert all('--reason "<reason>"' in command for command in payload["commands"])
    assert "global-sentinel/control/manual-veto/off" in content_text
    assert "global-sentinel/control/kill-switch/off" in content_text

    assert not (tmp_path / "control" / "manual_veto.json").exists()
    assert not (tmp_path / "control" / "kill_switch.json").exists()
    assert not (tmp_path / "logs" / "risk_checks").exists()
