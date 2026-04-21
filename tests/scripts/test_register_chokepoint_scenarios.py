from __future__ import annotations

import json
from pathlib import Path

from scripts.ops.register_chokepoint_scenarios import register


def test_register_writes_configs_and_merges_analogs(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    analog_path = config_dir / "crisis_analog_library.json"
    analog_path.write_text(
        json.dumps(
            [
                {
                    "label": "Existing analog",
                    "tags": ["legacy"],
                    "regime_markers": {"legacy": 1.0},
                    "asset_impacts": {},
                    "source_event_id": "legacy_analog",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = register(tmp_path)

    assert result["status"] == "ok"
    assert result["existing_analog_count"] == 1
    assert result["added_analog_count"] == 3
    assert result["total_analog_count"] == 4
    assert (config_dir / "chokepoint_definitions.json").exists()
    assert (config_dir / "chokepoint_playbooks.json").exists()
    definitions = json.loads((config_dir / "chokepoint_definitions.json").read_text(encoding="utf-8"))
    assert definitions["execution_metadata"]["informational_only"] is True
    assert definitions["execution_metadata"]["execution_influence_forbidden"] is True

    saved = json.loads(analog_path.read_text(encoding="utf-8"))
    assert len(saved) == 4
    assert {entry.get("source_event_id") for entry in saved} >= {
        "legacy_analog",
        "hormuz_blockade_2026",
        "dual_chokepoint_2026",
        "triple_chokepoint_2026",
    }
