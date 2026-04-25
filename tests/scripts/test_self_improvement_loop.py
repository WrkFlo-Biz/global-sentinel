from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.self_improvement_loop as self_improvement_loop_module
from scripts.self_improvement_loop import ImprovementConfig, SelfImprovementLoop


def _checks_by_name(summary: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        check["check"]: check
        for check in summary["checks"]
        if isinstance(check, dict) and "check" in check
    }


def test_control_flags_use_shared_control_snapshot(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_snapshot(repo_root: Path) -> dict[str, bool]:
        calls.append(repo_root)
        return {
            "manual_veto": True,
            "kill_switch": False,
        }

    monkeypatch.setattr(
        self_improvement_loop_module,
        "read_control_state_snapshot",
        fake_snapshot,
    )

    loop = SelfImprovementLoop(tmp_path, ImprovementConfig())

    assert loop.control_flags() == {
        "manual_veto": True,
        "kill_switch": False,
    }
    assert calls == [tmp_path]


def test_threshold_tuning_proposal_respects_helper_backed_control_flags(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        self_improvement_loop_module,
        "read_control_state_snapshot",
        lambda repo_root: {"manual_veto": False, "kill_switch": True},
    )

    loop = SelfImprovementLoop(tmp_path, ImprovementConfig())
    monkeypatch.setattr(loop, "current_mode", lambda: "NORMAL")

    replay = {
        "status": "ok",
        "eligible_for_tuning": True,
        "pass_rate": 0.9,
        "avg_confidence": 0.8,
        "penalty_pattern_counts": {},
        "correlation_break_count": 0,
    }
    drift = {"break_detected": False}

    assert loop.threshold_tuning_proposal(replay, drift) is None


def test_safety_audit_marks_control_files_as_deployment_diagnostics(tmp_path: Path) -> None:
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    (control_dir / "manual_veto.json").write_text("{}", encoding="utf-8")
    (control_dir / "kill_switch.json").write_text("{}", encoding="utf-8")

    loop = SelfImprovementLoop(tmp_path, ImprovementConfig())
    checks = _checks_by_name(loop.safety_audit())

    for check_name in (
        "manual_veto_file_present_diagnostic",
        "kill_switch_file_present_diagnostic",
    ):
        check = checks[check_name]
        assert check["passed"] is True
        assert check["kind"] == "deployment_diagnostic"
        assert check["authority"] == "read_control_state_snapshot"
        assert "presence only" in str(check["note"])
        assert "control snapshot helper" in str(check["note"])


def test_safety_audit_fails_missing_control_file_diagnostics(tmp_path: Path) -> None:
    loop = SelfImprovementLoop(tmp_path, ImprovementConfig())

    audit = loop.safety_audit()
    checks = _checks_by_name(audit)

    assert audit["passed"] is False
    assert checks["manual_veto_file_present_diagnostic"]["passed"] is False
    assert checks["kill_switch_file_present_diagnostic"]["passed"] is False
