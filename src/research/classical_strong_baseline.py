#!/usr/bin/env python3
"""Strong classical baseline optimizer using scipy/cvxpy.

This is the benchmark that quantum optimization must beat for meaningful claims.
Uses constrained quadratic programming, not just greedy heuristics.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class ClassicalStrongBaseline:
    """Constrained portfolio optimizer using scipy minimize."""

    def __init__(self, method: str = "SLSQP"):
        self.method = method

    def optimize(
        self,
        candidates: List[Dict[str, Any]],
        objective_type: str = "sharpe",
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run constrained optimization.

        Args:
            candidates: list of candidate dicts with scores
            objective_type: sharpe, min_variance, max_return, cvar
            constraints: optional constraint dict

        Returns:
            Optimization result with weights, objective value, metadata
        """
        start = time.time()
        n = len(candidates)
        if n == 0:
            return self._empty_result(objective_type)

        constraints = constraints or {}
        max_weight = float(constraints.get("max_single_weight", 0.40))
        min_weight = float(constraints.get("min_single_weight", 0.0))

        # Extract scores as proxy returns/risk
        scores = np.array([float(c.get("preopt_feature_score", c.get("base_score", 0.5))) for c in candidates])
        # Use score variance as risk proxy
        risks = np.array([float(c.get("volatility_penalty", 0.3)) for c in candidates])

        try:
            from scipy.optimize import minimize

            # Build covariance matrix (diagonal + small correlation)
            cov = np.diag(risks ** 2) + 0.01 * np.outer(risks, risks)

            if objective_type == "sharpe":
                weights = self._optimize_sharpe(scores, cov, n, max_weight, min_weight)
            elif objective_type == "min_variance":
                weights = self._optimize_min_variance(cov, n, max_weight, min_weight)
            elif objective_type == "max_return":
                weights = self._optimize_max_return(scores, n, max_weight, min_weight)
            else:
                weights = self._optimize_sharpe(scores, cov, n, max_weight, min_weight)

        except ImportError:
            logger.warning("scipy not available; using equal weight fallback")
            weights = np.ones(n) / n

        elapsed = time.time() - start

        # Compute objective value
        portfolio_return = float(np.dot(weights, scores))
        cov_simple = np.diag(risks ** 2) + 0.01 * np.outer(risks, risks)
        portfolio_risk = float(np.sqrt(np.dot(weights, np.dot(cov_simple, weights))))
        sharpe = portfolio_return / max(portfolio_risk, 1e-8)

        selected = []
        for i, c in enumerate(candidates):
            if weights[i] > 0.01:
                selected.append({
                    "symbol": c.get("symbol", f"C{i}"),
                    "weight": round(float(weights[i]), 4),
                    "score": float(scores[i]),
                })

        return {
            "schema_version": "classical_strong_baseline.v1",
            "optimizer": "scipy_" + self.method,
            "objective_type": objective_type,
            "portfolio_return": round(portfolio_return, 6),
            "portfolio_risk": round(portfolio_risk, 6),
            "sharpe_ratio": round(sharpe, 4),
            "selected_candidates": selected,
            "all_weights": [round(float(w), 6) for w in weights],
            "candidate_count": n,
            "elapsed_seconds": round(elapsed, 4),
            "not_for_direct_execution": True,
            "artifact_only": True,
        }

    def _optimize_sharpe(self, returns, cov, n, max_w, min_w):
        from scipy.optimize import minimize
        x0 = np.ones(n) / n

        def neg_sharpe(w):
            ret = np.dot(w, returns)
            risk = np.sqrt(np.dot(w, np.dot(cov, w)))
            return -(ret / max(risk, 1e-8))

        constraints_list = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
        ]
        bounds = [(min_w, max_w)] * n
        result = minimize(neg_sharpe, x0, method=self.method, bounds=bounds, constraints=constraints_list)
        return np.clip(result.x, 0, 1)

    def _optimize_min_variance(self, cov, n, max_w, min_w):
        from scipy.optimize import minimize
        x0 = np.ones(n) / n

        def variance(w):
            return np.dot(w, np.dot(cov, w))

        constraints_list = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(min_w, max_w)] * n
        result = minimize(variance, x0, method=self.method, bounds=bounds, constraints=constraints_list)
        return np.clip(result.x, 0, 1)

    def _optimize_max_return(self, returns, n, max_w, min_w):
        from scipy.optimize import minimize
        x0 = np.ones(n) / n

        def neg_return(w):
            return -np.dot(w, returns)

        constraints_list = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(min_w, max_w)] * n
        result = minimize(neg_return, x0, method=self.method, bounds=bounds, constraints=constraints_list)
        return np.clip(result.x, 0, 1)

    @staticmethod
    def _empty_result(objective_type):
        return {
            "schema_version": "classical_strong_baseline.v1",
            "optimizer": "none",
            "objective_type": objective_type,
            "portfolio_return": 0.0,
            "portfolio_risk": 0.0,
            "sharpe_ratio": 0.0,
            "selected_candidates": [],
            "all_weights": [],
            "candidate_count": 0,
            "elapsed_seconds": 0.0,
            "not_for_direct_execution": True,
            "artifact_only": True,
        }
