from __future__ import annotations

import sys

import pytest

from scripts.ops import gs_control


@pytest.mark.parametrize(
    ("argv", "expected_kind", "expected_target"),
    [
        (
            ["kill", "--reason", "emergency"],
            "gs.control.kill_switch.set",
            "global-sentinel/control/kill-switch/on",
        ),
        (
            ["unkill"],
            "gs.control.kill_switch.set",
            "global-sentinel/control/kill-switch/off",
        ),
        (
            ["veto", "--reason", "review needed"],
            "gs.control.manual_veto.set",
            "global-sentinel/control/manual-veto/on",
        ),
        (
            ["unveto"],
            "gs.control.manual_veto.set",
            "global-sentinel/control/manual-veto/off",
        ),
        (
            ["mode", "manual", "--strategy", "day_trade"],
            "gs.control.execution_mode.set",
            "global-sentinel/control/execution-mode/day_trade/manual",
        ),
    ],
)
def test_mutating_commands_return_scoped_approval_guidance(argv, expected_kind, expected_target, monkeypatch, capsys):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("mutating commands must not call local POST endpoints")

    def fail_get(*_args, **_kwargs):
        raise AssertionError("mutating guidance should not require local GET endpoints")

    monkeypatch.setattr(gs_control, "api_post", fail_post)
    monkeypatch.setattr(gs_control, "api_get", fail_get)
    monkeypatch.setattr(sys, "argv", ["gs_control.py", *argv])

    gs_control.main()

    output = capsys.readouterr().out.strip()
    assert "orchestrator approval" in output.lower()
    assert f"--kind {expected_kind}" in output
    assert f"--target {expected_target}" in output
    assert '--reason "<reason>"' not in output


def test_status_remains_read_only(monkeypatch, capsys):
    calls = []

    def fake_get(path):
        calls.append(path)
        return {
            "mode": "NORMAL",
            "cycle": 17,
            "regime_p": 0.25,
            "confidence": 0.8,
            "kill_switch": False,
            "manual_veto": False,
            "shadow_eligible": True,
            "fallback_mode": False,
            "execution_mode": {"day_trade": "manual", "medium_long": "auto"},
            "evidence": ["spread compression"],
        }

    def fail_post(*_args, **_kwargs):
        raise AssertionError("read-only status must not call local POST endpoints")

    monkeypatch.setattr(gs_control, "api_get", fake_get)
    monkeypatch.setattr(gs_control, "api_post", fail_post)
    monkeypatch.setattr(sys, "argv", ["gs_control.py", "status"])

    gs_control.main()

    output = capsys.readouterr().out
    assert calls == ["/api/control/status"]
    assert "MODE: NORMAL  |  Cycle #17" in output
    assert "Execution: day_trade=manual  medium_long=auto" in output
    assert "spread compression" in output


def test_refresh_stays_read_only(monkeypatch, capsys):
    calls = []

    def fake_get(path):
        calls.append(path)
        return {}

    def fail_post(*_args, **_kwargs):
        raise AssertionError("refresh must not call local POST endpoints")

    monkeypatch.setattr(gs_control, "api_get", fake_get)
    monkeypatch.setattr(gs_control, "api_post", fail_post)
    monkeypatch.setattr(sys, "argv", ["gs_control.py", "refresh"])

    gs_control.main()

    output = capsys.readouterr().out.strip()
    assert calls == [
        "/api/consciousness",
        "/api/politician-alpha",
        "/api/scorecard/latest",
    ]
    assert output == "Refresh triggered — consciousness, politician alpha, and scorecard endpoints polled"
