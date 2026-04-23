#!/usr/bin/env python3
"""Higher-order portfolio objectives beyond mean-variance.

CVaR (Conditional Value at Risk), Maximum Drawdown, Skewness-Kurtosis.
Tagged as quantum-friendly or classical-only.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


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
        if not returns:
            return 0.0

        if isinstance(returns[0], (list, tuple)):
            port_ret = [
                sum(float(row[i]) * float(weights[i]) for i in range(min(len(row), len(weights))))
                for row in returns
            ]
        elif len(weights) == 1:
            port_ret = [float(value) * float(weights[0]) for value in returns]
        else:
            port_ret = [float(value) for value in returns]

        sorted_returns = sorted(float(value) for value in port_ret)
        cutoff = int(math.ceil(len(sorted_returns) * alpha))
        if cutoff == 0:
            cutoff = 1
        cvar = sum(sorted_returns[:cutoff]) / cutoff
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
        n = len(returns)
        if n < 3:
            return 0.0
        arr = [float(value) for value in returns]
        mean = sum(arr) / n
        variance = sum((value - mean) ** 2 for value in arr) / (n - 1)
        std = math.sqrt(variance)
        if std < 1e-8:
            return 0.0
        centered_cubed = sum(((value - mean) / std) ** 3 for value in arr)
        return float(round((n / ((n - 1) * (n - 2))) * centered_cubed, 6))

    def compute_kurtosis(self, returns: List[float]) -> float:
        """Compute excess kurtosis."""
        n = len(returns)
        if n < 4:
            return 0.0
        arr = [float(value) for value in returns]
        mean = sum(arr) / n
        variance = sum((value - mean) ** 2 for value in arr) / (n - 1)
        std = math.sqrt(variance)
        if std < 1e-8:
            return 0.0
        kurt = sum(((value - mean) / std) ** 4 for value in arr) / n - 3.0
        return round(kurt, 6)

    def list_objectives(self) -> List[Dict[str, Any]]:
        return [{"type": k, **v} for k, v in self.OBJECTIVES.items()]
