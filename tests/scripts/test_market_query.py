from __future__ import annotations

import scripts.ops.market_query as market_query
from src.core.control_state_snapshot import read_control_state_snapshot


def test_read_control_state_snapshot_defaults_false_for_missing_or_invalid_files(tmp_path) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True)
    (control_dir / "manual_veto.json").write_text("{not-json", encoding="utf-8")
    (control_dir / "kill_switch.json").write_text("[]", encoding="utf-8")

    assert read_control_state_snapshot(tmp_path) == {
        "manual_veto": False,
        "kill_switch": False,
    }


def test_build_operating_context_uses_shared_control_snapshot(monkeypatch) -> None:
    calls = []

    def fake_snapshot(repo_root):
        calls.append(repo_root)
        return {"manual_veto": True, "kill_switch": False}

    monkeypatch.setattr(market_query, "read_control_state_snapshot", fake_snapshot)

    result = market_query.build_operating_context(
        {
            "hmm_regime": {
                "operating_mode": "RISK_OFF",
                "regime_shift_probability": "0.37",
            }
        }
    )

    assert calls == [market_query.REPO_ROOT]
    assert result == {
        "mode": "RISK_OFF",
        "regime_shift_probability": 0.37,
        "manual_veto": True,
        "kill_switch": False,
        "execution_sensitivity": "research_only",
    }
