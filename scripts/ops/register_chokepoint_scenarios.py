#!/usr/bin/env python3
"""Register the live chokepoint scenarios into local crisis research configs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.research.training.chokepoint_scenarios import (  # noqa: E402
    CHOKEPOINTS,
    CHOKEPOINT_CRISIS_EVENTS,
    COMBINED_SCENARIOS,
    EXECUTION_BOUNDARY,
    build_chokepoint_analog_library,
    merge_chokepoint_analog_library,
)


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def register(repo_root: str | Path = REPO_ROOT) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    definitions_path = repo_root / "config" / "chokepoint_definitions.json"
    playbooks_path = repo_root / "config" / "chokepoint_playbooks.json"
    analog_path = repo_root / "config" / "crisis_analog_library.json"

    definitions_payload = {
        "schema_version": "chokepoint_definitions.v1",
        "chokepoints": CHOKEPOINTS,
        "combined_scenarios": COMBINED_SCENARIOS,
        "execution_metadata": {
            **EXECUTION_BOUNDARY,
            "script": "register_chokepoint_scenarios",
            "refresh_expectation": "every_monitor_cycle",
            "source_type": "live_real_time_bridge_context",
        },
    }
    playbooks_payload = {
        "schema_version": "chokepoint_playbooks.v1",
        "combined_scenarios": COMBINED_SCENARIOS,
        "execution_metadata": {
            **EXECUTION_BOUNDARY,
            "script": "register_chokepoint_scenarios",
            "refresh_expectation": "every_monitor_cycle",
            "source_type": "live_real_time_bridge_context",
        },
    }
    _save_json(definitions_path, definitions_payload)
    _save_json(playbooks_path, playbooks_payload)

    try:
        existing_library = json.loads(analog_path.read_text(encoding="utf-8"))
        if not isinstance(existing_library, list):
            raise ValueError("crisis_analog_library.json is not a list")
    except FileNotFoundError:
        existing_library = []

    before_count = len(existing_library)
    merged_library = merge_chokepoint_analog_library(existing_library)
    added = len(merged_library) - before_count
    _save_json(analog_path, merged_library)

    return {
        "status": "ok",
        "definitions_path": str(definitions_path),
        "playbooks_path": str(playbooks_path),
        "analog_library_path": str(analog_path),
        "existing_analog_count": before_count,
        "added_analog_count": added,
        "total_analog_count": len(merged_library),
        "chokepoint_event_count": len(CHOKEPOINT_CRISIS_EVENTS),
        "registered_event_ids": [
            entry.get("source_event_id")
            for entry in build_chokepoint_analog_library()
        ],
    }


if __name__ == "__main__":
    result = register(os.environ.get("GS_REPO_ROOT", REPO_ROOT))
    print(json.dumps(result, indent=2, default=str))
