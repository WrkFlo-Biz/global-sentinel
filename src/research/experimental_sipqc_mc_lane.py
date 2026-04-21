"""Structure-Inspired Parameterized Quantum Circuit (SIPQC) Monte Carlo lane.

RESEARCH-ONLY frontier R&D module — Global Sentinel V4 Pack 8.

Explores geopolitical scenario analysis using parameterized circuit-inspired
sampling for scenario generation, Monte Carlo simulation of portfolio outcomes
under generated scenarios, and tail-risk / VaR estimation from simulated
distributions.

NOT FOR DIRECT EXECUTION in production pipelines.
"""
from __future__ import annotations

import logging
import math
import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np  # type: ignore[import-untyped]
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

import random as _stdlib_random


# ---------------------------------------------------------------------------
# Helpers — parameterized rotation primitives (pure-python fallback safe)
# ---------------------------------------------------------------------------

def _rotation_sample(
    theta: float,
    phi: float,
    rng: _stdlib_random.Random,
) -> float:
    """Return a sample inspired by parameterized Ry/Rz rotation gates.

    Maps two angles to a biased random variate via:
        cos(theta/2)**2 probability of sampling from one mode,
        sin(theta/2)**2 from the other, with phase-shift phi
        controlling the mode separation.
    """
    cos2 = math.cos(theta / 2.0) ** 2
    if rng.random() < cos2:
        return rng.gauss(0.0, 1.0) * math.cos(phi)
    return rng.gauss(0.0, 1.0) * math.sin(phi) + math.sin(theta)


def _parameterized_circuit_sample(
    params: List[List[float]],
    rng: _stdlib_random.Random,
) -> float:
    """Chain multiple rotation layers (circuit depth) into a single sample."""
    value = 0.0
    for layer in params:
        theta, phi = layer[0], layer[1]
        value += _rotation_sample(theta, phi, rng)
    return value


# ---------------------------------------------------------------------------
# Regime-to-parameter mapping
# ---------------------------------------------------------------------------

_REGIME_PARAM_MAP: Dict[str, Dict[str, float]] = {
    "risk_on": {"base_theta": 0.3, "base_phi": 0.5},
    "risk_off": {"base_theta": 1.8, "base_phi": 2.0},
    "crisis": {"base_theta": 2.5, "base_phi": 2.8},
    "transition": {"base_theta": 1.0, "base_phi": 1.2},
}


def _regime_to_params(
    regime_state: Dict[str, Any],
    circuit_depth: int,
    rng: _stdlib_random.Random,
) -> List[List[float]]:
    """Convert a regime state dict into layered circuit parameters."""
    regime_label = regime_state.get("regime", "transition")
    defaults = _REGIME_PARAM_MAP.get(regime_label, _REGIME_PARAM_MAP["transition"])
    base_theta = defaults["base_theta"]
    base_phi = defaults["base_phi"]
    shift_prob = regime_state.get("regime_shift_probability", 0.5)

    params: List[List[float]] = []
    for d in range(circuit_depth):
        theta = base_theta + shift_prob * (d + 1) * 0.1 + rng.gauss(0.0, 0.05)
        phi = base_phi + (1.0 - shift_prob) * (d + 1) * 0.1 + rng.gauss(0.0, 0.05)
        params.append([theta, phi])
    return params


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ExperimentalSIPQCMCLane:
    """SIPQC-inspired Monte Carlo lane for research scenario analysis.

    Attributes
    ----------
    research_only : bool
        Always ``True``.
    not_for_direct_execution : bool
        Always ``True``.
    """

    research_only: bool = True
    not_for_direct_execution: bool = True

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.n_scenarios: int = cfg.get("n_scenarios", 1000)
        self.confidence_level: float = cfg.get("confidence_level", 0.95)
        self.circuit_depth: int = cfg.get("circuit_depth", 3)
        self._seed: Optional[int] = cfg.get("seed", None)
        self._rng = _stdlib_random.Random(self._seed)
        logger.info(
            "ExperimentalSIPQCMCLane initialised (n_scenarios=%d, depth=%d, seed=%s)",
            self.n_scenarios,
            self.circuit_depth,
            self._seed,
        )

    # ------------------------------------------------------------------
    # Scenario generation
    # ------------------------------------------------------------------

    def generate_scenarios(
        self,
        regime_state: Dict[str, Any],
        n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Generate synthetic market scenarios from regime parameters.

        Parameters
        ----------
        regime_state:
            Must contain at least ``regime`` (str) and optionally
            ``regime_shift_probability`` (float 0-1).
        n:
            Number of scenarios.  Falls back to ``self.n_scenarios``.

        Returns
        -------
        List of scenario dicts, each with:
            regime_label, return_shift, vol_multiplier, correlation_shift.
        """
        count = n if n is not None else self.n_scenarios
        regime_label = regime_state.get("regime", "transition")

        scenarios: List[Dict[str, Any]] = []
        for i in range(count):
            params = _regime_to_params(regime_state, self.circuit_depth, self._rng)
            raw = _parameterized_circuit_sample(params, self._rng)

            # Map raw sample to market-meaningful quantities
            return_shift = raw * 0.01  # basis-point scale shift
            vol_multiplier = 1.0 + abs(raw) * 0.05
            correlation_shift = math.tanh(raw * 0.3)

            scenarios.append({
                "scenario_id": f"sipqc-{i:06d}",
                "regime_label": regime_label,
                "return_shift": return_shift,
                "vol_multiplier": vol_multiplier,
                "correlation_shift": correlation_shift,
            })

        logger.debug("Generated %d scenarios for regime '%s'", count, regime_label)
        return scenarios

    # ------------------------------------------------------------------
    # Outcome simulation
    # ------------------------------------------------------------------

    def simulate_outcomes(
        self,
        candidates: List[Dict[str, Any]],
        scenarios: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Simulate portfolio outcomes for each scenario.

        For every scenario the method estimates a portfolio P&L and drawdown
        across all candidates, using simple linear sensitivity.

        Parameters
        ----------
        candidates:
            List of candidate position dicts.  Each should have at least
            ``weight`` (float) and ``expected_return`` (float).
        scenarios:
            As returned by :meth:`generate_scenarios`.

        Returns
        -------
        List of outcome dicts with ``scenario_id``, ``pnl``, ``drawdown``,
        and ``regime_label``.
        """
        outcomes: List[Dict[str, Any]] = []

        for sc in scenarios:
            pnl = 0.0
            max_dd = 0.0
            running = 0.0

            for cand in candidates:
                weight = cand.get("weight", 1.0 / max(len(candidates), 1))
                exp_ret = cand.get("expected_return", 0.0)

                # Scenario-adjusted return
                adj_ret = (exp_ret + sc["return_shift"]) * sc["vol_multiplier"]
                contribution = weight * adj_ret
                pnl += contribution

                running += contribution
                if running < max_dd:
                    max_dd = running

            outcomes.append({
                "scenario_id": sc["scenario_id"],
                "regime_label": sc["regime_label"],
                "pnl": pnl,
                "drawdown": max_dd,
            })

        return outcomes

    # ------------------------------------------------------------------
    # Risk metrics
    # ------------------------------------------------------------------

    def compute_risk_metrics(self, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Derive tail-risk metrics from simulated outcomes.

        Returns
        -------
        Dict with ``var``, ``cvar``, ``max_drawdown``, ``tail_probability``,
        and ``scenario_diversity``.
        """
        if not outcomes:
            return {
                "var": 0.0,
                "cvar": 0.0,
                "max_drawdown": 0.0,
                "tail_probability": 0.0,
                "scenario_diversity": 0,
            }

        pnls = [o["pnl"] for o in outcomes]
        drawdowns = [o["drawdown"] for o in outcomes]
        n = len(pnls)

        sorted_pnls = sorted(pnls)
        idx = max(0, int((1.0 - self.confidence_level) * n) - 1)
        var = -sorted_pnls[idx]  # VaR as positive loss

        # CVaR: mean of losses beyond VaR
        tail = sorted_pnls[: idx + 1] if idx >= 0 else sorted_pnls[:1]
        cvar = -sum(tail) / max(len(tail), 1)

        max_drawdown = -min(drawdowns) if drawdowns else 0.0

        # Tail probability: fraction of outcomes worse than -2 std
        mean_pnl = sum(pnls) / n
        std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / max(n - 1, 1))
        threshold = mean_pnl - 2.0 * std_pnl if std_pnl > 0 else mean_pnl
        tail_count = sum(1 for p in pnls if p < threshold)
        tail_probability = tail_count / n

        # Scenario diversity
        unique_regimes = set(o.get("regime_label", "") for o in outcomes)
        scenario_diversity = len(unique_regimes)

        return {
            "var": var,
            "cvar": cvar,
            "max_drawdown": max_drawdown,
            "tail_probability": tail_probability,
            "scenario_diversity": scenario_diversity,
        }

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run(
        self,
        candidates: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute the full SIPQC-MC pipeline.

        Returns
        -------
        Dict containing ``schema_version``, ``research_only``,
        ``not_for_direct_execution``, ``scenarios_generated``,
        ``risk_metrics``, and ``timestamp_utc``.
        """
        scenarios = self.generate_scenarios(regime_state)
        outcomes = self.simulate_outcomes(candidates, scenarios)
        metrics = self.compute_risk_metrics(outcomes)

        return {
            "schema_version": "experimental_sipqc_mc.v1",
            "research_only": True,
            "not_for_direct_execution": True,
            "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "scenarios_generated": len(scenarios),
            "risk_metrics": metrics,
        }
