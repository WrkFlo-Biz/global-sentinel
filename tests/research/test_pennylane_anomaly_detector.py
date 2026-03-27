"""Tests for the PennyLane anomaly detector backend."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from src.research.backends import pennylane_anomaly_detector as pad


REPO_ROOT = Path(__file__).resolve().parents[2]


def _stub_detector(monkeypatch, threshold: float = 0.6):
    monkeypatch.setattr(pad, "SKLEARN_AVAILABLE", False)
    detector = pad.PennyLaneAnomalyDetector.__new__(pad.PennyLaneAnomalyDetector)
    detector.config = {}
    detector.n_qubits = 2
    detector.n_layers = 1
    detector.anomaly_threshold = threshold
    detector.trained = True
    detector.weights = object()
    detector._classical_model = None
    detector._weights_path = None
    detector._circuit = lambda weights, x: (float(x[0]) * 2.0) - 1.0
    return detector


def _assert_success_schema(result: dict) -> None:
    assert {
        "backend",
        "algorithm",
        "status",
        "candidate_id",
        "quantum_anomaly_score",
        "quantum_raw_expectation",
        "classical_anomaly_score",
        "is_anomaly_quantum",
        "is_anomaly_classical",
        "anomaly_agreement",
        "execution_metadata",
    }.issubset(result)
    assert result["backend"] == "pennylane_vqc"
    assert result["algorithm"] == "VQC"
    assert result["execution_metadata"]["not_for_direct_execution"] is True
    assert result["execution_metadata"]["quantum_direct_execution_forbidden"] is True
    assert result["execution_metadata"]["bounded_secondary_signal_only"] is True
    assert result["execution_metadata"]["backend"] == "pennylane_vqc"


def test_pennylane_score_candidate_returns_expected_schema(monkeypatch):
    detector = _stub_detector(monkeypatch, threshold=0.6)

    result = detector.score_candidate(
        {"candidate_id": "AAPL", "features": [0.2, 0.1, 0.0, 0.0]}
    )

    _assert_success_schema(result)
    assert result["candidate_id"] == "AAPL"
    assert result["is_anomaly_quantum"] is True


def test_pennylane_weights_roundtrip(monkeypatch, tmp_path: Path):
    detector = _stub_detector(monkeypatch, threshold=0.6)
    detector.weights = [[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]]
    detector.trained = True

    payload = detector.save_weights(tmp_path / "weights.json")

    reloaded = _stub_detector(monkeypatch, threshold=0.1)
    reloaded.weights = None
    reloaded.trained = False
    assert reloaded.load_weights(tmp_path / "weights.json") is True
    assert payload["schema_version"] == "pennylane_anomaly_weights.v1"
    assert reloaded.trained is True
    assert reloaded.anomaly_threshold == 0.6


def test_pennylane_load_weights_resolves_repo_relative_path(monkeypatch, tmp_path: Path):
    detector = _stub_detector(monkeypatch, threshold=0.6)
    detector.weights = [[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]]
    detector.trained = True

    target_dir = REPO_ROOT / "config"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "test_anomaly_detector_weights.json"
    try:
        detector.save_weights(target_path)

        reloaded = _stub_detector(monkeypatch, threshold=0.1)
        reloaded.weights = None
        reloaded.trained = False
        assert reloaded.load_weights("config/test_anomaly_detector_weights.json") is True
        assert reloaded.trained is True
        assert reloaded.anomaly_threshold == 0.6
    finally:
        target_path.unlink(missing_ok=True)


def test_pennylane_module_main_gracefully_degrades_when_dependency_missing():
    code = textwrap.dedent(
        """
        import builtins
        import runpy

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "pennylane" or name.startswith("pennylane."):
                raise ImportError("forced missing dependency")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = fake_import
        runpy.run_module("src.research.backends.pennylane_anomaly_detector", run_name="__main__")
        """
    )

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["backend"] == "pennylane_vqc"
    assert payload["algorithm"] == "VQC"
    assert payload["status"] == "error"
    assert payload["execution_metadata"]["not_for_direct_execution"] is True
    assert payload["execution_metadata"]["quantum_direct_execution_forbidden"] is True
    assert payload["execution_metadata"]["bounded_secondary_signal_only"] is True
    assert payload["execution_metadata"]["pennylane_available"] is False
