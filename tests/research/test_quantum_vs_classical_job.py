"""Tests for the quantum vs classical research job flow."""

import json
import sys
from pathlib import Path

from src.packets.quantum_optimization_request import QuantumOptimizationRequest
from src.research.classical_optimizer_baseline import ClassicalOptimizerBaseline
from src.research.evaluate_trade_outcomes import evaluate
from src.research.research_score_writer import build_research_score
from src.research.run_quantum_research_job import main as run_quantum_job_main


SAMPLE_REQUEST = QuantumOptimizationRequest(
    request_id="qreq-test-001",
    package_id="pkg-test-001",
    timestamp_utc="2026-03-07T18:00:00Z",
    runtime_flags={"shadow_mode_only": True},
    time_window_state={"window": "overnight", "impact_multiplier": 1.0},
    regime_state={"regime_shift_probability": 0.61, "macro_state": "mixed"},
    objective={"type": "hedge_basket_optimization"},
    constraints={"max_names": 4, "max_sector_weight": 0.50},
    candidate_universe=[
        {"symbol": "XOM", "score": 0.91, "sector": "Energy", "direction": "long"},
        {"symbol": "LMT", "score": 0.89, "sector": "Defense", "direction": "long"},
        {"symbol": "GLD", "score": 0.80, "sector": "Metals", "direction": "long"},
        {"symbol": "TLT", "score": 0.78, "sector": "Rates", "direction": "long"},
        {"symbol": "CAT", "score": 0.40, "sector": "Industrials", "direction": "long"},
    ],
    market_microstructure={},
    provenance={"source_snapshot_id": "snap-test"},
)


def test_classical_baseline_produces_result():
    result = ClassicalOptimizerBaseline().run(SAMPLE_REQUEST)
    assert result.success is True
    assert result.solver == "classical_baseline"
    assert len(result.ranked_solutions) <= 4
    assert result.objective_value > 0


def test_quantum_bridge_produces_result(tmp_path: Path):
    from src.research.quantum_optimizer_bridge import QuantumOptimizerBridge
    artifact_dir = tmp_path / "quantum"
    result = QuantumOptimizerBridge(artifact_dir).run(SAMPLE_REQUEST)
    assert result.success is True
    assert len(result.ranked_solutions) > 0
    assert result.diagnostics["framework_standard"] == "pyqpanda3"
    assert result.diagnostics["qpanda3_runtime_settings"]["framework_standard"] == "pyqpanda3"

    artifact_payload = json.loads(
        (artifact_dir / f"{SAMPLE_REQUEST.request_id}.json").read_text(encoding="utf-8")
    )
    assert artifact_payload["execution_metadata"]["framework_standard"] == "pyqpanda3"
    assert artifact_payload["execution_metadata"]["artifact_only"] is True
    assert artifact_payload["execution_metadata"]["not_for_direct_execution"] is True


def test_evaluate_trade_outcomes():
    classical = ClassicalOptimizerBaseline().run(SAMPLE_REQUEST)
    # Simulate quantum result same as classical for this test
    quantum_result = classical

    outcomes = {
        "trades": [
            {"symbol": "XOM", "trade_executed": True, "realized_return_bps": 120},
            {"symbol": "LMT", "trade_executed": True, "realized_return_bps": -30},
            {"symbol": "GLD", "trade_executed": True, "realized_return_bps": 80},
            {"symbol": "TLT", "trade_executed": False, "realized_return_bps": 0},
        ]
    }

    result = evaluate(
        classical_result=classical.to_dict(),
        quantum_result=quantum_result.to_dict(),
        trade_outcomes=outcomes,
    )

    assert result["winner"] == "tie"
    assert result["classical_overlap_score"] > 0
    assert "request_id" in result


def test_research_score_bounded():
    evaluation = {
        "request_id": "test-001",
        "package_id": "pkg-test",
        "quantum_overlap_score": 0.75,
        "quantum_directional_score": 0.80,
        "classical_overlap_score": 0.60,
        "classical_directional_score": 0.65,
        "quantum_realized_return_bps_sum": 250,
        "classical_realized_return_bps_sum": 150,
    }

    score = build_research_score(evaluation)

    assert 0.0 <= score["research_score"] <= 1.0
    assert score["guardrails"]["not_for_direct_execution"] is True
    assert score["recommended_influence"] in ("none", "research_positive", "research_neutral_positive", "research_negative")
    assert score["components"]["realized_return_bps_delta_quantum_minus_classical"] == 100


def test_run_quantum_research_job_writes_metadata(tmp_path: Path, monkeypatch):
    request_path = tmp_path / "request.json"
    quantum_dir = tmp_path / "quantum"
    classical_dir = tmp_path / "classical"
    comparison_out = tmp_path / "comparison.json"
    request_path.write_text(json.dumps(SAMPLE_REQUEST.to_dict(), indent=2), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_quantum_research_job",
            "--request-json",
            str(request_path),
            "--quantum-artifact-dir",
            str(quantum_dir),
            "--classical-artifact-dir",
            str(classical_dir),
            "--comparison-out",
            str(comparison_out),
        ],
    )

    run_quantum_job_main()

    comparison = json.loads(comparison_out.read_text(encoding="utf-8"))
    assert comparison["artifact_only"] is True
    assert comparison["research_only"] is True
    assert comparison["framework_standard"] in {"pyqpanda3", "classical_fallback"}
    assert "quantum_execution_metadata" in comparison

    stored_quantum = sorted(quantum_dir.glob("quantum_*.json"))
    stored_classical = sorted(classical_dir.glob("classical_*.json"))
    assert stored_quantum
    assert stored_classical

    quantum_payload = json.loads(stored_quantum[-1].read_text(encoding="utf-8"))
    classical_payload = json.loads(stored_classical[-1].read_text(encoding="utf-8"))
    assert quantum_payload["artifact_only"] is True
    assert classical_payload["execution_metadata"]["framework_standard"] == "classical_baseline"
