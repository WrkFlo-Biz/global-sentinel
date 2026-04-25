from __future__ import annotations

import json

import scripts.ops.daily_thesis_generator as daily_thesis_generator


def test_build_operating_context_uses_shared_control_snapshot(monkeypatch) -> None:
    calls = []

    def fake_snapshot(repo_root):
        calls.append(repo_root)
        return {"manual_veto": False, "kill_switch": True}

    monkeypatch.setattr(daily_thesis_generator, "read_control_state_snapshot", fake_snapshot)

    result = daily_thesis_generator.build_operating_context(
        {
            "latest_signal": {
                "mode": "DEFENSIVE",
                "regime_shift_probability": "0.82",
            }
        }
    )

    assert calls == [daily_thesis_generator.REPO_ROOT]
    assert result == {
        "mode": "DEFENSIVE",
        "regime_shift_probability": 0.82,
        "manual_veto": False,
        "kill_switch": True,
        "execution_sensitivity": "research_only",
    }


def test_build_operating_context_preserves_file_backed_flag_semantics(tmp_path, monkeypatch) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True)
    (control_dir / "manual_veto.json").write_text(json.dumps({"manual_veto": True}), encoding="utf-8")
    (control_dir / "kill_switch.json").write_text("{bad-json", encoding="utf-8")
    monkeypatch.setattr(daily_thesis_generator, "REPO_ROOT", tmp_path)

    result = daily_thesis_generator.build_operating_context(
        {
            "latest_signal": {
                "mode": "NORMAL",
                "regime_shift_probability": "0.21",
            }
        }
    )

    assert result == {
        "mode": "NORMAL",
        "regime_shift_probability": 0.21,
        "manual_veto": True,
        "kill_switch": False,
        "execution_sensitivity": "research_only",
    }
