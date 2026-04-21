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
        session_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        session_context = session_context or {}
        incident_mode = bool(runtime_flags.get("incident_mode", False))
        window = str(
            time_window_state.get("window")
            or time_window_state.get("time_window_name")
            or session_context.get("window")
            or session_context.get("window_name")
            or "overnight"
        ).lower()
        session = str(
            session_context.get("session")
            or time_window_state.get("session")
            or time_window_state.get("current_session")
            or ""
        ).lower()
        phase = str(
            session_context.get("intraday_phase")
            or time_window_state.get("intraday_phase")
            or ""
        ).lower()
        impact_multiplier = float(
            time_window_state.get("impact_multiplier", session_context.get("impact_multiplier", 1.0))
        )
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

        if session in {"overnight", "pre_market", "after_hours"} or window in {
            "overnight",
            "pre_market",
            "after_hours",
            "premarket_signal_prep",
        }:
            max_names = min(max_names, 8)
            max_participation_rate *= 0.65
            impact_budget_bps *= 1.05

        if (
            incident_mode
            or window in {"opening_rush", "opening_range_breakout_window", "opening_amateur_hour_cooldown"}
            or phase == "opening"
        ):
            max_names = min(max_names, 8)
            max_participation_rate *= 0.75
            impact_budget_bps *= 1.10 * impact_multiplier
        elif window in {"power_hour", "close_exhaustion_watch"} or phase == "power_hour":
            max_participation_rate *= 0.85
            impact_budget_bps *= 1.08 * impact_multiplier
        elif window in {"lunch_lull"} or phase == "midday":
            impact_budget_bps *= 0.92 * impact_multiplier

        if bool(time_window_state.get("watchlist_only_window", False)) or bool(
            time_window_state.get("shadow_execution_window_blocked", False)
        ):
            max_names = min(max_names, 4)
            max_participation_rate *= 0.5
            impact_budget_bps *= 0.85

        if regime_prob >= 0.75:
            max_names = min(max_names, 8)

        return {
            "max_names": max_names,
            "max_sector_weight": round(max_sector_weight, 4),
            "max_participation_rate": round(max_participation_rate, 4),
            "impact_budget_bps": round(impact_budget_bps, 4),
            "incident_mode": incident_mode,
            "time_window": window,
            "session": session or None,
            "intraday_phase": phase or None,
            "watchlist_only_window": bool(time_window_state.get("watchlist_only_window", False)),
            "objective_type": objective_type,
        }
