from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import src.core.control_state_snapshot  # Preload repo-local helper before script path rewrites.

MODULE_PATH = REPO_ROOT / "scripts" / "ops" / "iran_war_intelligence.py"
SPEC = importlib.util.spec_from_file_location("iran_war_intelligence_module", MODULE_PATH)
iran_war_intelligence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(iran_war_intelligence)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_check_kill_switch_prefers_explicit_key_over_legacy_active(
    tmp_path: Path, monkeypatch
) -> None:
    _write_json(
        tmp_path / "control" / "kill_switch.json",
        {"kill_switch": False, "active": True},
    )
    monkeypatch.setattr(iran_war_intelligence, "REPO_ROOT", tmp_path)

    assert iran_war_intelligence.check_kill_switch() is False


def test_check_kill_switch_falls_back_to_legacy_active_key(
    tmp_path: Path, monkeypatch
) -> None:
    _write_json(tmp_path / "control" / "kill_switch.json", {"active": True})
    monkeypatch.setattr(iran_war_intelligence, "REPO_ROOT", tmp_path)

    assert iran_war_intelligence.check_kill_switch() is True


def test_check_kill_switch_defaults_false_when_file_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(iran_war_intelligence, "REPO_ROOT", tmp_path)

    assert iran_war_intelligence.check_kill_switch() is False
