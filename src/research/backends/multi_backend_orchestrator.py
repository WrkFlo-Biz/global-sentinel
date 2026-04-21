#!/usr/bin/env python3
"""Multi-Backend Orchestrator — runs all quantum + classical research backends
on the same request and produces a unified comparison report.

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

from src.packets.quantum_optimization_result import QuantumOptimizationResult

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MultiBackendOrchestrator:
    """Run all available backends on the same request and compare results."""

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 artifact_dir: Optional[Path] = None):
        self.config = config or {}
        self.artifact_dir = artifact_dir or Path("reports/research/comparisons")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.backends: Dict[str, Any] = {}
        self.screening_pipeline = None
        self._load_backends()
        self._load_screening_pipeline()

    def _load_backends(self):
        # QPanda3 (primary)
        try:
            from src.research.quantum_optimizer_bridge import QuantumOptimizerBridge
            bridge_artifact = self.artifact_dir / "qpanda3"
            bridge_artifact.mkdir(parents=True, exist_ok=True)
            self.backends["qpanda3"] = QuantumOptimizerBridge(
                artifact_dir=bridge_artifact
            )
        except Exception as exc:
            logger.info("QPanda3 backend unavailable: %s", exc)
            self.backends["qpanda3"] = {"status": "unavailable", "reason": str(exc)}

        # Qiskit Finance
        try:
            from src.research.backends.qiskit_portfolio_optimizer import (
                QiskitPortfolioOptimizer,
            )
            self.backends["qiskit_finance"] = QiskitPortfolioOptimizer(
                self.config.get("qiskit", {})
            )
        except Exception as exc:
            logger.info("Qiskit Finance backend unavailable: %s", exc)
            self.backends["qiskit_finance"] = {"status": "unavailable", "reason": str(exc)}

        # PennyLane anomaly detection
        try:
            from src.research.backends.pennylane_anomaly_detector import (
                PennyLaneAnomalyDetector,
            )
            self.backends["pennylane_vqc"] = PennyLaneAnomalyDetector(
                self.config.get("pennylane", {})
            )
        except Exception as exc:
            logger.info("PennyLane backend unavailable: %s", exc)
            self.backends["pennylane_vqc"] = {"status": "unavailable", "reason": str(exc)}

        # Classical strong baseline (CVXPY Markowitz)
        try:
            from src.research.backends.classical_strong_baseline import (
                ClassicalStrongBaseline,
            )
            self.backends["classical_strong"] = ClassicalStrongBaseline()
        except Exception as exc:
            logger.info("Classical strong baseline unavailable: %s", exc)
            self.backends["classical_strong"] = {"status": "unavailable", "reason": str(exc)}

        # Existing greedy baseline
        try:
            from src.research.classical_optimizer_baseline import (
                ClassicalOptimizerBaseline,
            )
            self.backends["classical_greedy"] = ClassicalOptimizerBaseline()
        except Exception as exc:
            logger.info("Classical greedy baseline unavailable: %s", exc)
            self.backends["classical_greedy"] = {"status": "unavailable", "reason": str(exc)}

    def _load_screening_pipeline(self) -> None:
        try:
            from src.research.backends.anomaly_screening_pipeline import (
                AnomalyScreeningPipeline,
            )

            self.screening_pipeline = AnomalyScreeningPipeline(
                self.config.get("screening", {}),
                artifact_dir=self.artifact_dir / "screening",
            )
        except Exception as exc:
            logger.info("Anomaly screening pipeline unavailable: %s", exc)
            self.screening_pipeline = None

    def available_backends(self) -> Dict[str, str]:
        """Return {name: status} for each backend."""
        result = {}
        for name, backend in self.backends.items():
            if isinstance(backend, dict):
                result[name] = "unavailable"
            else:
                result[name] = "available"
        return result

    def run_comparison(self, request: dict, mode: str = "full") -> dict:
        """Run all available backends and produce a comparison report.

        Args:
            request: QuantumOptimizationRequest-compatible dict
            mode: "full" (all backends) or "quick" (primary + strong classical)
        """
        start = time.monotonic()
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        screening_report = self._screen_request(request)
        optimization_request = screening_report.get("optimization_request", request)

        results: Dict[str, Any] = {}
        attempted, succeeded, failed, unavailable = [], [], [], []

        if mode == "quick":
            targets = ["qpanda3", "classical_strong"]
        else:
            targets = list(self.backends.keys())

        for name in targets:
            backend = self.backends.get(name)
            if backend is None or isinstance(backend, dict):
                unavailable.append(name)
                continue

            attempted.append(name)
            try:
                if name == "pennylane_vqc":
                    # Anomaly detection takes different input shape
                    candidates = (
                        optimization_request.get("candidates")
                        or optimization_request.get("candidate_universe")
                        or []
                    )
                    batch = []
                    for c in candidates:
                        features = c.get("features") or [
                            c.get("score", 0), c.get("expected_return", 0),
                            c.get("volatility", 0.2), c.get("weight", 0),
                        ]
                        batch.append({
                            "candidate_id": c.get("symbol", "?"),
                            "features": features,
                        })
                    result = backend.score_batch(batch) if batch else []
                    results[name] = self._ensure_result_guardrails(name, {
                        "backend": "pennylane_vqc",
                        "status": "success",
                        "anomaly_scores": result,
                        "num_scored": len(result),
                        "execution_metadata": {
                            "not_for_direct_execution": True,
                            "quantum_direct_execution_forbidden": True,
                            "bounded_secondary_signal_only": True,
                            "backend": "pennylane_vqc",
                        },
                    })
                    succeeded.append(name)
                elif hasattr(backend, "run"):
                    r = backend.run(
                        self._build_quantum_request(optimization_request, request_hash)
                    )
                    results[name] = self._normalize_run_result(
                        name, r, optimization_request
                    )
                    if results[name].get("status") == "success":
                        succeeded.append(name)
                    else:
                        failed.append(name)
                elif hasattr(backend, "optimize"):
                    r = backend.optimize(optimization_request)
                    results[name] = self._ensure_result_guardrails(name, r)
                    if r.get("status") in ("success", "optimal", "optimal_inaccurate"):
                        succeeded.append(name)
                    else:
                        failed.append(name)
                else:
                    failed.append(name)
            except Exception as exc:
                failed.append(name)
                results[name] = self._ensure_result_guardrails(name, {
                    "status": "error",
                    "error": str(exc),
                })

        comparison = self._build_comparison(results)
        elapsed = time.monotonic() - start

        report = {
            "request_hash": request_hash,
            "backends_attempted": attempted,
            "backends_succeeded": succeeded,
            "backends_failed": failed,
            "backends_unavailable": unavailable,
            "results": results,
            "comparison": comparison,
            "preoptimization_screening": screening_report,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "orchestrator": "multi_backend",
                "mode": mode,
                "total_runtime_seconds": round(elapsed, 4),
                "timestamp_utc": _utc_now(),
                "request_hash": request_hash,
            },
        }

        # Persist artifact
        artifact_path = self.artifact_dir / f"comparison_{request_hash}.json"
        artifact_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Comparison artifact: %s", artifact_path)

        return report

    def _screen_request(self, request: dict) -> dict:
        if self.screening_pipeline is None:
            return {
                "status": "skipped",
                "reason": "screening_pipeline_unavailable",
                "optimization_request": request,
                "execution_metadata": self._execution_metadata(
                    "anomaly_screening_pipeline",
                    status="skipped",
                    reason="screening_pipeline_unavailable",
                ),
            }

        report = self.screening_pipeline.screen(request)
        if not isinstance(report, dict):
            return {
                "status": "error",
                "reason": "invalid_screening_report",
                "optimization_request": request,
                "execution_metadata": self._execution_metadata(
                    "anomaly_screening_pipeline",
                    status="error",
                    reason="invalid_screening_report",
                ),
            }
        report.setdefault("optimization_request", request)
        return self._ensure_result_guardrails(
            "anomaly_screening_pipeline",
            report,
        )

    def _build_quantum_request(self, request: dict, request_hash: str):
        from src.packets.quantum_optimization_request import QuantumOptimizationRequest

        return QuantumOptimizationRequest(
            request_id=request.get("request_id", request_hash),
            package_id=request.get("package_id", "comparison"),
            timestamp_utc=request.get("timestamp_utc", _utc_now()),
            runtime_flags=request.get("runtime_flags", {}),
            time_window_state=request.get("time_window_state", {}),
            regime_state=request.get("regime_state", {}),
            objective=request.get("objective", {}),
            constraints=request.get("constraints", {}),
            candidate_universe=request.get("candidates")
            or request.get("candidate_universe", []),
            market_microstructure=request.get("market_microstructure", {}),
            provenance=request.get("provenance", {}),
        )

    def _normalize_run_result(
        self,
        backend_name: str,
        result: Any,
        request: dict,
    ) -> dict:
        if isinstance(result, dict):
            return self._ensure_result_guardrails(backend_name, result)

        if isinstance(result, QuantumOptimizationResult):
            raw = result.to_dict()
        elif hasattr(result, "to_dict"):
            raw = result.to_dict()
        elif hasattr(result, "__dataclass_fields__"):
            from dataclasses import asdict
            raw = asdict(result)
        else:
            raise TypeError(
                f"Unsupported result type for backend {backend_name}: "
                f"{type(result).__name__}"
            )

        candidates = request.get("candidates") or request.get("candidate_universe") or []
        selected_symbols = [
            row.get("symbol", row.get("candidate_key", "?"))
            for row in raw.get("ranked_solutions", [])
        ]
        selection_vector = []
        selected_positions = {idx for idx, row in enumerate(candidates)
                              if row.get("symbol") in selected_symbols}
        for idx, _candidate in enumerate(candidates):
            selection_vector.append(1 if idx in selected_positions else 0)

        return self._ensure_result_guardrails(backend_name, {
            "backend": backend_name,
            "algorithm": raw.get("solver", "run_backend"),
            "status": "success" if raw.get("success") else "error",
            "selected_candidates": selected_symbols,
            "selected_indices": [
                idx for idx, selected in enumerate(selection_vector) if selected == 1
            ],
            "selection_vector": selection_vector,
            "objective_value": raw.get("objective_value"),
            "num_assets_input": len(candidates),
            "num_assets_selected": len(selected_symbols),
            "feasibility": raw.get("feasibility"),
            "diagnostics": raw.get("diagnostics", {}),
            "raw_result": raw,
            "execution_metadata": raw.get("diagnostics", {}).get(
                "execution_metadata",
                self._execution_metadata(
                    backend_name,
                    status="success" if raw.get("success") else "error",
                    solver=raw.get("solver", "run_backend"),
                ),
            ),
        })

    def _ensure_result_guardrails(self, backend_name: str, result: dict) -> dict:
        payload = dict(result)
        status = payload.get("status", "success")
        metadata = payload.get("execution_metadata")
        if not isinstance(metadata, dict):
            metadata = self._execution_metadata(backend_name, status=status)
        else:
            metadata = dict(metadata)
            metadata.setdefault("backend", backend_name)
            metadata.setdefault("status", status)
            metadata.setdefault("bounded_secondary_signal_only", True)
            metadata.setdefault("timestamp_utc", _utc_now())
        metadata["not_for_direct_execution"] = True
        metadata["quantum_direct_execution_forbidden"] = True
        payload["execution_metadata"] = metadata
        return payload

    def _execution_metadata(
        self,
        backend_name: str,
        *,
        status: str,
        **extra: Any,
    ) -> dict:
        metadata = {
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "backend": backend_name,
            "status": status,
            "timestamp_utc": _utc_now(),
        }
        metadata.update(extra)
        return metadata

    def _build_comparison(self, results: Dict[str, Any]) -> dict:
        objective_values: Dict[str, float] = {}
        runtimes: Dict[str, float] = {}
        selections: Dict[str, list] = {}

        for name, r in results.items():
            if not isinstance(r, dict):
                continue
            if r.get("objective_value") is not None:
                objective_values[name] = float(r["objective_value"])
            meta = r.get("execution_metadata", {})
            if isinstance(meta, dict) and meta.get("runtime_seconds"):
                runtimes[name] = meta["runtime_seconds"]
            if r.get("selection_vector"):
                selections[name] = r["selection_vector"]

        # Selection overlap
        overlap: Dict[str, float] = {}
        names = list(selections.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = selections[names[i]], selections[names[j]]
                if len(a) == len(b) and len(a) > 0:
                    match = sum(1 for x, y in zip(a, b) if x == y) / len(a)
                    overlap[f"{names[i]}_vs_{names[j]}"] = round(match, 4)

        # Quantum vs strong classical delta
        q_delta = None
        for q in ("qpanda3", "qiskit_finance"):
            if q in objective_values and "classical_strong" in objective_values:
                q_delta = round(
                    objective_values[q] - objective_values["classical_strong"], 6
                )
                break

        best = max(objective_values, key=objective_values.get) if objective_values else None

        return {
            "objective_values": objective_values,
            "runtime_seconds": runtimes,
            "selection_overlap": overlap,
            "best_objective_backend": best,
            "quantum_vs_strong_classical_delta": q_delta,
        }


if __name__ == "__main__":
    orch = MultiBackendOrchestrator()
    print("Available backends:", orch.available_backends())
    test = {
        "request_id": "orch-test-001",
        "candidates": [
            {"symbol": "SPY", "expected_return": 0.06, "volatility": 0.18, "sector": "index", "score": 0.7},
            {"symbol": "GLD", "expected_return": 0.03, "volatility": 0.15, "sector": "commodity", "score": 0.5},
            {"symbol": "TLT", "expected_return": 0.04, "volatility": 0.12, "sector": "bonds", "score": 0.6},
            {"symbol": "XLE", "expected_return": 0.07, "volatility": 0.28, "sector": "energy", "score": 0.55},
        ],
        "constraints": {"budget": 2},
        "objective": {"type": "portfolio_optimization"},
        "config": {"risk_factor": 0.5},
    }
    report = orch.run_comparison(test, mode="full")
    print(json.dumps({
        "succeeded": report["backends_succeeded"],
        "failed": report["backends_failed"],
        "unavailable": report["backends_unavailable"],
        "comparison": report["comparison"],
    }, indent=2, default=str))
