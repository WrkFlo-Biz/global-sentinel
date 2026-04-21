"""Tests for the multi-backend research orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.research.backends.anomaly_screening_pipeline import AnomalyScreeningPipeline
from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
from src.research.classical_optimizer_baseline import ClassicalOptimizerBaseline


REQUEST = {
    "request_id": "orch-test-001",
    "package_id": "pkg-quantum-lane",
    "objective": {"type": "portfolio_optimization"},
    "constraints": {"budget": 2, "max_names": 2, "max_sector_weight": 0.5},
    "config": {"risk_factor": 0.5},
    "regime_state": {
        "regime_shift_probability": 0.8,
        "macro_state": "inflationary_stress",
        "geopolitical_state": "heightened",
    },
    "market_microstructure": {
        "XLE": {"adv_shares": 40_000_000, "sigma_daily": 0.03},
        "XLU": {"adv_shares": 18_000_000, "sigma_daily": 0.02},
        "QQQ": {"adv_shares": 60_000_000, "sigma_daily": 0.04},
    },
    "candidates": [
        {
            "symbol": "XLE",
            "score": 0.72,
            "expected_return": 0.08,
            "volatility": 0.28,
            "sector": "energy",
            "theme": "energy",
        },
        {
            "symbol": "XLU",
            "score": 0.65,
            "expected_return": 0.05,
            "volatility": 0.16,
            "sector": "utilities",
            "theme": "utilities",
        },
        {
            "symbol": "QQQ",
            "score": 0.61,
            "expected_return": 0.07,
            "volatility": 0.24,
            "sector": "tech",
            "theme": "tech",
        },
    ],
}


class _FakeRunBackend:
    def __init__(self, backend_name: str, objective_value: float, selection_vector: list[int]):
        self.backend_name = backend_name
        self.objective_value = objective_value
        self.selection_vector = selection_vector

    def run(self, request):
        candidates = request.candidate_universe
        selected_indices = [
            idx for idx, selected in enumerate(self.selection_vector) if selected == 1
        ]
        return {
            "backend": self.backend_name,
            "algorithm": "shadow_research",
            "status": "success",
            "selected_candidates": [
                candidates[idx]["symbol"] for idx in selected_indices
            ],
            "selected_indices": selected_indices,
            "selection_vector": self.selection_vector,
            "objective_value": self.objective_value,
            "execution_metadata": {
                "backend": self.backend_name,
                "status": "success",
            },
        }


class _FakeOptimizeBackend:
    def optimize(self, request: dict):
        return {
            "backend": "classical_strong",
            "algorithm": "markowitz_milp",
            "status": "success",
            "selected_candidates": ["XLE", "XLU"],
            "selected_indices": [0, 1],
            "selection_vector": [1, 1, 0],
            "objective_value": 1.3,
            "execution_metadata": {
                "backend": "classical_strong",
                "status": "success",
            },
        }


class _FakeDetector:
    def score_batch(self, batch: list[dict]) -> list[dict]:
        results = []
        for row in batch:
            candidate_id = row["candidate_id"]
            score = 0.18 if candidate_id == "QQQ" else 0.82
            results.append(
                {
                    "candidate_id": candidate_id,
                    "quantum_anomaly_score": score,
                    "quantum_raw_expectation": (score * 2.0) - 1.0,
                    "classical_anomaly_score": score,
                    "is_anomaly_quantum": score < 0.3,
                    "is_anomaly_classical": score < 0.3,
                    "anomaly_agreement": True,
                    "threshold": 0.3,
                    "execution_metadata": {"backend": "pennylane_vqc"},
                }
            )
        return sorted(results, key=lambda row: row["quantum_anomaly_score"])


def test_orchestrator_loads_classical_greedy_from_correct_module(tmp_path):
    orchestrator = MultiBackendOrchestrator(artifact_dir=tmp_path)

    assert orchestrator.available_backends()["classical_greedy"] == "available"
    assert isinstance(orchestrator.backends["classical_greedy"], ClassicalOptimizerBaseline)
    assert orchestrator.backends["classical_greedy"].__class__.__module__ == (
        "src.research.classical_optimizer_baseline"
    )


def test_orchestrator_report_schema_guardrails_and_dependency_degradation(tmp_path):
    orchestrator = MultiBackendOrchestrator(artifact_dir=tmp_path)
    classical_greedy = orchestrator.backends["classical_greedy"]

    orchestrator.backends = {
        "qpanda3": _FakeRunBackend("qpanda3", 1.5, [1, 0, 1]),
        "qiskit_finance": {"status": "unavailable", "reason": "forced missing dependency"},
        "pennylane_vqc": {"status": "unavailable", "reason": "forced missing dependency"},
        "classical_strong": _FakeOptimizeBackend(),
        "classical_greedy": classical_greedy,
    }
    orchestrator.screening_pipeline = AnomalyScreeningPipeline(
        detector=_FakeDetector(),
        artifact_dir=tmp_path / "screening",
    )

    report = orchestrator.run_comparison(REQUEST, mode="full")

    assert {
        "request_hash",
        "backends_attempted",
        "backends_succeeded",
        "backends_unavailable",
        "results",
        "comparison",
        "preoptimization_screening",
        "execution_metadata",
    }.issubset(report)
    assert report["execution_metadata"]["not_for_direct_execution"] is True
    assert report["execution_metadata"]["quantum_direct_execution_forbidden"] is True
    assert "qiskit_finance" in report["backends_unavailable"]
    assert "pennylane_vqc" in report["backends_unavailable"]

    screening = report["preoptimization_screening"]
    assert screening["status"] == "success"
    assert screening["execution_metadata"]["not_for_direct_execution"] is True
    assert len(screening["candidate_universe"]) == len(REQUEST["candidates"])
    assert any(
        candidate["metadata"]["anomaly_screening"].get("is_anomaly_quantum")
        for candidate in screening["candidate_universe"]
    )

    screening_artifact = Path(screening["execution_metadata"]["artifact_path"])
    assert screening_artifact.is_file()
    comparison_artifacts = list(tmp_path.glob("comparison_*.json"))
    assert comparison_artifacts

    assert report["results"]["classical_greedy"]["execution_metadata"][
        "not_for_direct_execution"
    ] is True
    assert report["results"]["classical_strong"]["execution_metadata"][
        "not_for_direct_execution"
    ] is True
