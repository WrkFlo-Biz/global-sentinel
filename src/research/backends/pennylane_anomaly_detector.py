#!/usr/bin/env python3
"""PennyLane VQC Anomaly Detector — quantum signal validation for Global Sentinel.

Uses Variational Quantum Circuits on lightning.qubit to flag implausible candidate
signals, regime classification errors, and data quality issues.

ALL outputs are artifact-only with not_for_direct_execution=true.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is expected in runtime envs
    np = None  # type: ignore[assignment]

try:
    import pennylane as qml
    from pennylane import numpy as pnp

    PENNYLANE_AVAILABLE = True
except ImportError:
    qml = None  # type: ignore[assignment]
    pnp = None  # type: ignore[assignment]
    PENNYLANE_AVAILABLE = False

try:
    from sklearn.ensemble import IsolationForest

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_repo_relative_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / candidate


class PennyLaneAnomalyDetector:
    """Quantum VQC anomaly detector using PennyLane lightning.qubit."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.n_qubits = self.config.get("n_qubits", 4)
        self.n_layers = self.config.get("n_layers", 2)
        self.anomaly_threshold = self.config.get("anomaly_threshold", 0.3)
        self.trained = False
        self.weights = None
        self._classical_model = None
        self._weights_path = self.config.get("weights_path", "config/anomaly_detector_weights.json")

        if not PENNYLANE_AVAILABLE:
            raise ImportError(
                "PennyLane not available. "
                "pip install pennylane pennylane-lightning"
            )

        self.dev = qml.device("lightning.qubit", wires=self.n_qubits)

        @qml.qnode(self.dev)
        def _circuit(weights, x):
            qml.AmplitudeEmbedding(
                features=x, wires=range(self.n_qubits),
                normalize=True, pad_with=0.0,
            )
            for layer_w in weights:
                for i in range(self.n_qubits):
                    qml.Rot(layer_w[i, 0], layer_w[i, 1], layer_w[i, 2], wires=i)
                for i in range(self.n_qubits):
                    qml.CNOT(wires=[i, (i + 1) % self.n_qubits])
            return qml.expval(qml.PauliZ(0))

        self._circuit = _circuit
        if self._weights_path:
            self.load_weights(self._weights_path)

    def initialize_weights(self, seed: int = 42):
        rng = np.random.default_rng(seed)
        self.weights = pnp.array(
            rng.standard_normal((self.n_layers, self.n_qubits, 3)),
            requires_grad=True,
        )
        return self.weights

    def train(self, normal_features: List[List[float]], epochs: int = 50,
              learning_rate: float = 0.01) -> dict:
        """Train on normal (non-anomalous) feature vectors."""
        if self.weights is None:
            self.initialize_weights()
        opt = qml.GradientDescentOptimizer(stepsize=learning_rate)
        target_dim = 2 ** self.n_qubits

        padded = []
        for f in normal_features:
            arr = np.array(f, dtype=float)
            if len(arr) < target_dim:
                arr = np.pad(arr, (0, target_dim - len(arr)))
            else:
                arr = arr[:target_dim]
            padded.append(arr)

        total_cost = 0.0
        for _epoch in range(epochs):
            total_cost = 0.0
            for features in padded:
                def cost_fn(w):
                    return -self._circuit(w, features)
                self.weights = opt.step(cost_fn, self.weights)
                total_cost += float(cost_fn(self.weights))

        if SKLEARN_AVAILABLE and padded:
            self._classical_model = IsolationForest(
                contamination="auto",
                random_state=self.config.get("random_state", 42),
            )
            self._classical_model.fit(np.array(padded, dtype=float))
        self.trained = True
        return {"epochs": epochs, "final_avg_cost": total_cost / max(len(padded), 1)}

    def save_weights(self, path: str | Path) -> Dict[str, Any]:
        """Persist trained weights and config for reuse in screening runs."""
        if self.weights is None:
            raise ValueError("weights_not_initialized")
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "pennylane_anomaly_weights.v1",
            "saved_at_utc": _utc_now(),
            "n_qubits": self.n_qubits,
            "n_layers": self.n_layers,
            "anomaly_threshold": self.anomaly_threshold,
            "trained": self.trained,
            "weights": np.array(self.weights, dtype=float).tolist(),
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def load_weights(self, path: str | Path) -> bool:
        """Load previously trained weights if present."""
        weights_path = _resolve_repo_relative_path(path)
        if not weights_path.exists():
            return False
        payload = json.loads(weights_path.read_text(encoding="utf-8"))
        weights = payload.get("weights")
        if not isinstance(weights, list):
            return False
        if pnp is not None:
            self.weights = pnp.array(weights, requires_grad=True)
        elif np is not None:
            self.weights = np.array(weights, dtype=float)
        else:
            self.weights = weights
        self.trained = bool(payload.get("trained", True))
        self.anomaly_threshold = float(
            payload.get("anomaly_threshold", self.anomaly_threshold)
        )
        return True

    def score_candidate(self, candidate: dict) -> dict:
        start = time.monotonic()
        features = candidate.get("features", [])
        candidate_id = candidate.get("candidate_id", candidate.get("symbol", "unknown"))
        target_dim = 2 ** self.n_qubits

        arr = np.array(features, dtype=float)
        if len(arr) < target_dim:
            arr = np.pad(arr, (0, target_dim - len(arr)))
        else:
            arr = arr[:target_dim]

        if self.weights is None:
            self.initialize_weights()

        raw = float(self._circuit(self.weights, arr))
        score = (raw + 1.0) / 2.0  # [-1,1] -> [0,1]
        is_anomaly = score < self.anomaly_threshold
        classical_score, is_anomaly_classical = self._score_classically(arr)
        elapsed = time.monotonic() - start
        artifact_id = hashlib.sha256(
            json.dumps(
                {
                    "backend": "pennylane_vqc",
                    "candidate_id": candidate_id,
                    "timestamp_utc": _utc_now(),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]

        return {
            "backend": "pennylane_vqc",
            "algorithm": "VQC",
            "status": "success",
            "candidate_id": candidate_id,
            "quantum_anomaly_score": round(score, 6),
            "quantum_raw_expectation": round(raw, 6),
            "classical_anomaly_score": (
                round(classical_score, 6) if classical_score is not None else None
            ),
            "is_anomaly_quantum": is_anomaly,
            "is_anomaly_classical": is_anomaly_classical,
            "anomaly_agreement": (
                is_anomaly == is_anomaly_classical if is_anomaly_classical is not None else None
            ),
            "threshold": self.anomaly_threshold,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "backend": "pennylane_vqc",
                "device": "lightning.qubit",
                "n_qubits": self.n_qubits,
                "n_layers": self.n_layers,
                "trained": self.trained,
                "weights_path": (
                    str(getattr(self, "_weights_path", None))
                    if getattr(self, "_weights_path", None)
                    else None
                ),
                "runtime_seconds": round(elapsed, 4),
                "timestamp_utc": _utc_now(),
                "artifact_id": artifact_id,
                "pennylane_available": True,
            },
        }

    def score_batch(self, candidates: list) -> list:
        results = [self.score_candidate(c) for c in candidates]
        results.sort(key=lambda r: r["quantum_anomaly_score"])
        return results

    def _score_classically(self, features):
        """Return a bounded classical anomaly score for comparison."""
        if SKLEARN_AVAILABLE and self._classical_model is not None:
            raw = float(self._classical_model.score_samples([features])[0])
            score = 1.0 / (1.0 + float(np.exp(-raw)))
        else:
            magnitude = float(np.linalg.norm(features))
            score = max(0.0, min(1.0, 1.0 - abs(magnitude - 1.0)))
        return score, score < self.anomaly_threshold


def _standalone_error(message: str) -> dict:
    return {
        "backend": "pennylane_vqc",
        "algorithm": "VQC",
        "status": "error",
        "error": message,
        "selected_candidates": [],
        "execution_metadata": {
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "backend": "pennylane_vqc",
            "status": "error",
            "timestamp_utc": _utc_now(),
            "runtime_seconds": 0.0,
            "pennylane_available": PENNYLANE_AVAILABLE,
        },
    }


if __name__ == "__main__":
    normal = [
        [0.5, 0.3, 0.2, 0.4, 0.6, 0.3, 0.5, 0.4, 0.3, 0.5, 0.4, 0.3, 0.5, 0.4, 0.3, 0.5],
        [0.4, 0.35, 0.25, 0.45, 0.55, 0.35, 0.45, 0.35, 0.35, 0.45, 0.35, 0.35, 0.45, 0.35, 0.35, 0.45],
    ]
    try:
        det = PennyLaneAnomalyDetector(config={"n_qubits": 4, "n_layers": 2})
        det.train(normal, epochs=5, learning_rate=0.05)
        result = det.score_candidate({"candidate_id": "SPY", "features": normal[0]})
    except Exception as exc:  # pragma: no cover - exercised via subprocess
        result = _standalone_error(str(exc))
    print(json.dumps(result, indent=2, default=str))
