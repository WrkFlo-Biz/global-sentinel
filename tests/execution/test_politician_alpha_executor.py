from __future__ import annotations

from pathlib import Path

import src.execution.politician_alpha_executor as politician_alpha_executor_module
from src.execution.politician_alpha_executor import PoliticianAlphaExecutor


def test_control_flag_guards_use_shared_control_snapshot(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_snapshot(repo_root: Path) -> dict[str, bool]:
        calls.append(repo_root)
        return {
            "manual_veto": True,
            "kill_switch": False,
        }

    monkeypatch.setattr(
        politician_alpha_executor_module,
        "read_control_state_snapshot",
        fake_snapshot,
    )

    executor = PoliticianAlphaExecutor(tmp_path)

    assert executor._is_kill_switch_active() is False
    assert executor._is_manual_veto_active() is True
    assert calls == [tmp_path, tmp_path]


def test_submit_shadow_order_blocks_on_helper_backed_kill_switch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        politician_alpha_executor_module,
        "read_control_state_snapshot",
        lambda repo_root: {"manual_veto": False, "kill_switch": True},
    )

    executor = PoliticianAlphaExecutor(tmp_path)

    result = executor.submit_shadow_order({"symbol": "NVDA"})

    assert result["status"] == "blocked"
    assert result["reason"] == "kill_switch_active"
    assert result["shadow_only"] is True
    assert result["advisory_only"] is True
