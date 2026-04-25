from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import server as root_server
from dashboard.api import server as dashboard_server


class _DummyRequest:
    pass


class _JsonRequest:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    async def json(self) -> dict[str, object]:
        return self._payload


class _RecordingWebSocket:
    def __init__(
        self,
        *,
        disconnect_after_send_count: int | None = None,
        disconnect_exception=None,
        receive_messages: list[dict[str, object]] | None = None,
    ):
        self.sent: list[dict[str, object]] = []
        self._disconnect_after_send_count = disconnect_after_send_count
        self._disconnect_exception = disconnect_exception
        self._receive_messages = list(receive_messages or [])

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)
        if (
            self._disconnect_after_send_count is not None
            and len(self.sent) >= self._disconnect_after_send_count
            and self._disconnect_exception is not None
        ):
            raise self._disconnect_exception()

    async def receive(self) -> dict[str, object]:
        if self._receive_messages:
            return self._receive_messages.pop(0)
        return {"type": "websocket.disconnect"}


class _DummyConnectionManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws) -> None:
        if ws in self.active:
            self.active.remove(ws)


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


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _assert_live_control_status(payload: dict[str, object]) -> None:
    assert isinstance(payload.get("timestamp_utc"), str)
    assert payload["mode"] == "ELEVATED"
    assert payload["cycle"] == 7
    assert payload["kill_switch"] is False
    assert payload["manual_veto"] is True
    assert payload["shadow_eligible"] is True
    assert payload["fallback_mode"] is False
    assert payload["execution_mode"] == {
        "day_trade": "auto",
        "medium_long": "manual",
    }
    assert payload["evidence"] == ["spread widening", "news shock"]


def _assert_compatibility_controls(payload: dict[str, object]) -> None:
    assert payload["kill_switch"] == {
        "kill_switch": False,
        "active": False,
        "reason": "operator override",
        "set_at": "2026-04-25T11:00:00Z",
    }
    assert payload["manual_veto"] == {
        "manual_veto": True,
        "active": True,
        "reason": "manual review",
        "set_at": "2026-04-25T11:05:00Z",
    }


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
def test_control_status_uses_shared_snapshot_booleans_and_preserves_execution_mode_reads(
    tmp_path,
    monkeypatch,
    module,
):
    _write_execution_mode_config(tmp_path)
    _write_json(
        tmp_path / "logs" / "heartbeat.json",
        {
            "mode": "NORMAL",
            "cycle": 5,
            "timestamp_utc": "2026-04-25T11:40:00+00:00",
        },
    )
    _write_json(
        tmp_path / "logs" / "scorecards" / "scorecard_latest.json",
        {
            "mode": "ELEVATED",
            "cycle": 7,
            "regime_shift_probability": 0.42,
            "confidence": 0.77,
            "shadow_execution_eligible": True,
            "fallback_mode_status": False,
            "evidence": ["spread widening", "news shock"],
        },
    )
    _write_json(
        tmp_path / "control" / "kill_switch.json",
        {"kill_switch": False, "active": True},
    )
    _write_json(
        tmp_path / "control" / "manual_veto.json",
        {"active": True},
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    payload = module.control_status()

    assert payload["mode"] == "ELEVATED"
    assert payload["cycle"] == 7
    assert payload["kill_switch"] is False
    assert payload["manual_veto"] is True
    assert payload["execution_mode"] == {
        "day_trade": "auto",
        "medium_long": "manual",
    }
    assert payload["evidence"] == ["spread widening", "news shock"]


@pytest.mark.parametrize(
    "module",
    [dashboard_server, root_server],
    ids=["dashboard", "root"],
)
def test_controls_wrapper_normalizes_booleans_and_preserves_metadata(
    tmp_path,
    monkeypatch,
    module,
):
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
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    payload = module.controls()

    assert payload["kill_switch"] == {
        "kill_switch": False,
        "active": False,
        "reason": "operator override",
        "set_at": "2026-04-25T11:00:00Z",
    }
    assert payload["manual_veto"] == {
        "manual_veto": True,
        "active": True,
        "reason": "manual review",
        "set_at": "2026-04-25T11:05:00Z",
    }


def test_root_websocket_emits_canonical_control_status_alongside_controls_wrapper(
    tmp_path,
    monkeypatch,
):
    _write_execution_mode_config(tmp_path)
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
    heartbeat_sequence = iter(
        [
            {
                "mode": "NORMAL",
                "cycle": 5,
                "timestamp_utc": "2026-04-25T11:40:00+00:00",
            },
            {
                "mode": "CRISIS",
                "cycle": 6,
                "timestamp_utc": "2026-04-25T11:41:00+00:00",
            },
        ]
    )
    scorecard = {
        "mode": "ELEVATED",
        "cycle": 7,
        "regime_shift_probability": 0.42,
        "confidence": 0.77,
        "shadow_execution_eligible": True,
        "fallback_mode_status": False,
        "evidence": ["spread widening", "news shock"],
    }
    heartbeat_path = tmp_path / "logs" / "heartbeat.json"
    real_load_json = root_server.load_json

    def fake_load_json(path: Path):
        if path == heartbeat_path:
            return next(heartbeat_sequence)
        return real_load_json(path)

    async def immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(root_server, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(root_server, "manager", _DummyConnectionManager())
    monkeypatch.setattr(root_server, "load_json", fake_load_json)
    monkeypatch.setattr(root_server, "load_scorecards", lambda limit=1: [scorecard])
    monkeypatch.setattr(root_server.asyncio, "sleep", immediate_sleep)

    websocket = _RecordingWebSocket(
        disconnect_after_send_count=2,
        disconnect_exception=root_server.WebSocketDisconnect,
    )

    asyncio.run(root_server.websocket_endpoint(websocket))

    assert [frame["type"] for frame in websocket.sent] == ["init", "update"]
    for frame in websocket.sent:
        _assert_live_control_status(frame["control_status"])
        _assert_compatibility_controls(frame["controls"])


def test_dashboard_websocket_init_emits_canonical_control_status_alongside_controls_wrapper(
    tmp_path,
    monkeypatch,
):
    _write_execution_mode_config(tmp_path)
    _write_json(
        tmp_path / "logs" / "heartbeat.json",
        {
            "mode": "NORMAL",
            "cycle": 5,
            "timestamp_utc": "2026-04-25T11:40:00+00:00",
        },
    )
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
    scorecard = {
        "mode": "ELEVATED",
        "cycle": 7,
        "regime_shift_probability": 0.42,
        "confidence": 0.77,
        "shadow_execution_eligible": True,
        "fallback_mode_status": False,
        "evidence": ["spread widening", "news shock"],
    }

    monkeypatch.setattr(dashboard_server, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(dashboard_server, "API_KEY", "")
    monkeypatch.setattr(dashboard_server, "manager", _DummyConnectionManager())
    monkeypatch.setattr(dashboard_server, "load_scorecards", lambda limit=1: [scorecard])
    monkeypatch.setattr(
        dashboard_server,
        "_build_portfolio_payload",
        lambda account="all": {"status": "stub"},
    )
    monkeypatch.setattr(
        dashboard_server,
        "_build_portfolio_history_payload",
        lambda period="1D", timeframe="1H", account="all": {"status": "stub"},
    )
    monkeypatch.setattr(dashboard_server, "dashboard_live_state_manager", None)

    websocket = _RecordingWebSocket(receive_messages=[{"type": "websocket.disconnect"}])

    asyncio.run(dashboard_server.websocket_endpoint(websocket))

    assert len(websocket.sent) == 1
    frame = websocket.sent[0]
    assert frame["type"] == "init"
    _assert_live_control_status(frame["control_status"])
    _assert_compatibility_controls(frame["controls"])


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


@pytest.mark.parametrize(
    "module",
    [dashboard_server, root_server],
    ids=["dashboard", "root"],
)
def test_pending_orders_endpoint_no_longer_reads_local_pending_order_files(
    tmp_path, monkeypatch, module
):
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "pending_orders_day_trade.json").write_text(
        json.dumps({"symbol": "NVDA", "qty": 10}),
        encoding="utf-8",
    )
    (control_dir / "pending_orders_medium_long.json").write_text(
        json.dumps({"symbol": "MSFT", "qty": 5}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    response = module.pending_orders()
    payload = _decode_json_response(response)

    assert response.status_code == 200
    assert payload["day_trade"] is None
    assert payload["medium_long"] is None
    assert payload["approval_required"] is True
    assert payload["legacy_approval_file_bridge_disabled"] is True
    assert payload["status"] == "approval_required"
    assert "orchestrator" in payload["message"].lower()
    assert payload["orchestrator_command"] == module.ORCHESTRATOR_APPROVAL_COMMAND
