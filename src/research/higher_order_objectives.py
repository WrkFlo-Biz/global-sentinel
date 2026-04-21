#!/usr/bin/env python3
"""Higher-order portfolio objectives beyond mean-variance.

CVaR (Conditional Value at Risk), Maximum Drawdown, Skewness-Kurtosis.
Tagged as quantum-friendly or classical-only.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np


class HigherOrderObjectives:
    """Higher-order objective functions for portfolio optimization."""

    OBJECTIVES = {
        "cvar": {
            "name": "Conditional Value at Risk",
            "quantum_friendly": True,
            "encoding": "QUBO",
            "description": "Minimize expected loss in worst alpha% of scenarios",
        },
        "max_drawdown": {
            "name": "Maximum Drawdown Minimization",
            "quantum_friendly": True,
            "encoding": "QUBO",
            "description": "Minimize peak-to-trough portfolio decline",
        },
        "skew_kurtosis": {
            "name": "Skewness-Kurtosis Aware",
            "quantum_friendly": False,
            "encoding": "classical_only",
            "description": "Optimize for positive skew and low kurtosis",
        },
    }

    def get_objective(self, objective_type: str) -> Dict[str, Any]:
        return self.OBJECTIVES.get(objective_type, {})

    def compute_cvar(
        self,
        returns: List[float],
        weights: List[float],
        alpha: float = 0.05,
    ) -> float:
        """Compute portfolio CVaR at confidence level alpha."""
        portfolio_returns = np.array(returns) @ np.array(weights) if len(np.array(returns).shape) > 1 else np.array(returns) * np.array(weights[0]) if len(weights) == 1 else np.array(returns)

        if isinstance(returns[0], (list, np.ndarray)):
            # Matrix of scenario returns
            port_ret = np.array(returns) @ np.array(weights)
        else:
            # Single scenario
            port_ret = np.array(returns)

        sorted_returns = np.sort(port_ret)
        cutoff = int(np.ceil(len(sorted_returns) * alpha))
        if cutoff == 0:
            cutoff = 1
        cvar = float(np.mean(sorted_returns[:cutoff]))
        return round(cvar, 6)

    def compute_max_drawdown(self, cumulative_returns: List[float]) -> float:
        """Compute maximum drawdown from cumulative returns."""
        if not cumulative_returns:
            return 0.0
        peak = cumulative_returns[0]
        max_dd = 0.0
        for r in cumulative_returns:
            peak = max(peak, r)
            dd = (peak - r) / max(abs(peak), 1e-8)
            max_dd = max(max_dd, dd)
        return round(max_dd, 6)

    def compute_skewness(self, returns: List[float]) -> float:
        """Compute skewness of return distribution."""
        arr = np.array(returns)
        n = len(arr)
        if n < 3:
            return 0.0
        mean = np.mean(arr)
        std = np.std(arr, ddof=1)
        if std < 1e-8:
            return 0.0
        return float(round((n / ((n-1)*(n-2))) * np.sum(((arr - mean) / std) ** 3), 6))

    def compute_kurtosis(self, returns: List[float]) -> float:
        """Compute excess kurtosis."""
        arr = np.array(returns)
        n = len(arr)
        if n < 4:
            return 0.0
        mean = np.mean(arr)
        std = np.std(arr, ddof=1)
        if std < 1e-8:
            return 0.0
        kurt = float(np.mean(((arr - mean) / std) ** 4) - 3.0)
        return round(kurt, 6)

    def list_objectives(self) -> List[Dict[str, Any]]:
        return [{"type": k, **v} for k, v in self.OBJECTIVES.items()]
