"""Lightweight Monte Carlo scenario engine for research stress testing.

Generates shock paths and samples stress outcomes for candidate ranking.
Not a full pricing engine — designed for research-level scenario analysis.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, asdict
from typing import Dict, Any, List


@dataclass
class ScenarioPath:
    scenario_id: str
    returns: List[float]
    terminal_return: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MonteCarloScenarioEngine:

    def generate_paths(
        self,
        *,
        n_paths: int = 100,
        n_steps: int = 20,
        mu: float = 0.0,
        sigma: float = 0.02,
        shock_bps: float = 0.0,
        seed: int = 42,
    ) -> List[ScenarioPath]:
        rng = random.Random(seed)
        out: List[ScenarioPath] = []

        shock = shock_bps / 10000.0

        for i in range(n_paths):
            vals = []
            total = 1.0
            for step in range(n_steps):
                step_ret = rng.gauss(mu, sigma)
                if step == 0 and shock != 0.0:
                    step_ret += shock
                vals.append(step_ret)
                total *= (1.0 + step_ret)

            out.append(
                ScenarioPath(
                    scenario_id=f"mc-{i:05d}",
                    returns=vals,
                    terminal_return=total - 1.0,
                    metadata={
                        "mu": mu,
                        "sigma": sigma,
                        "shock_bps": shock_bps,
                        "n_steps": n_steps,
                    },
                )
            )
        return out

    def summarize(self, paths: List[ScenarioPath]) -> Dict[str, Any]:
        terminals = [p.terminal_return for p in paths]
        if not terminals:
            return {"count": 0}

        terminals_sorted = sorted(terminals)
        n = len(terminals_sorted)
        p05 = terminals_sorted[max(0, int(0.05 * n) - 1)]
        p50 = terminals_sorted[max(0, int(0.50 * n) - 1)]
        p95 = terminals_sorted[max(0, int(0.95 * n) - 1)]

        return {
            "count": len(paths),
            "mean_terminal_return": sum(terminals) / len(terminals),
            "p05_terminal_return": p05,
            "p50_terminal_return": p50,
            "p95_terminal_return": p95,
        }
