"""Tests for the strong classical baseline backend."""

from __future__ import annotations

import json
import subprocess
import sys

from src.research.backends import classical_strong_baseline as csb


SAMPLE_REQUEST = {
    "candidates": [
        {"symbol": "AAPL", "expected_return": 0.08, "volatility": 0.25, "sector": "tech"},
        {"symbol": "MSFT", "expected_return": 0.07, "volatility": 0.22, "sector": "tech"},
        {"symbol": "XOM", "expected_return": 0.05, "volatility": 0.30, "sector": "energy"},
        {"symbol": "JPM", "expected_return": 0.06, "volatility": 0.20, "sector": "financials"},
    ],
    "constraints": {"budget": 2, "max_sector_pct": 0.5},
    "config": {"risk_factor": 0.5},
}


def _assert_common_schema(result: dict) -> None:
    assert {
        "backend",
        "algorithm",
        "status",
        "selected_candidates",
        "selected_indices",
        "selection_vector",
        "objective_value",
        "execution_metadata",
    }.issubset(result)
    assert result["backend"] == "classical_strong"
    assert result["algorithm"] == "markowitz_milp"
    assert result["execution_metadata"]["not_for_direct_execution"] is True
    assert result["execution_metadata"]["quantum_direct_execution_forbidden"] is True
    assert result["execution_metadata"]["bounded_secondary_signal_only"] is True
    assert result["execution_metadata"]["backend"] == "classical_strong"


def test_classical_strong_baseline_graceful_fallback_without_cvxpy(monkeypatch):
    monkeypatch.setattr(csb, "CVXPY_AVAILABLE", False)

    result = csb.ClassicalStrongBaseline().optimize(SAMPLE_REQUEST)

    _assert_common_schema(result)
    assert result["status"] == "error"
    assert result["reason"] == "cvxpy_not_available"
    assert result["selected_candidates"] == []
    assert result["selected_indices"] == []
    assert result["selection_vector"] == []


def test_classical_strong_baseline_standalone_outputs_json():
    proc = subprocess.run(
        [sys.executable, "-m", "src.research.backends.classical_strong_baseline"],
        cwd="/Users/mosestut/global-sentinel",
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    _assert_common_schema(payload)
