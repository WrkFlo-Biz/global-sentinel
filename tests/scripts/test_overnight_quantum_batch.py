"""Tests for the overnight quantum batch helper."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "ops" / "overnight_quantum_batch.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("overnight_quantum_batch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["overnight_quantum_batch_test"] = module
    spec.loader.exec_module(module)
    return module


def test_extract_request_from_preoptimization_screening():
    module = _load_module()
    payload = {
        "preoptimization_screening": {
            "optimization_request": {
                "request_id": "abc",
                "candidates": [{"symbol": "SPY", "expected_return": 0.1}],
            }
        }
    }

    request = module.extract_request_from_artifact(payload)

    assert request is not None
    assert request["request_id"] == "abc"
    assert request["candidates"][0]["symbol"] == "SPY"


def test_run_batch_uses_latest_request_and_logs_results(tmp_path, monkeypatch):
    module = _load_module()

    artifact_dir = tmp_path / "reports" / "research" / "comparisons"
    artifact_dir.mkdir(parents=True)
    artifact_dir.joinpath("comparison_abc.json").write_text(
        json.dumps(
            {
                "preoptimization_screening": {
                    "optimization_request": {
                        "request_id": "req-1",
                        "candidates": [
                            {"symbol": "SPY", "expected_return": 0.06, "volatility": 0.2},
                            {"symbol": "TLT", "expected_return": 0.03, "volatility": 0.1},
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    calls = []

    class FakeOrchestrator:
        def run_comparison(self, request, mode="full"):
            calls.append((request, mode))
            return {
                "backends_succeeded": ["qpanda3", "classical_strong"],
                "backends_failed": [],
                "comparison": {
                    "objective_values": {"qpanda3": 1.2, "classical_strong": 1.1},
                    "best_objective_backend": "qpanda3",
                    "quantum_vs_strong_classical_delta": 0.1,
                },
            }

    logged = []

    class FakeTracker:
        def __init__(self, repo_root):
            self.repo_root = Path(repo_root)

        def log_result(self, result):
            logged.append(result)

    monkeypatch.setitem(
        sys.modules,
        "src.research.backends.multi_backend_orchestrator",
        type("FakeMod", (), {"MultiBackendOrchestrator": FakeOrchestrator}),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.research.experiment_tracker",
        type("FakeTrackMod", (), {"ExperimentTracker": FakeTracker}),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    report = module.run_batch(iterations=2, sleep_seconds=1, repo_root=tmp_path, mode="full")

    assert report["status"] == "success"
    assert report["iterations_completed"] == 2
    assert len(report["results"]) == 2
    assert len(calls) == 2
    assert calls[0][0]["request_id"] == "req-1"
    assert calls[0][1] == "full"
    assert len(logged) == 2
    assert Path(report["output_path"]).is_file()


def test_run_batch_skips_when_request_missing(tmp_path):
    module = _load_module()
    artifact_dir = tmp_path / "reports" / "research" / "comparisons"
    artifact_dir.mkdir(parents=True)
    artifact_dir.joinpath("comparison_abc.json").write_text(
        json.dumps({"comparison": {"objective_values": {"qpanda3": 1.0}}}),
        encoding="utf-8",
    )

    report = module.run_batch(iterations=1, sleep_seconds=0, repo_root=tmp_path)

    assert report["status"] == "skipped"
    assert "No reusable request found" in report["reason"]


def test_run_batch_uses_most_recent_artifact_with_request(tmp_path, monkeypatch):
    module = _load_module()
    artifact_dir = tmp_path / "reports" / "research" / "comparisons"
    artifact_dir.mkdir(parents=True)
    artifact_dir.joinpath("comparison_001.json").write_text(
        json.dumps(
            {
                "preoptimization_screening": {
                    "optimization_request": {
                        "request_id": "usable",
                        "candidates": [{"symbol": "GLD", "expected_return": 0.03}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    artifact_dir.joinpath("comparison_999.json").write_text(
        json.dumps({"comparison": {"objective_values": {"qpanda3": 1.0}}}),
        encoding="utf-8",
    )

    seen = []

    class FakeOrchestrator:
        def run_comparison(self, request, mode="full"):
            seen.append(request["request_id"])
            return {
                "backends_succeeded": ["qpanda3"],
                "backends_failed": [],
                "comparison": {"objective_values": {"qpanda3": 1.0}},
            }

    class FakeTracker:
        def __init__(self, repo_root):
            self.repo_root = repo_root

        def log_result(self, result):
            return None

    monkeypatch.setitem(
        sys.modules,
        "src.research.backends.multi_backend_orchestrator",
        type("FakeMod", (), {"MultiBackendOrchestrator": FakeOrchestrator}),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.research.experiment_tracker",
        type("FakeTrackMod", (), {"ExperimentTracker": FakeTracker}),
    )

    report = module.run_batch(iterations=1, sleep_seconds=0, repo_root=tmp_path)

    assert report["status"] == "success"
    assert seen == ["usable"]
