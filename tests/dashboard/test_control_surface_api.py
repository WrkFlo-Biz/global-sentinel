from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import server as root_server
from dashboard.api import server as dashboard_server


class _DummyRequest:
    pass


class _JsonRequest:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    async def json(self) -> dict[str, object]:
        return self._payload


def _decode_json_response(response) -> dict[str, object]:
    return json.loads(response.body.decode("utf-8"))


def _write_execution_mode_config(repo_root: Path) -> str:
    config_path = repo_root / "config" / "execution_mode.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "strategies:\n"
        "  day_trade: {}\n"
        "  medium_long: {}\n"
        "execution_mode:\n"
        "  day_trade: auto\n"
        "  medium_long: manual\n"
        "bot_permissions:\n"
        "  telegram: read_only\n"
    )
    config_path.write_text(content, encoding="utf-8")
    return content


def _assert_demoted_payload(payload, module, expected_kind: str, expected_target: str) -> None:
    assert payload["ok"] is False
    assert payload["status"] == "approval_required"
    assert payload["error"] == module.APPROVAL_REQUIRED_ERROR
    assert payload["kind"] == expected_kind
    assert payload["target"] == expected_target
    assert payload["orchestrator_command"] == module._approval_command(
        expected_kind,
        expected_target,
    )
    assert "orchestrator approval" in payload["message"].lower()


@pytest.mark.parametrize(
    "module",
    [dashboard_server, root_server],
    ids=["dashboard", "root"],
)
def test_telegram_approve_endpoint_is_disabled_and_writes_no_local_file(tmp_path, monkeypatch, module):
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    response = asyncio.run(module.telegram_approve(_DummyRequest()))
    payload = _decode_json_response(response)

    assert response.status_code == 410
    assert payload["error"] == "legacy_approval_file_bridge_disabled"
    assert "scoped GS trade ticket" in payload["message"]
    assert payload["orchestrator_command"] == module.ORCHESTRATOR_APPROVAL_COMMAND
    assert "gs.trade.execute_shadow" in payload["orchestrator_command"]
    assert "global-sentinel/trade-ticket/<ticket_id>" in payload["orchestrator_command"]
    assert not (tmp_path / "control" / "pending_approval_day_trade.json").exists()
    assert not (tmp_path / "control" / "pending_approval_medium_long.json").exists()


@pytest.mark.parametrize(
    "module",
    [dashboard_server, root_server],
    ids=["dashboard", "root"],
)
def test_get_execution_mode_remains_read_only(tmp_path, monkeypatch, module):
    original = _write_execution_mode_config(tmp_path)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    payload = module.get_execution_mode()

    assert payload["execution_mode"] == {
        "day_trade": "auto",
        "medium_long": "manual",
    }
    assert payload["bot_permissions"] == {"telegram": "read_only"}
    assert (tmp_path / "config" / "execution_mode.yaml").read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "module",
    [dashboard_server, root_server],
    ids=["dashboard", "root"],
)
def test_execution_mode_mutator_is_demoted_without_config_write(tmp_path, monkeypatch, module):
    original = _write_execution_mode_config(tmp_path)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    response = asyncio.run(
        module.set_execution_mode(
            _JsonRequest({"strategy": "day_trade", "mode": "manual"})
        )
    )
    payload = _decode_json_response(response)

    assert response.status_code == 410
    _assert_demoted_payload(
        payload,
        module,
        "gs.control.execution_mode.set",
        "global-sentinel/control/execution-mode/day_trade/manual",
    )
    assert payload["requested_change"] == {"strategy": "day_trade", "mode": "manual"}
    assert (tmp_path / "config" / "execution_mode.yaml").read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("module", "endpoint_name", "request_model", "active", "change_key", "expected_kind", "expected_target"),
    [
        (
            dashboard_server,
            "set_kill_switch",
            "KillSwitchRequest",
            True,
            "kill_switch",
            "gs.control.kill_switch.set",
            "global-sentinel/control/kill-switch/on",
        ),
        (
            dashboard_server,
            "set_veto",
            "VetoRequest",
            False,
            "manual_veto",
            "gs.control.manual_veto.set",
            "global-sentinel/control/manual-veto/off",
        ),
        (
            root_server,
            "set_kill_switch",
            "KillSwitchRequest",
            True,
            "kill_switch",
            "gs.control.kill_switch.set",
            "global-sentinel/control/kill-switch/on",
        ),
        (
            root_server,
            "set_veto",
            "VetoRequest",
            False,
            "manual_veto",
            "gs.control.manual_veto.set",
            "global-sentinel/control/manual-veto/off",
        ),
    ],
    ids=[
        "dashboard-kill-on",
        "dashboard-veto-off",
        "root-kill-on",
        "root-veto-off",
    ],
)
def test_control_mutators_are_demoted_without_control_writes(
    tmp_path,
    monkeypatch,
    module,
    endpoint_name,
    request_model,
    active,
    change_key,
    expected_kind,
    expected_target,
):
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    request = getattr(module, request_model)(active=active, reason="operator request")

    response = getattr(module, endpoint_name)(request)
    payload = _decode_json_response(response)

    assert response.status_code == 410
    _assert_demoted_payload(payload, module, expected_kind, expected_target)
    assert payload["requested_change"] == {
        change_key: active,
        "reason": "operator request",
    }
    assert not (tmp_path / "control" / "kill_switch.json").exists()
    assert not (tmp_path / "control" / "manual_veto.json").exists()


@pytest.mark.parametrize(
    ("endpoint_name", "expected_target", "expected_change"),
    [
        (
            "v6_kill_switch",
            "global-sentinel/control/kill-switch/on",
            {"kill_switch": True, "reason": "Dashboard kill switch activated"},
        ),
        (
            "v6_kill_switch_deactivate",
            "global-sentinel/control/kill-switch/off",
            {"kill_switch": False, "reason": ""},
        ),
    ],
)
def test_dashboard_v6_kill_switch_mirrors_are_demoted_without_control_writes(
    tmp_path,
    monkeypatch,
    endpoint_name,
    expected_target,
    expected_change,
):
    monkeypatch.setattr(dashboard_server, "REPO_ROOT", tmp_path)

    response = getattr(dashboard_server, endpoint_name)()
    payload = _decode_json_response(response)

    assert response.status_code == 410
    _assert_demoted_payload(
        payload,
        dashboard_server,
        "gs.control.kill_switch.set",
        expected_target,
    )
    assert payload["requested_change"] == expected_change
    assert not (tmp_path / "control" / "kill_switch.json").exists()
