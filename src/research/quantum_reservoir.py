#!/usr/bin/env python3
"""Quantum Reservoir Computing for Market Regime Detection.

Uses a 6-qubit PennyLane quantum reservoir circuit for volatility regime
classification (calm / transition / crisis). The reservoir has fixed random
entangling layers; only the classical readout (logistic regression) is trained.

Highest-impact quantum enhancement: 6 qubits, 86%+ accuracy target.

Features (6 inputs):
  1. VIX level (normalized)
  2. SPY daily return
  3. Oil (WTI) daily return
  4. Yield curve slope (10Y-2Y)
  5. Put/Call ratio
  6. UVXY daily change

Output: probability distribution over [calm, transition, crisis]
Written to data/quantum_feed/quantum_regime_prediction.json

Deployed 2026-03-25.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# PennyLane for quantum circuit simulation
try:
    import pennylane as qml
    HAS_PENNYLANE = True
except ImportError:
    HAS_PENNYLANE = False

# sklearn for classical comparison and readout layer
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import accuracy_score, classification_report
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
N_QUBITS = 6
N_RESERVOIR_LAYERS = 3
REGIME_LABELS = {0: "calm", 1: "transition", 2: "crisis"}
REGIME_LABEL_TO_IDX = {"calm": 0, "transition": 1, "crisis": 2}

QUANTUM_FEED = PROJECT_ROOT / "data" / "quantum_feed"
MODEL_PATH = QUANTUM_FEED / "quantum_reservoir_model.json"
PREDICTION_PATH = QUANTUM_FEED / "quantum_regime_prediction.json"
TRAINING_LOG_PATH = PROJECT_ROOT / "reports" / "research" / "quantum_reservoir_training.jsonl"
COMPARISON_PATH = PROJECT_ROOT / "reports" / "research" / "comparisons" / "quantum_reservoir_vs_classical.json"

# Fixed random seed for reservoir connectivity (reproducible)
RESERVOIR_SEED = 42

FEATURE_NAMES = [
    "vix_normalized",
    "spy_return",
    "oil_return",
    "yield_curve_slope",
    "put_call_ratio",
    "uvxy_change",
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_read(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_json_write(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.rename(path)
        return True
    except Exception as e:
        logger.error("Failed to write %s: %s", path, e)
        return False


def _safe_jsonl_append(path: Path, entry: Dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return True
    except Exception:
        return False


# ===================================================================
# 1. QUANTUM RESERVOIR CIRCUIT
# ===================================================================

class QuantumReservoirCircuit:
    """6-qubit quantum reservoir with fixed random entangling layers.

    Input encoding: RY rotations parameterized by market features.
    Reservoir dynamics: fixed CZ gates with random connectivity.
    Readout: expectation values of PauliZ on all qubits.
    """

    def __init__(self, n_qubits: int = N_QUBITS, n_layers: int = N_RESERVOIR_LAYERS,
                 seed: int = RESERVOIR_SEED):
        self.n_qubits = n_qubits
        self.n_layers = n_layers
        self.seed = seed

        if not HAS_PENNYLANE:
            raise ImportError("PennyLane required for quantum reservoir computing")

        # Generate fixed random reservoir connectivity
        rng = np.random.RandomState(seed)
        self.reservoir_rotations = []
        self.reservoir_cz_pairs = []
        for layer in range(n_layers):
            # Random RZ/RX rotations (fixed, not trainable)
            rotations = rng.uniform(0, 2 * np.pi, size=(n_qubits, 2))
            self.reservoir_rotations.append(rotations)
            # Random CZ connectivity (subset of all pairs)
            all_pairs = [(i, j) for i in range(n_qubits) for j in range(i + 1, n_qubits)]
            n_cz = rng.randint(n_qubits, len(all_pairs) + 1)
            selected = [all_pairs[k] for k in rng.choice(len(all_pairs), size=min(n_cz, len(all_pairs)), replace=False)]
            self.reservoir_cz_pairs.append(selected)

        # Build PennyLane device and QNode
        self.dev = qml.device("default.qubit", wires=n_qubits)
        self._qnode = qml.QNode(self._circuit, self.dev, interface="numpy")

    def _circuit(self, features: np.ndarray):
        """Quantum reservoir circuit.

        Args:
            features: array of shape (n_qubits,) with normalized market features
        """
        # Input encoding: RY rotations
        for i in range(self.n_qubits):
            qml.RY(features[i], wires=i)

        # Reservoir dynamics: fixed random entangling layers
        for layer in range(self.n_layers):
            # Fixed RZ/RX rotations
            for i in range(self.n_qubits):
                qml.RZ(self.reservoir_rotations[layer][i, 0], wires=i)
                qml.RX(self.reservoir_rotations[layer][i, 1], wires=i)
            # CZ entangling gates
            for i, j in self.reservoir_cz_pairs[layer]:
                qml.CZ(wires=[i, j])

        # Readout: PauliZ expectation on all qubits
        return [qml.expval(qml.PauliZ(i)) for i in range(self.n_qubits)]

    def get_reservoir_output(self, features: np.ndarray) -> np.ndarray:
        """Run a single feature vector through the reservoir.

        Args:
            features: array of shape (6,) -- raw or normalized market features

        Returns:
            array of shape (6,) -- expectation values from reservoir qubits
        """
        # Clamp features to [-pi, pi] for stable encoding
        encoded = np.clip(np.array(features, dtype=np.float64), -np.pi, np.pi)
        if len(encoded) < self.n_qubits:
            encoded = np.pad(encoded, (0, self.n_qubits - len(encoded)))
        elif len(encoded) > self.n_qubits:
            encoded = encoded[:self.n_qubits]

        result = self._qnode(encoded)
        return np.array(result, dtype=np.float64)

    def get_reservoir_batch(self, feature_matrix: np.ndarray) -> np.ndarray:
        """Run batch of feature vectors through reservoir.

        Args:
            feature_matrix: shape (n_samples, 6)

        Returns:
            shape (n_samples, 6) -- reservoir outputs
        """
        outputs = []
        for row in feature_matrix:
            outputs.append(self.get_reservoir_output(row))
        return np.array(outputs)

    def circuit_info(self) -> Dict[str, Any]:
        return {
            "n_qubits": self.n_qubits,
            "n_layers": self.n_layers,
            "seed": self.seed,
            "device": "default.qubit",
            "encoding": "RY",
            "reservoir_gates": "RZ + RX + CZ (fixed random)",
            "readout": "PauliZ expectation (all qubits)",
        }


# ===================================================================
# 2. FEATURE EXTRACTION & LABELING
# ===================================================================

class FeatureExtractor:
    """Extract and normalize the 6 market features for the reservoir."""

    def __init__(self):
        self.scaler = StandardScaler() if HAS_SKLEARN else None
        self._fitted = False

    def extract_from_market_data(self, market_data: Dict) -> Optional[np.ndarray]:
        """Extract 6 features from a latest_signal-style market data dict.

        Expected keys in market_data:
          - vix.price or vix
          - spy_return or sp500_futures.change_pct
          - oil_wti.change_pct or oil_return
          - yield_curve_slope (10Y-2Y spread)
          - put_call_ratio
          - uvxy_change
        """
        try:
            # VIX normalized (divide by 40 to get ~[0, 1] range, then scale to [-pi, pi])
            vix_raw = self._get_nested(market_data, ["vix", "price"],
                                        market_data.get("vix", 20))
            vix_norm = (float(vix_raw) - 20.0) / 20.0  # center around 20

            # SPY return
            spy_ret = self._get_nested(market_data, ["sp500_futures", "change_pct"],
                                        market_data.get("spy_return", 0))
            spy_ret = float(spy_ret) / 100.0 if abs(float(spy_ret)) > 1 else float(spy_ret)

            # Oil return
            oil_ret = self._get_nested(market_data, ["oil_wti", "change_pct"],
                                        market_data.get("oil_return", 0))
            oil_ret = float(oil_ret) / 100.0 if abs(float(oil_ret)) > 1 else float(oil_ret)

            # Yield curve slope (default ~1.0)
            yc_slope = float(market_data.get("yield_curve_slope", 1.0))

            # Put/Call ratio (default ~0.9)
            pc_ratio = float(market_data.get("put_call_ratio", 0.9))
            pc_ratio = (pc_ratio - 0.9) / 0.3  # normalize around 0.9

            # UVXY change
            uvxy = float(market_data.get("uvxy_change", 0))
            uvxy = uvxy / 100.0 if abs(uvxy) > 1 else uvxy

            features = np.array([vix_norm, spy_ret, oil_ret, yc_slope, pc_ratio, uvxy],
                                dtype=np.float64)

            # Scale to [-pi, pi] range for quantum encoding
            features = np.clip(features * np.pi, -np.pi, np.pi)

            return features

        except Exception as e:
            logger.warning("Feature extraction failed: %s", e)
            return None

    def extract_from_signal_file(self, signal_path: Path) -> Optional[np.ndarray]:
        """Extract features from a latest_signal.json file."""
        data = _safe_json_read(signal_path)
        if not data:
            return None
        market = data.get("market_data", data)
        return self.extract_from_market_data(market)

    def label_regime(self, vix_change: float) -> int:
        """Label regime based on next-day VIX move.

        > 2.0 points = crisis (2)
        0.5 to 2.0 = transition (1)
        < 0.5 = calm (0)
        """
        abs_change = abs(vix_change)
        if abs_change > 2.0:
            return 2  # crisis
        elif abs_change >= 0.5:
            return 1  # transition
        else:
            return 0  # calm

    def build_training_dataset(self, lookback_days: int = 90) -> Tuple[np.ndarray, np.ndarray]:
        """Build training dataset from historical data.

        Looks for:
          1. data/quantum_feed/ signal history
          2. HMM regime history for labels
          3. Synthetic data generation if insufficient real data
        """
        features_list = []
        labels_list = []

        # Try to load HMM regime data for labels
        hmm_data = _safe_json_read(QUANTUM_FEED / "hmm_regime.json")
        recent_history = hmm_data.get("recent_regime_history", []) if hmm_data else []

        # Try to collect from quantum_feed snapshots
        for signal_file in sorted(QUANTUM_FEED.glob("signal_snapshot_*.json"))[-lookback_days:]:
            data = _safe_json_read(signal_file)
            if not data:
                continue
            market = data.get("market_data", data)
            feats = self.extract_from_market_data(market)
            if feats is not None:
                features_list.append(feats)
                # Use VIX change for labeling if available
                vix_change = self._get_nested(market, ["vix", "change_pct"],
                                               data.get("vix_change", 0))
                labels_list.append(self.label_regime(float(vix_change)))

        # If insufficient data, generate synthetic training samples
        if len(features_list) < 30:
            logger.info("Insufficient historical data (%d samples), generating synthetic dataset",
                        len(features_list))
            synth_features, synth_labels = self._generate_synthetic_dataset(
                n_samples=max(100, lookback_days),
                existing_features=features_list,
                existing_labels=labels_list,
            )
            features_list = synth_features
            labels_list = synth_labels

        X = np.array(features_list, dtype=np.float64)
        y = np.array(labels_list, dtype=np.int32)

        return X, y

    def _generate_synthetic_dataset(self, n_samples: int = 100,
                                     existing_features: Optional[List] = None,
                                     existing_labels: Optional[List] = None,
                                     ) -> Tuple[List, List]:
        """Generate synthetic market regime data for initial training.

        Uses realistic distributions for each regime class:
          - Calm: low VIX (15-20), small returns, normal yield curve
          - Transition: medium VIX (20-28), moderate returns, flattening curve
          - Crisis: high VIX (28-80), large returns, inverted/steep curve
        """
        rng = np.random.RandomState(123)
        features = []
        labels = []

        # Include any existing data
        if existing_features:
            features.extend(existing_features)
            labels.extend(existing_labels or [0] * len(existing_features))

        # Distribution parameters for each regime
        regime_params = {
            0: {  # calm
                "vix_mean": 17, "vix_std": 3,
                "spy_mean": 0.05, "spy_std": 0.5,
                "oil_mean": 0.02, "oil_std": 0.8,
                "yc_mean": 1.2, "yc_std": 0.3,
                "pc_mean": 0.85, "pc_std": 0.1,
                "uvxy_mean": -0.5, "uvxy_std": 2.0,
            },
            1: {  # transition
                "vix_mean": 24, "vix_std": 4,
                "spy_mean": -0.3, "spy_std": 1.2,
                "oil_mean": 0.5, "oil_std": 1.5,
                "yc_mean": 0.5, "yc_std": 0.5,
                "pc_mean": 1.0, "pc_std": 0.15,
                "uvxy_mean": 2.0, "uvxy_std": 4.0,
            },
            2: {  # crisis
                "vix_mean": 35, "vix_std": 12,
                "spy_mean": -1.5, "spy_std": 2.5,
                "oil_mean": 1.5, "oil_std": 3.0,
                "yc_mean": -0.2, "yc_std": 0.8,
                "pc_mean": 1.3, "pc_std": 0.25,
                "uvxy_mean": 8.0, "uvxy_std": 6.0,
            },
        }

        # Class balance: 60% calm, 25% transition, 15% crisis (realistic)
        remaining = n_samples - len(features)
        class_counts = {0: int(remaining * 0.60), 1: int(remaining * 0.25), 2: int(remaining * 0.15)}
        # Ensure we hit the target
        class_counts[0] += remaining - sum(class_counts.values())

        for regime, count in class_counts.items():
            params = regime_params[regime]
            for _ in range(count):
                vix_raw = rng.normal(params["vix_mean"], params["vix_std"])
                vix_norm = (vix_raw - 20.0) / 20.0

                spy_ret = rng.normal(params["spy_mean"], params["spy_std"]) / 100.0
                oil_ret = rng.normal(params["oil_mean"], params["oil_std"]) / 100.0
                yc_slope = rng.normal(params["yc_mean"], params["yc_std"])
                pc_ratio = (rng.normal(params["pc_mean"], params["pc_std"]) - 0.9) / 0.3
                uvxy = rng.normal(params["uvxy_mean"], params["uvxy_std"]) / 100.0

                feat = np.clip(
                    np.array([vix_norm, spy_ret, oil_ret, yc_slope, pc_ratio, uvxy]) * np.pi,
                    -np.pi, np.pi,
                )
                features.append(feat)
                labels.append(regime)

        return features, labels

    @staticmethod
    def _get_nested(d: Dict, keys: List[str], default: Any = 0) -> Any:
        """Navigate nested dict with a key path."""
        current = d
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current


# ===================================================================
# 3. QUANTUM REGIME CLASSIFIER (train + infer)
# ===================================================================

class QuantumRegimeClassifier:
    """Full quantum reservoir computing pipeline for regime detection.

    Architecture:
      Input features (6) -> Quantum Reservoir (6 qubits, fixed) -> Expectation values (6)
      -> Classical Logistic Regression -> P(calm), P(transition), P(crisis)
    """

    def __init__(self, n_qubits: int = N_QUBITS, n_layers: int = N_RESERVOIR_LAYERS):
        self.reservoir = QuantumReservoirCircuit(n_qubits=n_qubits, n_layers=n_layers)
        self.extractor = FeatureExtractor()
        self.classifier: Optional[LogisticRegression] = None
        self.classical_rf: Optional[RandomForestClassifier] = None
        self.scaler = StandardScaler() if HAS_SKLEARN else None
        self._trained = False
        self._training_metadata: Dict[str, Any] = {}

    def train(self, X: Optional[np.ndarray] = None, y: Optional[np.ndarray] = None,
              lookback_days: int = 90) -> Dict[str, Any]:
        """Train the quantum reservoir readout layer.

        If X, y not provided, builds dataset from historical data.

        Returns:
            Training report dict with accuracy metrics.
        """
        if not HAS_SKLEARN:
            return {"status": "error", "error": "scikit-learn not available"}

        start_time = time.time()

        # Build dataset if not provided
        if X is None or y is None:
            X, y = self.extractor.build_training_dataset(lookback_days=lookback_days)

        if len(X) < 10:
            return {"status": "error", "error": f"Insufficient training data: {len(X)} samples"}

        logger.info("Training quantum reservoir classifier on %d samples", len(X))

        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # Run all samples through quantum reservoir
        logger.info("Computing quantum reservoir outputs for %d samples...", len(X))
        reservoir_start = time.time()
        X_quantum = self.reservoir.get_reservoir_batch(X_scaled)
        reservoir_time = time.time() - reservoir_start
        logger.info("Reservoir computation took %.2fs", reservoir_time)

        # Train classical readout (logistic regression on reservoir outputs)
        self.classifier = LogisticRegression(
            multi_class="multinomial",
            solver="lbfgs",
            max_iter=1000,
            class_weight="balanced",
            C=1.0,
        )
        self.classifier.fit(X_quantum, y)

        # Cross-validation accuracy
        quantum_cv_scores = cross_val_score(
            LogisticRegression(multi_class="multinomial", solver="lbfgs",
                               max_iter=1000, class_weight="balanced"),
            X_quantum, y, cv=min(5, len(X) // 3), scoring="accuracy"
        )
        quantum_accuracy = float(np.mean(quantum_cv_scores))
        quantum_std = float(np.std(quantum_cv_scores))

        # ---------------------------------------------------------------
        # Classical comparison: Random Forest on raw features
        # ---------------------------------------------------------------
        self.classical_rf = RandomForestClassifier(
            n_estimators=100, max_depth=5, random_state=42, class_weight="balanced"
        )
        self.classical_rf.fit(X_scaled, y)

        classical_cv_scores = cross_val_score(
            RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42,
                                    class_weight="balanced"),
            X_scaled, y, cv=min(5, len(X) // 3), scoring="accuracy"
        )
        classical_accuracy = float(np.mean(classical_cv_scores))
        classical_std = float(np.std(classical_cv_scores))

        # Training predictions for detailed report
        quantum_preds = self.classifier.predict(X_quantum)
        classical_preds = self.classical_rf.predict(X_scaled)

        train_time = time.time() - start_time
        self._trained = True

        # Build training metadata
        self._training_metadata = {
            "trained_at": _utc_now(),
            "n_samples": int(len(X)),
            "n_features": int(X.shape[1]),
            "n_qubits": self.reservoir.n_qubits,
            "n_layers": self.reservoir.n_layers,
            "quantum_cv_accuracy": round(quantum_accuracy, 4),
            "quantum_cv_std": round(quantum_std, 4),
            "classical_cv_accuracy": round(classical_accuracy, 4),
            "classical_cv_std": round(classical_std, 4),
            "quantum_advantage": round(quantum_accuracy - classical_accuracy, 4),
            "quantum_train_accuracy": round(float(accuracy_score(y, quantum_preds)), 4),
            "classical_train_accuracy": round(float(accuracy_score(y, classical_preds)), 4),
            "reservoir_compute_seconds": round(reservoir_time, 3),
            "total_train_seconds": round(train_time, 3),
            "class_distribution": {
                REGIME_LABELS[i]: int(np.sum(y == i)) for i in range(3)
            },
            "circuit_info": self.reservoir.circuit_info(),
        }

        # Save model
        self._save_model()

        # Save comparison report
        comparison_report = {
            "experiment": "quantum_reservoir_regime_detection",
            "timestamp_utc": _utc_now(),
            "quantum_method": "6-qubit reservoir computing + logistic regression",
            "classical_method": "random forest (100 trees, depth 5)",
            "n_samples": int(len(X)),
            "quantum_accuracy": round(quantum_accuracy, 4),
            "classical_accuracy": round(classical_accuracy, 4),
            "quantum_advantage_pct": round((quantum_accuracy - classical_accuracy) * 100, 2),
            "reservoir_info": self.reservoir.circuit_info(),
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "backend": "pennylane_reservoir",
            },
        }
        _safe_json_write(COMPARISON_PATH, comparison_report)

        # Append to training log
        _safe_jsonl_append(TRAINING_LOG_PATH, self._training_metadata)

        logger.info(
            "Training complete: quantum_accuracy=%.4f (+/-%.4f), "
            "classical_accuracy=%.4f (+/-%.4f), advantage=%.4f",
            quantum_accuracy, quantum_std, classical_accuracy, classical_std,
            quantum_accuracy - classical_accuracy,
        )

        return {
            "status": "success",
            **self._training_metadata,
        }

    def predict(self, features: Optional[np.ndarray] = None,
                market_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Run inference on today's market features.

        Args:
            features: raw feature vector (6,), or
            market_data: dict from latest_signal.json market_data section

        Returns:
            Prediction dict with regime probabilities.
        """
        if not self._trained and not self._load_model():
            return {"status": "error", "error": "Model not trained. Run train() first."}

        # Extract features
        if features is None and market_data is not None:
            features = self.extractor.extract_from_market_data(market_data)
        elif features is None:
            # Try loading from latest signal
            features = self.extractor.extract_from_signal_file(
                QUANTUM_FEED / "latest_signal.json"
            )

        if features is None:
            return {"status": "error", "error": "No features available for prediction"}

        try:
            # Scale features
            features_scaled = self.scaler.transform(features.reshape(1, -1))

            # Quantum reservoir
            reservoir_output = self.reservoir.get_reservoir_output(features_scaled[0])

            # Classify
            quantum_probs = self.classifier.predict_proba(reservoir_output.reshape(1, -1))[0]
            quantum_pred = int(self.classifier.predict(reservoir_output.reshape(1, -1))[0])

            # Classical comparison
            classical_probs = None
            classical_pred = None
            if self.classical_rf is not None:
                classical_probs = self.classical_rf.predict_proba(features_scaled)[0]
                classical_pred = int(self.classical_rf.predict(features_scaled)[0])

            result = {
                "timestamp_utc": _utc_now(),
                "status": "success",
                "quantum_prediction": {
                    "regime": REGIME_LABELS[quantum_pred],
                    "regime_idx": quantum_pred,
                    "probabilities": {
                        REGIME_LABELS[i]: round(float(quantum_probs[i]), 4)
                        for i in range(len(quantum_probs))
                    },
                    "confidence": round(float(np.max(quantum_probs)), 4),
                    "method": "quantum_reservoir_6q",
                    "reservoir_outputs": [round(float(v), 6) for v in reservoir_output],
                },
                "model_metadata": self._training_metadata,
                "execution_metadata": {
                    "not_for_direct_execution": True,
                    "quantum_direct_execution_forbidden": True,
                    "bounded_secondary_signal_only": True,
                    "backend": "pennylane_reservoir",
                    "n_qubits": self.reservoir.n_qubits,
                },
            }

            if classical_probs is not None and classical_pred is not None:
                result["classical_prediction"] = {
                    "regime": REGIME_LABELS[classical_pred],
                    "regime_idx": classical_pred,
                    "probabilities": {
                        REGIME_LABELS[i]: round(float(classical_probs[i]), 4)
                        for i in range(len(classical_probs))
                    },
                    "confidence": round(float(np.max(classical_probs)), 4),
                    "method": "random_forest",
                }
                result["agreement"] = quantum_pred == classical_pred
                result["ensemble_probabilities"] = {
                    REGIME_LABELS[i]: round(float(0.6 * quantum_probs[i] + 0.4 * classical_probs[i]), 4)
                    for i in range(min(len(quantum_probs), len(classical_probs)))
                }
            else:
                result["classical_prediction"] = None
                result["agreement"] = None
                result["ensemble_probabilities"] = {
                    REGIME_LABELS[i]: round(float(quantum_probs[i]), 4)
                    for i in range(len(quantum_probs))
                }

            # Write prediction to quantum_feed
            _safe_json_write(PREDICTION_PATH, result)

            logger.info(
                "Quantum regime prediction: %s (conf=%.3f) | Classical: %s | Agree: %s",
                REGIME_LABELS[quantum_pred], np.max(quantum_probs),
                REGIME_LABELS[classical_pred] if classical_pred is not None else "N/A",
                result["agreement"],
            )

            return result

        except Exception as e:
            logger.error("Prediction failed: %s\n%s", e, traceback.format_exc())
            return {"status": "error", "error": str(e)}

    def _save_model(self) -> bool:
        """Save model parameters to JSON (logistic regression coefficients + scaler)."""
        try:
            if self.classifier is None or self.scaler is None:
                return False

            model_data = {
                "saved_at": _utc_now(),
                "model_type": "quantum_reservoir_logistic_regression",
                "n_qubits": self.reservoir.n_qubits,
                "n_layers": self.reservoir.n_layers,
                "reservoir_seed": self.reservoir.seed,
                "scaler_mean": self.scaler.mean_.tolist(),
                "scaler_scale": self.scaler.scale_.tolist(),
                "classifier_coef": self.classifier.coef_.tolist(),
                "classifier_intercept": self.classifier.intercept_.tolist(),
                "classifier_classes": self.classifier.classes_.tolist(),
                "classical_rf_trained": self.classical_rf is not None,
                "training_metadata": self._training_metadata,
            }

            # Also save RF feature importances
            if self.classical_rf is not None:
                model_data["rf_feature_importances"] = dict(
                    zip(FEATURE_NAMES, self.classical_rf.feature_importances_.tolist())
                )

            return _safe_json_write(MODEL_PATH, model_data)

        except Exception as e:
            logger.error("Failed to save model: %s", e)
            return False

    def _load_model(self) -> bool:
        """Load model from saved JSON."""
        try:
            if not MODEL_PATH.exists():
                return False

            data = _safe_json_read(MODEL_PATH)
            if not data:
                return False

            # Reconstruct scaler
            self.scaler = StandardScaler()
            self.scaler.mean_ = np.array(data["scaler_mean"])
            self.scaler.scale_ = np.array(data["scaler_scale"])
            self.scaler.var_ = self.scaler.scale_ ** 2
            self.scaler.n_features_in_ = len(self.scaler.mean_)
            self.scaler.n_samples_seen_ = 100  # approximate

            # Reconstruct logistic regression
            self.classifier = LogisticRegression(
                multi_class="multinomial", solver="lbfgs", max_iter=1000
            )
            self.classifier.coef_ = np.array(data["classifier_coef"])
            self.classifier.intercept_ = np.array(data["classifier_intercept"])
            self.classifier.classes_ = np.array(data["classifier_classes"])
            self.classifier.n_features_in_ = self.classifier.coef_.shape[1]

            # Reconstruct RF (retrain needed for full RF, but we can do inference
            # with quantum only if RF not available)
            self.classical_rf = None  # Will be retrained on next full train()

            self._training_metadata = data.get("training_metadata", {})
            self._trained = True

            logger.info("Loaded quantum reservoir model from %s", MODEL_PATH)
            return True

        except Exception as e:
            logger.warning("Failed to load model: %s", e)
            return False


# ===================================================================
# 4. WRAPPER FOR CONTINUOUS LEARNER INTEGRATION
# ===================================================================

def run_quantum_reservoir_training(lookback_days: int = 90) -> Dict[str, Any]:
    """Training wrapper -- called by quantum_continuous_learner.py.

    Returns training report dict.
    """
    logger.info("=== Quantum Reservoir Training ===")
    try:
        classifier = QuantumRegimeClassifier()
        report = classifier.train(lookback_days=lookback_days)
        return report
    except Exception as e:
        logger.error("Quantum reservoir training failed: %s\n%s", e, traceback.format_exc())
        return {"status": "error", "error": str(e)}


def run_quantum_reservoir_inference(market_data: Optional[Dict] = None) -> Dict[str, Any]:
    """Inference wrapper -- called by quantum_continuous_learner.py.

    Args:
        market_data: Optional dict with market features. If None, reads latest_signal.json.

    Returns prediction dict with regime probabilities.
    """
    logger.info("=== Quantum Reservoir Inference ===")
    try:
        classifier = QuantumRegimeClassifier()
        result = classifier.predict(market_data=market_data)
        return result
    except Exception as e:
        logger.error("Quantum reservoir inference failed: %s\n%s", e, traceback.format_exc())
        return {"status": "error", "error": str(e)}


def run_quantum_reservoir_full(market_data: Optional[Dict] = None,
                                lookback_days: int = 90,
                                force_retrain: bool = False) -> Dict[str, Any]:
    """Full train + inference wrapper for daily use.

    Trains if model doesn't exist or force_retrain is True, then runs inference.
    """
    logger.info("=== Quantum Reservoir Full Pipeline ===")
    try:
        classifier = QuantumRegimeClassifier()

        # Train if needed
        model_exists = MODEL_PATH.exists()
        if not model_exists or force_retrain:
            train_report = classifier.train(lookback_days=lookback_days)
            if train_report.get("status") != "success":
                return {"status": "error", "phase": "training", "details": train_report}
        else:
            if not classifier._load_model():
                train_report = classifier.train(lookback_days=lookback_days)
                if train_report.get("status") != "success":
                    return {"status": "error", "phase": "training", "details": train_report}

        # Run inference
        result = classifier.predict(market_data=market_data)
        return result

    except Exception as e:
        logger.error("Quantum reservoir pipeline failed: %s\n%s", e, traceback.format_exc())
        return {"status": "error", "error": str(e)}


# ===================================================================
# 5. CLI ENTRY POINT
# ===================================================================

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Quantum Reservoir Regime Detector")
    parser.add_argument("--train", action="store_true", help="Train the model")
    parser.add_argument("--predict", action="store_true", help="Run inference")
    parser.add_argument("--full", action="store_true", help="Train + predict")
    parser.add_argument("--lookback", type=int, default=90, help="Training lookback days")
    parser.add_argument("--test", action="store_true", help="Run test with sample data")
    args = parser.parse_args()

    if args.test:
        print("Running quantum reservoir test with sample data...")
        classifier = QuantumRegimeClassifier()

        # Generate small synthetic dataset
        extractor = FeatureExtractor()
        X, y = extractor._generate_synthetic_dataset(n_samples=50)
        X = np.array(X)
        y = np.array(y)

        # Train
        report = classifier.train(X=X, y=y)
        print(f"\nTraining Report:")
        print(f"  Status: {report.get('status')}")
        print(f"  Samples: {report.get('n_samples')}")
        print(f"  Quantum CV Accuracy: {report.get('quantum_cv_accuracy', 0):.4f} "
              f"(+/-{report.get('quantum_cv_std', 0):.4f})")
        print(f"  Classical CV Accuracy: {report.get('classical_cv_accuracy', 0):.4f} "
              f"(+/-{report.get('classical_cv_std', 0):.4f})")
        print(f"  Quantum Advantage: {report.get('quantum_advantage', 0):+.4f}")
        print(f"  Reservoir Compute Time: {report.get('reservoir_compute_seconds', 0):.2f}s")

        # Test inference with sample market data
        sample_market = {
            "vix": {"price": 25.33, "change_pct": -6.01},
            "sp500_futures": {"change_pct": -0.3},
            "oil_wti": {"change_pct": 1.86},
            "yield_curve_slope": 0.8,
            "put_call_ratio": 0.95,
            "uvxy_change": -3.5,
        }
        prediction = classifier.predict(market_data=sample_market)
        print(f"\nPrediction (sample market data):")
        qp = prediction.get("quantum_prediction", {})
        cp = prediction.get("classical_prediction", {})
        print(f"  Quantum: {qp.get('regime')} (conf={qp.get('confidence', 0):.3f})")
        if cp:
            print(f"  Classical: {cp.get('regime')} (conf={cp.get('confidence', 0):.3f})")
        print(f"  Agreement: {prediction.get('agreement')}")
        print(f"  Ensemble probs: {prediction.get('ensemble_probabilities')}")
        print(f"\nPrediction written to: {PREDICTION_PATH}")
        print(f"Model saved to: {MODEL_PATH}")

    elif args.train:
        result = run_quantum_reservoir_training(lookback_days=args.lookback)
        print(json.dumps(result, indent=2, default=str))

    elif args.predict:
        result = run_quantum_reservoir_inference()
        print(json.dumps(result, indent=2, default=str))

    elif args.full:
        result = run_quantum_reservoir_full(lookback_days=args.lookback)
        print(json.dumps(result, indent=2, default=str))

    else:
        parser.print_help()
