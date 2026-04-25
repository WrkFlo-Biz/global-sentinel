from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "healthcheck.py"
SPEC = importlib.util.spec_from_file_location("healthcheck_module", MODULE_PATH)
healthcheck = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(healthcheck)


def test_check_controls_uses_shared_snapshot_file_semantics(tmp_path, monkeypatch) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True)
    (control_dir / "manual_veto.json").write_text('{"manual_veto": true}', encoding="utf-8")
    (control_dir / "kill_switch.json").write_text("{bad-json", encoding="utf-8")
    monkeypatch.setattr(healthcheck, "PROJECT_ROOT", tmp_path)

    result = healthcheck.check_controls()

    assert result == {
        "status": "alert",
        "alerts": ["MANUAL VETO ACTIVE"],
    }


def test_check_controls_reports_ok_when_snapshot_is_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(healthcheck, "PROJECT_ROOT", tmp_path)

    result = healthcheck.check_controls()

    assert result == {
        "status": "ok",
        "alerts": [],
    }
