#!/usr/bin/env python3
"""Qiskit Finance QAOA Portfolio Optimizer — research backend for Global Sentinel.

Accepts QuantumOptimizationRequest-compatible dicts, returns results in the same
format as quantum_optimizer_bridge.py. ALL outputs are artifact-only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional in test env
    np = None  # type: ignore[assignment]

try:
    from qiskit_finance.applications.optimization import PortfolioOptimization
    from qiskit_algorithms import QAOA
    from qiskit_algorithms.optimizers import COBYLA
    from qiskit_optimization.algorithms import MinimumEigenOptimizer

    _SAMPLER_CLS = None
    try:
        from qiskit.primitives import StatevectorSampler as _SamplerCls
        _SAMPLER_CLS = _SamplerCls
    except ImportError:
        from qiskit.primitives import Sampler as _SamplerCls
        _SAMPLER_CLS = _SamplerCls

    QISKIT_AVAILABLE = True
except ImportError:
    QISKIT_AVAILABLE = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_id(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _standalone_error(message: str) -> dict:
    return {
        "backend": "qiskit_finance",
        "algorithm": "QAOA",
        "status": "error",
        "error": message,
        "selected_candidates": [],
        "selected_indices": [],
        "selection_vector": [],
        "objective_value": None,
        "execution_metadata": {
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "backend": "qiskit_finance",
            "algorithm": "QAOA",
            "status": "error",
            "timestamp_utc": _utc_now(),
            "runtime_seconds": 0.0,
            "qiskit_available": QISKIT_AVAILABLE,
        },
    }


class QiskitPortfolioOptimizer:
    """Qiskit Finance QAOA portfolio optimizer (research-only)."""

    MAX_ASSETS = 12  # QAOA on CPU simulator scales poorly beyond this

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.risk_factor = self.config.get("risk_factor", 0.5)
        self.max_iterations = self.config.get("max_iterations", 100)
        if not QISKIT_AVAILABLE:
            raise ImportError(
                "qiskit-finance stack not available. "
                "pip install qiskit-finance qiskit-algorithms qiskit-optimization"
            )

    def optimize(self, request: dict) -> dict:
        start = time.monotonic()
        candidates = request.get("candidates") or request.get("candidate_universe") or []
        constraints = request.get("constraints", {})
        config = request.get("config", {})
        n = len(candidates)

        if n == 0:
            return self._skip("no_candidates", start)
        if n > self.MAX_ASSETS:
            return self._skip(
                f"too_many_candidates_{n}_max_{self.MAX_ASSETS}", start
            )

        expected_returns = _as_vector([
            c.get("expected_return", c.get("score", 0.0)) for c in candidates
        ])
        cov = self._build_covariance(candidates, n)
        budget = min(constraints.get("budget", max(1, n // 2)), n)
        risk_factor = config.get("risk_factor", self.risk_factor)

        try:
            portfolio = PortfolioOptimization(
                expected_returns=expected_returns,
                covariances=cov,
                risk_factor=risk_factor,
                budget=budget,
            )
            qp = portfolio.to_quadratic_program()
            cobyla = COBYLA(maxiter=config.get("max_iterations", self.max_iterations))
            sampler = _SAMPLER_CLS()
            qaoa = QAOA(sampler=sampler, optimizer=cobyla,
                        reps=config.get("qaoa_reps", 1))
            optimizer = MinimumEigenOptimizer(qaoa)
            result = optimizer.solve(qp)
            elapsed = time.monotonic() - start

            selection = [int(x) for x in result.x]
            selected = [
                candidates[i].get("symbol", f"C{i}")
                for i in range(n) if selection[i] == 1
            ]

            return {
                "backend": "qiskit_finance",
                "algorithm": "QAOA",
                "status": "success",
                "selected_candidates": selected,
                "selected_indices": [i for i in range(n) if selection[i] == 1],
                "selection_vector": selection,
                "objective_value": float(result.fval),
                "num_assets_input": n,
                "num_assets_selected": sum(selection),
                "budget": budget,
                "risk_factor": risk_factor,
                "execution_metadata": self._meta(
                    status="success", elapsed=elapsed, num_assets=n, budget=budget,
                    risk_factor=risk_factor, selection=selection,
                    qaoa_reps=config.get("qaoa_reps", 1),
                    max_iterations=config.get("max_iterations", self.max_iterations),
                ),
            }
        except Exception as exc:
            logger.warning("Qiskit QAOA failed: %s", exc, exc_info=True)
            elapsed = time.monotonic() - start
            return {
                "backend": "qiskit_finance", "algorithm": "QAOA",
                "status": "error", "error": str(exc),
                "selected_candidates": [], "selected_indices": [],
                "selection_vector": [], "objective_value": None,
                "execution_metadata": self._meta(
                    status="error", elapsed=elapsed, error=str(exc)
                ),
            }

    # ------------------------------------------------------------------
    def _build_covariance(self, candidates: list, n: int):
        if candidates[0].get("covariance_row"):
            return _as_matrix([c["covariance_row"] for c in candidates])
        vols = [float(c.get("volatility", 0.2)) for c in candidates]
        cov = [[0.0 for _col in range(n)] for _row in range(n)]
        for i in range(n):
            cov[i][i] = vols[i] ** 2
            for j in range(i + 1, n):
                corr = 0.3 if candidates[i].get("sector") == candidates[j].get("sector") else 0.1
                cov[i][j] = corr * vols[i] * vols[j]
                cov[j][i] = cov[i][j]
        return _as_matrix(cov)

    def _meta(self, *, status: str, elapsed: float = 0, **extra) -> dict:
        m = {
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "backend": "qiskit_finance",
            "algorithm": "QAOA",
            "optimizer": "COBYLA",
            "status": status,
            "runtime_seconds": round(elapsed, 4),
            "timestamp_utc": _utc_now(),
            "artifact_id": _artifact_id({"b": "qiskit", "t": _utc_now(), "s": status}),
            "qiskit_available": True,
        }
        m.update(extra)
        return m

    def _skip(self, reason: str, start: float) -> dict:
        elapsed = time.monotonic() - start
        return {
            "backend": "qiskit_finance", "algorithm": "QAOA",
            "status": "skipped", "reason": reason,
            "selected_candidates": [], "selected_indices": [],
            "selection_vector": [], "objective_value": None,
            "execution_metadata": self._meta(
                status="skipped", elapsed=elapsed, reason=reason
            ),
        }


def _as_vector(values: list[Any]) -> Any:
    normalized = [float(value) for value in values]
    if np is not None:
        return np.array(normalized, dtype=float)
    return normalized


def _as_matrix(rows: list[list[Any]]) -> Any:
    normalized = [[float(value) for value in row] for row in rows]
    if np is not None:
        return np.array(normalized, dtype=float)
    return normalized


if __name__ == "__main__":
    test = {
        "candidates": [
            {"symbol": "AAPL", "expected_return": 0.08, "volatility": 0.25, "sector": "tech"},
            {"symbol": "MSFT", "expected_return": 0.07, "volatility": 0.22, "sector": "tech"},
            {"symbol": "XOM", "expected_return": 0.05, "volatility": 0.30, "sector": "energy"},
            {"symbol": "JPM", "expected_return": 0.06, "volatility": 0.20, "sector": "financials"},
        ],
        "constraints": {"budget": 2},
        "config": {"risk_factor": 0.5, "qaoa_reps": 1, "max_iterations": 50},
    }
    try:
        opt = QiskitPortfolioOptimizer()
        result = opt.optimize(test)
    except Exception as exc:  # pragma: no cover - exercised via subprocess
        result = _standalone_error(str(exc))
    print(json.dumps(result, indent=2, default=str))
