#!/usr/bin/env python3
"""Train PennyLane anomaly detector from recent research artifacts."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _extract_features(candidate: Dict[str, Any]) -> List[float]:
    if isinstance(candidate.get("features"), list):
        return [float(x) for x in candidate["features"]]
    return [
        _safe_float(candidate.get("preopt_score", candidate.get("score", 0.0))),
        _safe_float(candidate.get("expected_return", candidate.get("score", 0.0))),
        _safe_float(candidate.get("volatility", 0.2)),
        _safe_float(candidate.get("weight", 0.0)),
        _safe_float(candidate.get("liquidity_bonus", 0.0)),
        _safe_float(candidate.get("impact_penalty", 0.0)),
        _safe_float(candidate.get("regime_alignment", 0.0)),
        _safe_float(candidate.get("quantum_anomaly_score", 0.5)),
    ]


def _load_recent_candidates(repo_root: Path, limit: int) -> List[Dict[str, Any]]:
    files = sorted(
        glob.glob(str(repo_root / "reports" / "research" / "comparisons" / "comparison_*.json"))
    )[-limit:]
    candidates: List[Dict[str, Any]] = []
    for path_str in files:
        path = Path(path_str)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pre = payload.get("preoptimization_screening") or {}
        opt = pre.get("optimization_request") or {}
        for candidate in opt.get("candidates") or []:
            metadata = (candidate.get("metadata") or {}).get("anomaly_screening") or {}
            if metadata.get("screened") and metadata.get("is_anomaly_quantum") is False:
                candidates.append(candidate)
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Train PennyLane anomaly detector on recent comparison artifacts")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--artifact-limit", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--weights-output", default="config/anomaly_detector_weights.json")
    parser.add_argument("--report-output", default="reports/research/anomaly_detector_training_report.json")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.research.backends.pennylane_anomaly_detector import PennyLaneAnomalyDetector

    candidates = _load_recent_candidates(repo_root, args.artifact_limit)
    normal_features = [_extract_features(candidate) for candidate in candidates]

    detector = PennyLaneAnomalyDetector(
        {
            "n_qubits": 4,
            "n_layers": 2,
            "anomaly_threshold": 0.3,
            "weights_path": str(repo_root / args.weights_output),
        }
    )

    if not normal_features:
        report = {
            "status": "skipped",
            "reason": "no_normal_feature_vectors_found",
            "artifact_limit": args.artifact_limit,
            "weights_output": str(repo_root / args.weights_output),
            "report_output": str(repo_root / args.report_output),
        }
    else:
        train_result = detector.train(
            normal_features,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
        )
        weights_payload = detector.save_weights(repo_root / args.weights_output)
        report = {
            "status": "success",
            "artifact_limit": args.artifact_limit,
            "normal_feature_count": len(normal_features),
            "feature_dimension": len(normal_features[0]),
            "train_result": train_result,
            "weights_output": str(repo_root / args.weights_output),
            "weights_schema_version": weights_payload.get("schema_version"),
            "report_output": str(repo_root / args.report_output),
        }

    report_path = repo_root / args.report_output
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
