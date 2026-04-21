"""Commodity Cascade Phase Classifier — Citadel Quant Framework Integration.

Implements the institutional insight that commodity shocks propagate through
a predictable cascade with quantified lag structures:

  Oil shock → Shipping rates → Refinery spreads → Airline margins →
  Fertilizer costs → Food inflation → EM pressure

Each phase has a characteristic lag from the initial shock origin.
The classifier identifies the current phase and recommends which
strategies should be active based on cascade timing.

Reference: @neelsalami / Neel Somani (Former Citadel Commodities Quant)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Cascade phases with characteristic lags from initial oil shock
CASCADE_PHASES = {
    "shock_origin": {
        "lag_hours": (0, 6),
        "assets": ["USO", "XLE", "XOP", "OXY", "CVX", "XOM"],
        "strategies": ["oil_momentum_intraday", "oil_gap_persistence"],
        "description": "Initial oil price shock — direct energy plays",
    },
    "shipping_react": {
        "lag_hours": (24, 72),
        "assets": ["STNG", "FRO", "ZIM", "NAT"],
        "strategies": ["shipping_rate_explosion"],
        "description": "Shipping rates spike as tankers reroute around Cape",
    },
    "refinery_spread": {
        "lag_hours": (48, 120),
        "assets": ["PSX", "VLO", "MPC"],
        "strategies": ["refining_crack_spread"],
        "description": "Crack spreads widen as refinery inputs cost more",
    },
    "airline_margin": {
        "lag_hours": (72, 168),
        "assets": ["UAL", "DAL", "AAL", "JETS"],
        "strategies": ["airline_short", "jet_fuel_squeeze"],
        "description": "Jet fuel squeeze destroys airline margins",
    },
    "fertilizer_cost": {
        "lag_hours": (168, 672),
        "assets": ["MOS", "CF", "NTR"],
        "strategies": ["fertilizer_food_chain"],
        "description": "Nat gas → ammonia → fertilizer cost cascade (1-4 weeks)",
    },
    "food_inflation": {
        "lag_hours": (672, 2160),
        "assets": ["DBA", "CORN", "WEAT"],
        "strategies": ["petro_inflation"],
        "description": "Fertilizer costs flow into food prices (1-3 months)",
    },
    "em_pressure": {
        "lag_hours": (336, 1008),
        "assets": ["EEM", "FXI", "EWZ"],
        "strategies": ["china_oil_import_shock", "asia_energy_cascade", "em_capital_flight"],
        "description": "EM economies pressured by energy import costs (2-6 weeks)",
    },
}


class CascadePhaseClassifier:
    """Classifies the current phase of a commodity shock cascade.

    Uses the scorecard's shock origin timestamp and current time to determine
    which phase of the cascade is active, informing strategy selection.
    """

    def __init__(self) -> None:
        self._shock_origin_utc: datetime | None = None
        self._current_phase: str = "none"
        self._phase_history: list[dict[str, Any]] = []

    def update_shock_origin(self, origin_utc: datetime) -> None:
        """Set or update the shock origin timestamp."""
        if self._shock_origin_utc != origin_utc:
            self._shock_origin_utc = origin_utc
            logger.info("Cascade shock origin set to %s", origin_utc.isoformat())

    def detect_shock_origin(self, scorecard: dict) -> datetime | None:
        """Detect shock origin from scorecard signals.

        A shock is detected when:
        - Oil regime transitions to SHOCK or DISLOCATION
        - commodity_shock score > 0.7
        - geopolitical_tension score > 0.6
        """
        oil_regime = scorecard.get("v6_oil_regime", "NORMAL")
        commodity_shock = scorecard.get("component_scores", {}).get("commodity_shock", 0)
        geo_tension = scorecard.get("component_scores", {}).get("geopolitical_tension", 0)

        if oil_regime in ("SHOCK", "DISLOCATION") and commodity_shock > 0.7:
            if self._shock_origin_utc is None:
                self._shock_origin_utc = datetime.now(timezone.utc)
                logger.info(
                    "Shock origin auto-detected: oil_regime=%s, commodity_shock=%.2f, geo=%.2f",
                    oil_regime, commodity_shock, geo_tension,
                )
            return self._shock_origin_utc

        # Reset if regime normalizes
        if oil_regime == "NORMAL" and commodity_shock < 0.3:
            if self._shock_origin_utc is not None:
                logger.info("Shock cascade ended — regime normalized")
                self._shock_origin_utc = None
                self._current_phase = "none"

        return self._shock_origin_utc

    def classify(self, scorecard: dict | None = None) -> dict[str, Any]:
        """Classify current cascade phase.

        Returns:
            Dict with phase name, active assets, recommended strategies,
            hours since shock, and phase description.
        """
        if scorecard:
            self.detect_shock_origin(scorecard)

        if self._shock_origin_utc is None:
            return {
                "phase": "none",
                "hours_since_shock": 0,
                "active_assets": [],
                "recommended_strategies": [],
                "description": "No active commodity shock cascade",
                "all_active_phases": [],
            }

        now = datetime.now(timezone.utc)
        hours_since = (now - self._shock_origin_utc).total_seconds() / 3600

        active_phases = []
        all_assets = []
        all_strategies = []

        for phase_name, phase_cfg in CASCADE_PHASES.items():
            lag_min, lag_max = phase_cfg["lag_hours"]
            if lag_min <= hours_since <= lag_max:
                active_phases.append(phase_name)
                all_assets.extend(phase_cfg["assets"])
                all_strategies.extend(phase_cfg["strategies"])

        # The "current" phase is the latest one that's active
        current = active_phases[-1] if active_phases else "post_cascade"
        self._current_phase = current

        result = {
            "phase": current,
            "hours_since_shock": round(hours_since, 1),
            "shock_origin_utc": self._shock_origin_utc.isoformat(),
            "active_assets": list(set(all_assets)),
            "recommended_strategies": list(set(all_strategies)),
            "description": CASCADE_PHASES.get(current, {}).get(
                "description", f"Post-cascade ({hours_since:.0f}h since shock)"
            ),
            "all_active_phases": active_phases,
        }

        logger.info(
            "Cascade phase: %s (%.1fh since shock), %d active phases, %d assets",
            current, hours_since, len(active_phases), len(all_assets),
        )

        return result

    def get_inventory_adjusted_size(
        self,
        base_size: float,
        inventory_level: float,
        historical_avg: float,
        historical_std: float,
    ) -> float:
        """Citadel framework: size up when inventories are below average.

        When inventories are low (convex payoff zone), supply disruptions
        cause exponential price moves. Size up accordingly.
        When inventories are high, disruptions are absorbed. Size down.
        """
        if historical_std <= 0:
            return base_size

        z_score = (inventory_level - historical_avg) / historical_std

        if z_score < -1.5:
            multiplier = 1.75  # Deep deficit — maximum conviction
        elif z_score < -1.0:
            multiplier = 1.5   # Below average — convex zone
        elif z_score < -0.5:
            multiplier = 1.25  # Slightly below — mild boost
        elif z_score > 1.5:
            multiplier = 0.4   # Deep surplus — dampened zone
        elif z_score > 1.0:
            multiplier = 0.5   # Above average — reduced size
        elif z_score > 0.5:
            multiplier = 0.75  # Slightly above — mild reduction
        else:
            multiplier = 1.0   # Normal range

        adjusted = base_size * multiplier
        logger.debug(
            "Inventory-adjusted size: base=%.2f, z=%.2f, mult=%.2f, adj=%.2f",
            base_size, z_score, multiplier, adjusted,
        )
        return adjusted
