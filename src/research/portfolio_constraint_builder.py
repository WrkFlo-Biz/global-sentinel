"""Build reusable optimization constraints for classical and quantum lanes.

Produces constraint dicts consumed by QuantumOptimizationRequest,
adapting to objective type, regime, time window, and incident mode.
"""
from __future__ import annotations

from typing import Any, Dict


class PortfolioConstraintBuilder:

    def build(
        self,
        *,
        objective_type: str,
        runtime_flags: Dict[str, Any],
        time_window_state: Dict[str, Any],
        regime_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        incident_mode = bool(runtime_flags.get("incident_mode", False))
        window = str(time_window_state.get("window", "overnight")).lower()
        impact_multiplier = float(time_window_state.get("impact_multiplier", 1.0))
        regime_prob = float(regime_state.get("regime_shift_probability", 0.5))

        max_names = 10
        max_sector_weight = 0.30
        max_participation_rate = 0.02
        impact_budget_bps = 15.0

        if objective_type == "hedge_basket_optimization":
            max_names = 8
            max_sector_weight = 0.35
            impact_budget_bps = 18.0

        if objective_type == "derivative_pricing_research":
            max_names = 6
            max_sector_weight = 0.40
            impact_budget_bps = 12.0

        if incident_mode or window in {"opening_rush", "power_hour"}:
            max_participation_rate *= 0.75
            impact_budget_bps *= 1.10 * impact_multiplier

        if regime_prob >= 0.75:
            max_names = min(max_names, 8)

        return {
            "max_names": max_names,
            "max_sector_weight": round(max_sector_weight, 4),
            "max_participation_rate": round(max_participation_rate, 4),
            "impact_budget_bps": round(impact_budget_bps, 4),
            "incident_mode": incident_mode,
            "time_window": window,
            "objective_type": objective_type,
        }
