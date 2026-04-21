#!/usr/bin/env python3
"""Run crisis-pattern training over the historical disruption dataset."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.research.training.crisis_training_dataset import (  # noqa: E402
    CRISIS_EVENTS,
    CRISIS_PLAYBOOKS,
    build_analog_library,
    dataset_summary,
    event_to_feature_vector,
)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _save_json(path: Path, payload: Dict[str, Any] | List[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def run_training(repo_root: str | Path = REPO_ROOT) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    results: Dict[str, Any] = {
        "schema_version": "crisis_training_report.v1",
        "training_start": _timestamp(),
        "events_processed": 0,
        "categories": {},
        "playbooks_loaded": len(CRISIS_PLAYBOOKS),
        "steps": {},
        "execution_metadata": {
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "script": "run_crisis_training",
        },
    }

    results.update(dataset_summary())

    analog_library = build_analog_library(CRISIS_EVENTS)
    analog_path = repo_root / "config" / "crisis_analog_library.json"
    try:
        from src.research.historical_analog_engine import HistoricalAnalogEngine

        engine = HistoricalAnalogEngine()
        engine.library = list(engine.library) + analog_library
        engine.save_library(analog_path)
        results["steps"]["analog_engine"] = {
            "status": "ok",
            "analog_count_added": len(analog_library),
            "analog_library_path": str(analog_path),
        }
    except Exception as exc:
        _save_json(analog_path, analog_library)
        results["steps"]["analog_engine"] = {
            "status": "fallback_saved",
            "analog_count_added": len(analog_library),
            "analog_library_path": str(analog_path),
            "reason": str(exc),
        }

    signatures = [
        event["regime_signature"]
        for event in CRISIS_EVENTS
        if isinstance(event.get("regime_signature"), dict) and event["regime_signature"]
    ]
    results["steps"]["regime_signatures"] = {
        "status": "ok",
        "count": len(signatures),
    }

    crisis_features = [event_to_feature_vector(event) for event in CRISIS_EVENTS]
    results["steps"]["feature_vectors"] = {
        "status": "ok",
        "count": len(crisis_features),
        "dimension": len(crisis_features[0]) if crisis_features else 0,
    }

    training_step: Dict[str, Any]
    try:
        from src.research.backends.pennylane_anomaly_detector import PennyLaneAnomalyDetector

        detector = PennyLaneAnomalyDetector(
            {
                "n_qubits": 4,
                "n_layers": 2,
                "anomaly_threshold": 0.3,
                "weights_path": str(repo_root / "config" / "anomaly_detector_weights.json"),
            }
        )
        train_result = detector.train(crisis_features, epochs=50, learning_rate=0.01)
        crisis_weights_path = repo_root / "config" / "anomaly_detector_crisis_weights.json"
        detector.save_weights(crisis_weights_path)
        training_step = {
            "status": "ok",
            "samples": len(crisis_features),
            "weights_path": str(crisis_weights_path),
            "train_result": train_result,
        }
    except Exception as exc:
        training_step = {
            "status": "skipped",
            "reason": str(exc),
            "samples": len(crisis_features),
        }
    results["steps"]["anomaly_detector_training"] = training_step

    playbooks_path = repo_root / "config" / "crisis_playbooks.json"
    _save_json(playbooks_path, CRISIS_PLAYBOOKS)
    results["steps"]["playbooks"] = {
        "status": "ok",
        "path": str(playbooks_path),
        "count": len(CRISIS_PLAYBOOKS),
    }

    results["events_processed"] = len(CRISIS_EVENTS)
    results["categories"] = dataset_summary()["category_counts"]
    results["training_end"] = _timestamp()

    report_path = repo_root / "reports" / "research" / "crisis_training_report.json"
    _save_json(report_path, results)
    results["report_path"] = str(report_path)
    return results


if __name__ == "__main__":
    report = run_training(os.environ.get("GS_REPO_ROOT", REPO_ROOT))
    print(json.dumps(report, indent=2, default=str))
