"""Tests for the Qiskit Finance research backend."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from src.research.backends import qiskit_portfolio_optimizer as qpo


REPO_ROOT = Path(__file__).resolve().parents[2]


SAMPLE_REQUEST = {
    "candidates": [
        {"symbol": "AAPL", "expected_return": 0.08, "volatility": 0.25, "sector": "tech"},
        {"symbol": "MSFT", "expected_return": 0.07, "volatility": 0.22, "sector": "tech"},
        {"symbol": "XOM", "expected_return": 0.05, "volatility": 0.30, "sector": "energy"},
        {"symbol": "JPM", "expected_return": 0.06, "volatility": 0.20, "sector": "financials"},
    ],
    "constraints": {"budget": 2},
    "config": {"risk_factor": 0.5, "qaoa_reps": 1, "max_iterations": 5},
}


class _FakePortfolioOptimization:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def to_quadratic_program(self):
        return {"quadratic_program": True}


class _FakeCOBYLA:
    def __init__(self, maxiter):
        self.maxiter = maxiter


class _FakeSampler:
    pass


class _FakeQAOA:
    def __init__(self, sampler, optimizer, reps):
        self.sampler = sampler
        self.optimizer = optimizer
        self.reps = reps


class _FakeSolveResult:
    x = [1, 0, 1, 0]
    fval = 1.2345


class _FakeMinimumEigenOptimizer:
    def __init__(self, qaoa):
        self.qaoa = qaoa

    def solve(self, quadratic_program):
        return _FakeSolveResult()


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
    assert result["backend"] == "qiskit_finance"
    assert result["algorithm"] == "QAOA"
    assert result["execution_metadata"]["not_for_direct_execution"] is True
    assert result["execution_metadata"]["quantum_direct_execution_forbidden"] is True
    assert result["execution_metadata"]["bounded_secondary_signal_only"] is True
    assert result["execution_metadata"]["backend"] == "qiskit_finance"


def test_qiskit_optimizer_success_with_fake_stack(monkeypatch):
    monkeypatch.setattr(qpo, "QISKIT_AVAILABLE", True)
    monkeypatch.setattr(
        qpo, "PortfolioOptimization", _FakePortfolioOptimization, raising=False
    )
    monkeypatch.setattr(qpo, "COBYLA", _FakeCOBYLA, raising=False)
    monkeypatch.setattr(qpo, "QAOA", _FakeQAOA, raising=False)
    monkeypatch.setattr(
        qpo, "MinimumEigenOptimizer", _FakeMinimumEigenOptimizer, raising=False
    )
    monkeypatch.setattr(qpo, "_SAMPLER_CLS", _FakeSampler, raising=False)

    optimizer = qpo.QiskitPortfolioOptimizer({"max_iterations": 5})
    result = optimizer.optimize(SAMPLE_REQUEST)

    _assert_common_schema(result)
    assert result["status"] == "success"
    assert result["selected_candidates"] == ["AAPL", "XOM"]
    assert result["selected_indices"] == [0, 2]
    assert result["selection_vector"] == [1, 0, 1, 0]
    assert result["execution_metadata"]["num_assets"] == 4
    assert result["execution_metadata"]["budget"] == 2


def test_qiskit_module_main_gracefully_degrades_when_dependency_missing():
    code = textwrap.dedent(
        """
        import builtins
        import runpy

        real_import = builtins.__import__
        blocked = (
            "qiskit",
            "qiskit_finance",
            "qiskit_algorithms",
            "qiskit_optimization",
        )

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
                raise ImportError("forced missing dependency")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = fake_import
        runpy.run_module("src.research.backends.qiskit_portfolio_optimizer", run_name="__main__")
        """
    )

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    _assert_common_schema(payload)
    assert payload["status"] == "error"
    assert payload["selected_candidates"] == []
    assert payload["selected_indices"] == []
    assert payload["selection_vector"] == []
    assert payload["execution_metadata"]["qiskit_available"] is False
