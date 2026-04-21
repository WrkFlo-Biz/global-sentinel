"""Tests for rollback telemetry collection."""
from __future__ import annotations

import json
from pathlib import Path

from src.core.rollback_telemetry import RollbackTelemetryCollector
from src.research.rollback_encoder_version import EncoderVersionManager


def test_collects_encoder_and_learning_state_versions(tmp_path: Path):
    repo_root = tmp_path
    encoder_dir = repo_root / "artifacts" / "encoder_versions"
    learning_dir = repo_root / "reports" / "research" / "state" / "versions"
    learning_dir.mkdir(parents=True, exist_ok=True)

    manager = EncoderVersionManager(storage_dir=encoder_dir)
    manager.save_version({"weights": [1, 2]}, "v1")
    manager.save_version({"weights": [2, 3]}, "v2")

    (learning_dir / "20260307T190000Z_hasha.json").write_text(
        json.dumps(
            {
                "timestamp": "20260307T190000Z",
                "content_hash": "hasha",
                "learning_state": {"weights": {"a": 1.0}},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    collector = RollbackTelemetryCollector(repo_root, encoder_dir=encoder_dir, learning_state_dir=learning_dir)
    result = collector.collect()

    assert result.encoder_version_count == 2
    assert result.current_encoder_version == "v2"
    assert result.learning_state_version_count == 1
    assert result.rollback_path_present is True
    assert result.rollback_path_proven is False


def test_marks_rollback_path_proven_when_rollback_evidence_exists(tmp_path: Path):
    repo_root = tmp_path
    encoder_dir = repo_root / "artifacts" / "encoder_versions"
    learning_dir = repo_root / "reports" / "research" / "state" / "versions"
    encoder_dir.mkdir(parents=True, exist_ok=True)
    learning_dir.mkdir(parents=True, exist_ok=True)

    (encoder_dir / "v1.json").write_text(
        json.dumps(
            {
                "schema_version": "encoder_version.v1",
                "version_tag": "v1",
                "timestamp_utc": "2026-03-07T19:00:00+00:00",
                "content_hash": "aaa",
                "encoder_state": {"weights": [1]},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    (encoder_dir / "v2.json").write_text(
        json.dumps(
            {
                "schema_version": "encoder_version.v1",
                "version_tag": "v2",
                "timestamp_utc": "2026-03-07T19:05:00+00:00",
                "content_hash": "bbb",
                "encoder_state": {"weights": [2]},
                "metadata": {"rollback_from": "v1"},
            }
        ),
        encoding="utf-8",
    )
    (encoder_dir / "_current.json").write_text(
        json.dumps({"current_version_tag": "v2"}),
        encoding="utf-8",
    )
    (learning_dir / "20260307T190500Z_hashb.json").write_text(
        json.dumps(
            {
                "timestamp": "20260307T190500Z",
                "content_hash": "hashb",
                "learning_state": {"weights": {"a": 2.0}},
                "metadata": {"rollback_from": "20260307T190000Z_hasha"},
            }
        ),
        encoding="utf-8",
    )

    collector = RollbackTelemetryCollector(repo_root, encoder_dir=encoder_dir, learning_state_dir=learning_dir)
    result = collector.collect()

    assert result.rollback_path_present is True
    assert result.rollback_path_proven is True
    assert result.recent_encoder_rollbacks[0]["rollback_from"] == "v1"
    assert result.recent_learning_state_rollbacks[0]["metadata"]["rollback_from"] == "20260307T190000Z_hasha"
