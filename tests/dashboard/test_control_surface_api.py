from __future__ import annotations

import asyncio
import json

from dashboard.api import server


class _DummyRequest:
    pass


def test_telegram_approve_endpoint_is_disabled_and_writes_no_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    response = asyncio.run(server.telegram_approve(_DummyRequest()))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 410
    assert payload["error"] == "legacy_approval_file_bridge_disabled"
    assert "scoped GS trade ticket" in payload["message"]
    assert payload["orchestrator_command"] == server.ORCHESTRATOR_APPROVAL_COMMAND
    assert "gs.trade.execute_shadow" in payload["orchestrator_command"]
    assert "global-sentinel/trade-ticket/<ticket_id>" in payload["orchestrator_command"]
    assert not (tmp_path / "control" / "pending_approval_day_trade.json").exists()
    assert not (tmp_path / "control" / "pending_approval_medium_long.json").exists()
