#!/usr/bin/env python3
"""Run repeated overnight quantum comparisons using the latest cached request.

This is a research-only helper that replays the most recent comparison request
through the multi-backend orchestrator while markets are closed. Results can be
logged into the experiment tracker to accumulate baseline comparison data
without touching execution paths.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(request, dict):
        return None
    normalized = dict(request)
    if not normalized.get("candidates") and normalized.get("candidate_universe"):
        normalized["candidates"] = list(normalized.get("candidate_universe") or [])
    candidates = normalized.get("candidates") or []
    return normalized if isinstance(candidates, list) and candidates else None


def extract_request_from_artifact(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Recover an optimization request from a comparison artifact."""
    request = _normalize_request(payload.get("request") or {})
    if request:
        return request

    request = _normalize_request(payload.get("optimization_request") or {})
    if request:
        return request

    screening = payload.get("preoptimization_screening") or {}
    if isinstance(screening, dict):
        request = _normalize_request(screening.get("optimization_request") or {})
        if request:
            return request
        request = _normalize_request(screening.get("request") or {})
        if request:
            return request

    return None


def find_comparison_artifacts(repo_root: Path) -> list[Path]:
    patterns = [
        repo_root / "reports" / "research" / "operational" / "operational_comparison_*.json",
        repo_root / "reports" / "research" / "comparisons" / "comparison_*.json",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(Path(path) for path in glob.glob(str(pattern)))
    return sorted(candidates)


def _load_artifact_request(repo_root: Path) -> tuple[Optional[Dict[str, Any]], Optional[Path], Optional[str]]:
    artifact_paths = find_comparison_artifacts(repo_root)
    if not artifact_paths:
        return None, None, "No comparison artifacts found"
    last_error: Optional[str] = None
    for artifact_path in reversed(artifact_paths):
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            last_error = f"Failed to read artifact {artifact_path.name}: {exc}"
            continue
        request = extract_request_from_artifact(payload)
        if request is not None:
            return request, artifact_path, None
        last_error = f"No reusable request found in {artifact_path.name}"
    return None, artifact_paths[-1], last_error or "No reusable request found in comparison artifacts"


def run_batch(
    iterations: int = 10,
    sleep_seconds: int = 60,
    *,
    repo_root: str | Path = ".",
    mode: str = "full",
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    request, artifact_path, error = _load_artifact_request(repo_root)
    report: Dict[str, Any] = {
        "status": "pending",
        "timestamp_utc": _iso_now(),
        "iterations_requested": iterations,
        "iterations_completed": 0,
        "sleep_seconds": sleep_seconds,
        "mode": mode,
        "source_artifact": str(artifact_path) if artifact_path else None,
        "results": [],
        "execution_metadata": {
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "script": "overnight_quantum_batch",
        },
    }

    if request is None:
        report["status"] = "skipped"
        report["reason"] = error
        return report

    from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
    from src.research.experiment_tracker import ExperimentTracker

    orchestrator = MultiBackendOrchestrator()
    tracker = ExperimentTracker(repo_root)

    print(
        f"Running {iterations} overnight comparisons on "
        f"{len(request.get('candidates', []))} candidates from {artifact_path}"
    )

    for index in range(iterations):
        try:
            result = orchestrator.run_comparison(request, mode=mode)
            tracker.log_result(result)

            comparison = result.get("comparison", {})
            summary = {
                "iteration": index + 1,
                "timestamp_utc": _iso_now(),
                "objective_values": comparison.get("objective_values", {}),
                "best_objective_backend": comparison.get("best_objective_backend"),
                "quantum_vs_strong_classical_delta": comparison.get(
                    "quantum_vs_strong_classical_delta"
                ),
                "backends_succeeded": result.get("backends_succeeded", []),
                "backends_failed": result.get("backends_failed", []),
            }
            report["results"].append(summary)
            report["iterations_completed"] = index + 1
            print(
                f"[{index + 1}/{iterations}] Objectives: "
                f"{summary['objective_values']} "
                f"Delta: {summary['quantum_vs_strong_classical_delta']}"
            )
        except Exception as exc:  # pragma: no cover - runtime defensive path
            report["results"].append(
                {
                    "iteration": index + 1,
                    "timestamp_utc": _iso_now(),
                    "status": "error",
                    "error": str(exc),
                }
            )
            print(f"[{index + 1}/{iterations}] Error: {exc}")

        if index < iterations - 1 and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    report["status"] = "success"

    output_dir = repo_root / "reports" / "research" / "overnight_batches"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / (
        "overnight_batch_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["output_path"] = str(output_path)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--sleep", type=int, default=60, dest="sleep_seconds")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--mode", default="full", choices=("quick", "full"))
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    result = run_batch(
        iterations=args.iterations,
        sleep_seconds=args.sleep_seconds,
        repo_root=args.repo_root,
        mode=args.mode,
    )
    print(json.dumps(result, indent=2, default=str))
