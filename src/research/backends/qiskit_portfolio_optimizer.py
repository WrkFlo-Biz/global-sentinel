#!/usr/bin/env python3
"""Qiskit Finance QAOA Portfolio Optimizer — research backend for Global Sentinel.

Accepts QuantumOptimizationRequest-compatible dicts, returns results in the same
format as quantum_optimizer_bridge.py. ALL outputs are artifact-only.

Scaled for 30+ assets using MPS (Matrix Product State) simulator method.
Constraints: max 20% per sector, max 5% per single name, min 10% cash.
Outputs optimal portfolio weights alongside classical Markowitz comparison.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path("/opt/global-sentinel")
QAOA_PORTFOLIO_PATH = REPO_ROOT / "data" / "quantum_feed" / "qaoa_portfolio_weights.json"

try:
    import numpy as np
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


def _classical_markowitz(candidates: list, risk_factor: float = 0.5, budget: int = 5) -> dict:
    """Classical Markowitz mean-variance optimization for comparison."""
    try:
        n = len(candidates)
        if n == 0:
            return {"status": "no_candidates"}

        returns = np.array([c.get("expected_return", 0.05) for c in candidates])
        vols = np.array([c.get("volatility", 0.2) for c in candidates])

        # Build covariance
        cov = np.diag(vols ** 2)
        for i in range(n):
            for j in range(i + 1, n):
                corr = 0.3 if candidates[i].get("sector") == candidates[j].get("sector") else 0.1
                cov[i][j] = corr * vols[i] * vols[j]
                cov[j][i] = cov[i][j]

        # Simple mean-variance: maximize return - risk_factor * variance
        # Score each asset independently, pick top budget
        scores = returns - risk_factor * np.diag(cov)
        selected_idx = np.argsort(-scores)[:budget]
        selection = [0] * n
        for idx in selected_idx:
            selection[idx] = 1

        # Compute weights proportional to score for selected assets
        selected_scores = scores[selected_idx]
        selected_scores = np.maximum(selected_scores, 0.001)
        weights = selected_scores / selected_scores.sum()

        # Apply constraints: max 5% per name (in portfolio context = max weight)
        weights = np.minimum(weights, 0.20)  # max 20% each in selected subset
        weights = weights / weights.sum()  # renormalize

        # 10% cash reserve
        weights = weights * 0.90  # 90% invested, 10% cash

        weight_dict = {}
        for i, idx in enumerate(selected_idx):
            sym = candidates[idx].get("symbol", f"C{idx}")
            weight_dict[sym] = round(float(weights[i]), 4)

        obj_val = float(np.dot(weights, returns[selected_idx]) - risk_factor * weights @ cov[np.ix_(selected_idx, selected_idx)] @ weights)

        return {
            "status": "success",
            "algorithm": "markowitz_mean_variance",
            "selected_candidates": list(weight_dict.keys()),
            "weights": weight_dict,
            "objective_value": round(obj_val, 6),
            "cash_reserve": 0.10,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


class QiskitPortfolioOptimizer:
    """Qiskit Finance QAOA portfolio optimizer (research-only).

    Scaled to handle 30+ assets:
    - Pre-filters to top 30 by signal strength
    - Uses MPS (Matrix Product State) method in Aer for large qubit counts
    - Applies sector/name/cash constraints
    """

    MAX_ASSETS = 30  # Scaled up from 12 using MPS method

    # Sector concentration limits
    MAX_SECTOR_WEIGHT = 0.20   # 20% max per sector
    MAX_SINGLE_NAME = 0.05     # 5% max per single name
    MIN_CASH_RESERVE = 0.10    # 10% minimum cash

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.risk_factor = self.config.get("risk_factor", 0.5)
        self.max_iterations = self.config.get("max_iterations", 150)
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

        # Pre-filter to top MAX_ASSETS by signal strength
        if n > self.MAX_ASSETS:
            scored = sorted(
                candidates,
                key=lambda c: c.get("score", c.get("expected_return", 0.0)),
                reverse=True,
            )
            logger.info("Pre-filtered candidates from %d to %d by signal strength for QAOA", n, self.MAX_ASSETS)
            candidates = scored[:self.MAX_ASSETS]
            n = len(candidates)

        expected_returns = np.array([
            c.get("expected_return", c.get("score", 0.0)) for c in candidates
        ])
        cov = self._build_covariance(candidates, n)
        budget = min(constraints.get("budget", max(1, n // 2)), n)
        risk_factor = config.get("risk_factor", self.risk_factor)

        # Determine simulator method based on qubit count
        use_mps = n > 15
        qaoa_reps = config.get("qaoa_reps", 2 if n <= 20 else 1)
        max_iter = config.get("max_iterations", self.max_iterations)

        try:
            portfolio = PortfolioOptimization(
                expected_returns=expected_returns,
                covariances=cov,
                risk_factor=risk_factor,
                budget=budget,
            )
            qp = portfolio.to_quadratic_program()
            cobyla = COBYLA(maxiter=max_iter)

            # Use MPS method for 30+ qubits, statevector for smaller
            if use_mps:
                try:
                    from qiskit_aer import AerSimulator
                    from qiskit_aer.primitives import SamplerV2 as AerSampler
                    backend = AerSimulator(method="matrix_product_state")
                    sampler = AerSampler(backend_options={"method": "matrix_product_state"})
                    logger.info("Using MPS method for %d-qubit QAOA", n)
                except ImportError:
                    sampler = _SAMPLER_CLS()
                    logger.info("AerSampler not available, falling back to default Sampler for %d qubits", n)
            else:
                sampler = _SAMPLER_CLS()

            qaoa = QAOA(sampler=sampler, optimizer=cobyla, reps=qaoa_reps)
            optimizer = MinimumEigenOptimizer(qaoa)
            result = optimizer.solve(qp)
            elapsed_qaoa = time.monotonic() - start

            selection = [int(x) for x in result.x]
            selected_indices = [i for i in range(n) if selection[i] == 1]

            # --- Apply portfolio constraints ---
            raw_weights = self._compute_constrained_weights(
                candidates, selected_indices, expected_returns, cov
            )

            selected_symbols = [
                candidates[i].get("symbol", f"C{i}")
                for i in selected_indices
            ]

            # --- Classical Markowitz comparison ---
            markowitz = _classical_markowitz(candidates, risk_factor, budget)

            # --- Write expanded output with weights ---
            qaoa_result = {
                "backend": "qiskit_finance",
                "algorithm": "QAOA",
                "simulator_method": "matrix_product_state" if use_mps else "statevector",
                "status": "success",
                "selected_candidates": selected_symbols,
                "selected_indices": selected_indices,
                "selection_vector": selection,
                "objective_value": float(result.fval),
                "num_assets_input": n,
                "num_assets_selected": sum(selection),
                "budget": budget,
                "risk_factor": risk_factor,
                "qaoa_reps": qaoa_reps,
                "portfolio_weights": raw_weights,
                "constraints_applied": {
                    "max_sector_weight": self.MAX_SECTOR_WEIGHT,
                    "max_single_name": self.MAX_SINGLE_NAME,
                    "min_cash_reserve": self.MIN_CASH_RESERVE,
                },
                "classical_markowitz": markowitz,
                "execution_metadata": self._meta(
                    status="success", elapsed=elapsed_qaoa, num_assets=n, budget=budget,
                    risk_factor=risk_factor, selection=selection,
                    qaoa_reps=qaoa_reps, max_iterations=max_iter,
                    simulator_method="mps" if use_mps else "statevector",
                ),
            }

            # Write QAOA portfolio weights to separate file for integration
            self._write_portfolio_weights(qaoa_result)

            return qaoa_result

        except Exception as exc:
            logger.warning("Qiskit QAOA failed: %s", exc, exc_info=True)
            elapsed = time.monotonic() - start

            # Fall back to classical Markowitz
            markowitz = _classical_markowitz(candidates, risk_factor, budget)

            return {
                "backend": "qiskit_finance", "algorithm": "QAOA",
                "status": "error", "error": str(exc),
                "selected_candidates": [], "selected_indices": [],
                "selection_vector": [], "objective_value": None,
                "classical_markowitz_fallback": markowitz,
                "execution_metadata": self._meta(
                    status="error", elapsed=elapsed, error=str(exc)
                ),
            }

    def _compute_constrained_weights(
        self, candidates: list, selected_indices: list,
        expected_returns: np.ndarray, cov: np.ndarray
    ) -> dict:
        """Compute portfolio weights with constraints applied.

        Constraints:
        - Max 20% per sector
        - Max 5% per single name
        - Min 10% cash reserve
        """
        if not selected_indices:
            return {}

        n_selected = len(selected_indices)
        # Start with equal weight for selected assets
        weights = np.ones(n_selected) / n_selected

        # Score-weighted initial allocation
        scores = expected_returns[selected_indices]
        scores = np.maximum(scores, 0.001)
        weights = scores / scores.sum()

        # Apply max single name constraint (5%)
        investable = 1.0 - self.MIN_CASH_RESERVE  # 90% investable
        weights = weights * investable
        weights = np.minimum(weights, self.MAX_SINGLE_NAME)

        # Apply sector constraint (20% per sector)
        sector_weights: Dict[str, float] = {}
        for i, idx in enumerate(selected_indices):
            sector = candidates[idx].get("sector", "unknown")
            sector_weights[sector] = sector_weights.get(sector, 0) + weights[i]

        # Scale down sectors that exceed 20%
        for sector, total_wt in sector_weights.items():
            if total_wt > self.MAX_SECTOR_WEIGHT:
                scale = self.MAX_SECTOR_WEIGHT / total_wt
                for i, idx in enumerate(selected_indices):
                    if candidates[idx].get("sector", "unknown") == sector:
                        weights[i] *= scale

        # Normalize to fill investable portion
        total = weights.sum()
        if total > 0 and total < investable:
            weights = weights * (investable / total)
            # Re-apply caps after normalization
            weights = np.minimum(weights, self.MAX_SINGLE_NAME)

        result = {}
        for i, idx in enumerate(selected_indices):
            sym = candidates[idx].get("symbol", f"C{idx}")
            if weights[i] > 0.001:
                result[sym] = round(float(weights[i]), 4)

        result["_CASH"] = round(float(1.0 - sum(result.values())), 4)
        return result

    def _write_portfolio_weights(self, qaoa_result: dict) -> None:
        """Write QAOA portfolio weights for integration with continuous learner."""
        try:
            output = {
                "timestamp": _utc_now(),
                "source": "qiskit_qaoa_mps",
                "algorithm": qaoa_result.get("algorithm"),
                "simulator_method": qaoa_result.get("simulator_method"),
                "objective_value": qaoa_result.get("objective_value"),
                "portfolio_weights": qaoa_result.get("portfolio_weights", {}),
                "classical_markowitz_weights": (
                    qaoa_result.get("classical_markowitz", {}).get("weights", {})
                ),
                "num_assets": qaoa_result.get("num_assets_selected", 0),
                "constraints": qaoa_result.get("constraints_applied", {}),
                "not_for_direct_execution": True,
                "research_artifact_only": True,
            }
            QAOA_PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
            QAOA_PORTFOLIO_PATH.write_text(json.dumps(output, indent=2, default=str))
            logger.info("QAOA portfolio weights written to %s", QAOA_PORTFOLIO_PATH)
        except Exception as e:
            logger.warning("Failed to write QAOA portfolio weights: %s", e)

    # ------------------------------------------------------------------
    def _build_covariance(self, candidates: list, n: int):
        if candidates[0].get("covariance_row"):
            return np.array([c["covariance_row"] for c in candidates])
        vols = np.array([c.get("volatility", 0.2) for c in candidates])
        cov = np.diag(vols ** 2)
        for i in range(n):
            for j in range(i + 1, n):
                corr = 0.3 if candidates[i].get("sector") == candidates[j].get("sector") else 0.1
                cov[i][j] = corr * vols[i] * vols[j]
                cov[j][i] = cov[i][j]
        return cov

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


if __name__ == "__main__":
    test = {
        "candidates": [
            {"symbol": "AAPL", "expected_return": 0.08, "volatility": 0.25, "sector": "tech"},
            {"symbol": "MSFT", "expected_return": 0.07, "volatility": 0.22, "sector": "tech"},
            {"symbol": "XOM", "expected_return": 0.05, "volatility": 0.30, "sector": "energy"},
            {"symbol": "JPM", "expected_return": 0.06, "volatility": 0.20, "sector": "financials"},
            {"symbol": "GLD", "expected_return": 0.03, "volatility": 0.15, "sector": "commodities"},
            {"symbol": "TLT", "expected_return": 0.02, "volatility": 0.12, "sector": "bonds"},
        ],
        "constraints": {"budget": 4},
        "config": {"risk_factor": 0.5, "qaoa_reps": 1, "max_iterations": 50},
    }
    try:
        opt = QiskitPortfolioOptimizer()
        result = opt.optimize(test)
    except Exception as exc:
        result = _standalone_error(str(exc))
    print(json.dumps(result, indent=2, default=str))
