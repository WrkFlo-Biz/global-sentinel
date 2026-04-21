"""Tests for the full-source trainer helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.research.training.full_source_trainer import FullSourceTrainer


def test_cached_alias_lookup_finds_nested_scorecard_source(tmp_path):
    scorecard_dir = tmp_path / "logs" / "scorecards"
    scorecard_dir.mkdir(parents=True)
    payload = {
        "bridge_results": {
            "options_greeks_bridge": {
                "put_call_ratio": 1.4,
                "gamma_squeeze_risk": 0.72,
            }
        }
    }
    (scorecard_dir / "scorecard_20260308_000001.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    trainer = FullSourceTrainer(repo_root=tmp_path, attempt_live_fetch=False)
    data = trainer._load_cached_source_data(["options_greeks_bridge", "options_greeks"])

    assert isinstance(data, dict)
    assert data["put_call_ratio"] == 1.4


def test_full_source_trainer_builds_manifest_without_live_fetch(tmp_path):
    scorecard_dir = tmp_path / "logs" / "scorecards"
    scorecard_dir.mkdir(parents=True)
    (scorecard_dir / "scorecard_20260308_000001.json").write_text(
        json.dumps(
            {
                "bridge_results": {
                    "fred_bridge": {"DFF": 4.5, "T10Y2Y": -0.35},
                    "gdelt_bridge": {"max_severity": 0.7, "event_count": 12},
                    "options_greeks_bridge": {"put_call_ratio": 1.3, "gamma_squeeze_risk": 0.6},
                }
            }
        ),
        encoding="utf-8",
    )

    trainer = FullSourceTrainer(repo_root=tmp_path, attempt_live_fetch=False)
    report = trainer.run_full_training()

    assert report["features_extracted"] > 0
    manifest_path = tmp_path / "config" / "training_feature_manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["feature_count"] == report["features_extracted"]
