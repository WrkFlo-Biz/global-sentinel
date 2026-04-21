#!/usr/bin/env python3
"""Quantum research job orchestrator.

End-to-end runner for the Azure Container Apps Job:
1. Load request JSON
2. Run classical baseline
3. Run quantum bridge
4. Write both artifact JSONs
5. Write comparison summary

Usage:
    python -m src.research.run_quantum_research_job \
        --request-json artifacts/incoming/request.json \
        --quantum-artifact-dir artifacts/quantum \
        --classical-artifact-dir artifacts/classical \
        --comparison-out reports/research/quantum_vs_classical_latest.json
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import logging
import sys
from pathlib import Path

from src.packets.quantum_optimization_request import QuantumOptimizationRequest
from src.research.classical_optimizer_baseline import ClassicalOptimizerBaseline
from src.research.quantum_optimization_result_handler import QuantumResultHandler

logger = logging.getLogger(__name__)


def load_request(path: Path) -> QuantumOptimizationRequest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return QuantumOptimizationRequest(**raw)


def parse_args():
    p = argparse.ArgumentParser(description="Run quantum vs classical research job")
    p.add_argument("--request-json", required=True, help="Path to request JSON")
    p.add_argument("--quantum-artifact-dir", default="artifacts/quantum")
    p.add_argument("--classical-artifact-dir", default="artifacts/classical")
    p.add_argument("--comparison-out", default="reports/research/quantum_vs_classical_latest.json")
    p.add_argument(
        "--mode",
        choices=["single", "full_comparison"],
        default="single",
        help="single=QPanda3 bridge only; full_comparison=run all research backends via orchestrator",
    )
    return p.parse_args()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _result_to_dict(result):
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return dict(result)
    raise TypeError(f"Unsupported result payload type: {type(result).__name__}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = parse_args()

    request_path = Path(args.request_json)
    if not request_path.exists():
        logger.error("Request JSON not found: %s", request_path)
        sys.exit(1)

    req = load_request(request_path)
    result_handler = QuantumResultHandler(_repo_root())
    result_handler.artifacts_dir = Path(args.quantum_artifact_dir)
    result_handler.classical_dir = Path(args.classical_artifact_dir)
    logger.info("Loaded request %s (package=%s, %d candidates)",
                req.request_id, req.package_id, len(req.candidate_universe))

    if args.mode == "full_comparison":
        _run_full_comparison(req, args, result_handler)
    else:
        _run_single(req, args, result_handler)


def _run_full_comparison(req, args, result_handler):
    """Run all research backends via MultiBackendOrchestrator."""
    from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator

    artifact_dir = Path(args.quantum_artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    orchestrator = MultiBackendOrchestrator(artifact_dir=artifact_dir)

    avail = orchestrator.available_backends()
    logger.info("Multi-backend orchestrator: %s", avail)

    raw_request = req.to_dict() if hasattr(req, "to_dict") else asdict(req)
    report = orchestrator.run_comparison(raw_request, mode="full")

    out = Path(args.comparison_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info(
        "Full comparison written: %s (backends succeeded: %s)",
        out,
        report.get("backends_succeeded", []),
    )
    print(json.dumps(report, indent=2, default=str))


def _run_single(req, args, result_handler):
    """Original single-backend QPanda3 bridge + classical baseline."""
    # Step 1: Classical baseline
    logger.info("Running classical baseline...")
    classical = ClassicalOptimizerBaseline().run(req)
    classical_payload = classical.to_dict()
    classical_payload["execution_metadata"] = {
        "framework_standard": "classical_baseline",
        "provider_name": "classical",
        "backend_type": "classical",
        "backend_name": "classical_baseline",
        "algorithm_family": "classical_baseline",
        "formulation_id": str(req.objective.get("type", "unregistered")),
        "artifact_only": True,
        "research_only": True,
    }
    cpath = result_handler.store_classical_baseline(classical_payload)
    logger.info("Classical result: objective=%.4f, %d solutions -> %s",
                classical.objective_value, len(classical.ranked_solutions), cpath)

    # Step 2: Quantum bridge
    logger.info("Running quantum bridge...")
    quantum = None
    qpath = None
    quantum_execution_metadata = {
        "framework_standard": "classical_fallback",
        "provider_name": "classical",
        "backend_type": "classical",
        "backend_name": "classical_fallback",
        "algorithm_family": "fallback",
        "formulation_id": str(req.objective.get("type", "unregistered")),
        "artifact_only": True,
        "research_only": True,
    }
    try:
        from src.research.quantum_optimizer_bridge import QuantumOptimizerBridge
        qbridge = QuantumOptimizerBridge(Path(args.quantum_artifact_dir))
        quantum = qbridge.run(req)
        quantum_payload = _result_to_dict(quantum)
        qpath = result_handler.store_result(quantum_payload)
        quantum_objective = quantum.objective_value
        quantum_solver = quantum.solver
        quantum_count = len(quantum.ranked_solutions)
        quantum_execution_metadata = (
            quantum.diagnostics.get("execution_metadata")
            or quantum_payload.get("execution_metadata")
            or quantum_execution_metadata
        )
        logger.info("Quantum result: objective=%.4f, %d solutions",
                    quantum_objective, quantum_count)
    except Exception as exc:
        logger.warning("Quantum bridge failed, using classical as fallback: %s", exc)
        quantum_objective = classical.objective_value
        quantum_solver = "classical_fallback"
        quantum_count = len(classical.ranked_solutions)

    # Step 3: Comparison summary
    comparison = {
        "request_id": req.request_id,
        "package_id": req.package_id,
        "classical_solver": classical.solver,
        "quantum_solver": quantum_solver,
        "classical_objective_value": classical.objective_value,
        "quantum_objective_value": quantum_objective,
        "objective_delta": quantum_objective - classical.objective_value,
        "relative_improvement": (
            (quantum_objective - classical.objective_value) / abs(classical.objective_value)
            if classical.objective_value != 0 else 0.0
        ),
        "classical_count": len(classical.ranked_solutions),
        "quantum_count": quantum_count,
        "artifact_only": True,
        "research_only": True,
        "not_for_direct_execution": True,
        "promotion_allowed": False,
        "classical_artifact_path": str(cpath),
        "quantum_artifact_path": str(qpath) if qpath else None,
        "classical_execution_metadata": classical_payload["execution_metadata"],
        "quantum_execution_metadata": quantum_execution_metadata,
        "framework_standard": quantum_execution_metadata.get("framework_standard", "classical_fallback"),
        "provider_name": quantum_execution_metadata.get("provider_name", "classical"),
        "backend_type": quantum_execution_metadata.get("backend_type", "classical"),
        "hardware_job_id": quantum_execution_metadata.get("hardware_job_id", ""),
        "job_submission_mode": quantum_execution_metadata.get("job_submission_mode", ""),
        "async_submission": bool(quantum_execution_metadata.get("async_submission", False)),
        "note": "Extend with slippage-adjusted trade outcome comparison downstream.",
    }

    out = Path(args.comparison_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    logger.info("Comparison written: %s (delta=%.4f)", out, comparison["objective_delta"])
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
