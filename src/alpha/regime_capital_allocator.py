#!/usr/bin/env python3
"""Regime-Aware Dynamic Capital Allocator.

Inspired by multi-strategy portfolio systems that use:
1. Causal temporal features (lagged cross-asset signals)
2. Graph diffusion (how shocks propagate across asset classes)
3. ETF shock propagation (sector rotation cascades)

These feed into a regime state vector that converts each strategy's forecast
into a proprietary utility score for capital allocation — stronger strategies
get more capital, weaker ones get less.

Integrates with:
- ProfitOptimizer (provides base allocation recommendations)
- OilShockRegime (oil market regime classification)
- AgSpreadSignal (agricultural spread phase)
- CrossAssetSignals (bond/currency/commodity cascades)
- StrategyEngine (trade idea generation)
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Regime state vector components and their weights
REGIME_COMPONENTS = {
    "oil_regime": 0.20,         # NORMAL=0, ELEVATED=0.33, SHOCK=0.66, DISLOCATION=1.0
    "crisis_mode": 0.15,        # NORMAL=0, ELEVATED=0.5, CRISIS=1.0
    "commodity_shock": 0.15,    # 0.0-1.0 from scorecard
    "vix_regime": 0.10,         # normalized VIX level
    "chokepoint_risk": 0.10,    # max chokepoint score
    "fertilizer_regime": 0.10,  # NORMAL=0, ELEVATED=0.33, SHOCK=0.66, CRISIS=1.0
    "ag_spread_phase": 0.10,    # NEUTRAL=0, ETHANOL_RALLY=0.5, SQUEEZE=0.75, REVERSAL=1.0
    "geopolitical_tension": 0.10,  # from scorecard
}

# Strategy utility decay — how fast a losing strategy loses allocation
UTILITY_DECAY_RATE = 0.15  # 15% decay per losing period
UTILITY_GROWTH_RATE = 0.10  # 10% growth per winning period

# Allocation bounds
MIN_STRATEGY_ALLOCATION = 0.005   # 0.5% of equity
MAX_STRATEGY_ALLOCATION = 0.08    # 8% of equity
REBALANCE_THRESHOLD = 0.005       # Only rebalance if change > 0.5%


class RegimeCapitalAllocator:
    """Dynamic capital allocator that adjusts strategy weights based on
    regime state vector and rolling strategy performance."""

    def __init__(self) -> None:
        self._strategy_utilities: Dict[str, float] = {}  # strategy → utility score (0-1)
        self._regime_state: Dict[str, float] = {}
        self._allocation_history: List[Dict[str, Any]] = []

    def compute_regime_state_vector(
        self,
        oil_regime: str = "NORMAL",
        crisis_mode: str = "NORMAL",
        commodity_shock: float = 0.0,
        vix_level: Optional[float] = None,
        chokepoint_scores: Optional[Dict[str, float]] = None,
        fertilizer_regime: str = "UNKNOWN",
        ag_spread_phase: str = "NEUTRAL",
        geopolitical_tension: float = 0.0,
    ) -> Dict[str, float]:
        """Build the regime state vector from current market conditions.

        Returns dict of component → normalized value (0.0-1.0).
        """
        state: Dict[str, float] = {}

        # Oil regime
        oil_map = {"NORMAL": 0.0, "ELEVATED": 0.33, "SHOCK": 0.66, "DISLOCATION": 1.0}
        state["oil_regime"] = oil_map.get(oil_regime, 0.0)

        # Crisis mode
        crisis_map = {"NORMAL": 0.0, "ELEVATED": 0.5, "CRISIS": 1.0, "MANUAL_REVIEW": 0.8}
        state["crisis_mode"] = crisis_map.get(crisis_mode, 0.0)

        # Commodity shock (already 0-1)
        state["commodity_shock"] = min(max(commodity_shock, 0.0), 1.0)

        # VIX regime (normalize: 12=calm, 20=normal, 35=elevated, 50+=crisis)
        if vix_level is not None:
            state["vix_regime"] = min(max((vix_level - 12) / 40, 0.0), 1.0)
        else:
            state["vix_regime"] = 0.25  # assume normal

        # Chokepoint risk (max across all chokepoints)
        if chokepoint_scores:
            numeric_vals = [v for v in chokepoint_scores.values() if isinstance(v, (int, float))]
            state["chokepoint_risk"] = min(max(max(numeric_vals), 0.0), 1.0) if numeric_vals else 0.0
        else:
            state["chokepoint_risk"] = 0.0

        # Fertilizer regime
        fert_map = {"NORMAL": 0.0, "ELEVATED": 0.33, "SHOCK": 0.66, "CRISIS": 1.0, "UNKNOWN": 0.15}
        state["fertilizer_regime"] = fert_map.get(fertilizer_regime, 0.15)

        # Ag spread phase
        phase_map = {"NEUTRAL": 0.0, "ETHANOL_RALLY": 0.5, "FERTILIZER_SQUEEZE": 0.75, "SPREAD_REVERSAL": 1.0}
        state["ag_spread_phase"] = phase_map.get(ag_spread_phase, 0.0)

        # Geopolitical tension (already 0-1 from scorecard)
        state["geopolitical_tension"] = min(max(geopolitical_tension, 0.0), 1.0)

        # Compute composite regime score (weighted sum)
        composite = sum(
            state.get(comp, 0.0) * weight
            for comp, weight in REGIME_COMPONENTS.items()
        )
        state["composite_regime_score"] = round(composite, 4)

        self._regime_state = state
        return state

    def compute_strategy_utility(
        self,
        strategy_name: str,
        signal_strength: float,
        pnl_trailing: float,
        regime_alignment: float,
    ) -> float:
        """Compute utility score for a strategy given current conditions.

        Args:
            strategy_name: Name of the strategy
            signal_strength: Current signal strength (0-1)
            pnl_trailing: Trailing P&L (positive = winning)
            regime_alignment: How well this strategy aligns with current regime (0-1)

        Returns:
            Utility score (0-1) used for capital allocation ranking.
        """
        prev_utility = self._strategy_utilities.get(strategy_name, 0.5)

        # Performance-based adjustment
        if pnl_trailing > 0:
            perf_factor = 1.0 + UTILITY_GROWTH_RATE * min(pnl_trailing / 1000, 3.0)
        else:
            perf_factor = 1.0 - UTILITY_DECAY_RATE * min(abs(pnl_trailing) / 1000, 3.0)

        # Regime alignment boost
        regime_factor = 0.7 + 0.3 * regime_alignment

        # Signal strength contribution
        signal_factor = 0.5 + 0.5 * signal_strength

        # Composite utility
        utility = prev_utility * perf_factor * regime_factor * signal_factor
        utility = min(max(utility, 0.05), 1.0)

        self._strategy_utilities[strategy_name] = utility
        return round(utility, 4)

    def allocate(
        self,
        strategy_utilities: Dict[str, float],
        total_equity: float,
        max_gross_exposure_pct: float = 0.40,
    ) -> Dict[str, Dict[str, Any]]:
        """Allocate capital across strategies proportional to utility scores.

        Stronger strategies get more capital. Implements:
        - Proportional allocation based on utility ranking
        - Min/max allocation bounds per strategy
        - Total gross exposure cap
        - Rebalance threshold to avoid churn

        Args:
            strategy_utilities: Dict of strategy_name → utility score (0-1)
            total_equity: Total portfolio equity
            max_gross_exposure_pct: Maximum gross exposure as fraction of equity

        Returns:
            Dict of strategy_name → {allocation_pct, allocation_usd, utility, rank}
        """
        if not strategy_utilities:
            return {}

        max_gross_usd = total_equity * max_gross_exposure_pct

        # Rank strategies by utility
        ranked = sorted(strategy_utilities.items(), key=lambda x: x[1], reverse=True)
        total_utility = sum(u for _, u in ranked) or 1.0

        allocations: Dict[str, Dict[str, Any]] = {}
        total_allocated = 0.0

        for rank, (strategy, utility) in enumerate(ranked, 1):
            # Proportional allocation
            raw_pct = (utility / total_utility) * max_gross_exposure_pct

            # Apply bounds
            alloc_pct = min(max(raw_pct, MIN_STRATEGY_ALLOCATION), MAX_STRATEGY_ALLOCATION)

            # Check gross cap
            if total_allocated + alloc_pct > max_gross_exposure_pct:
                alloc_pct = max(max_gross_exposure_pct - total_allocated, 0.0)

            alloc_usd = round(alloc_pct * total_equity, 2)
            total_allocated += alloc_pct

            allocations[strategy] = {
                "allocation_pct": round(alloc_pct, 4),
                "allocation_usd": alloc_usd,
                "utility": utility,
                "rank": rank,
            }

        self._allocation_history.append({
            "total_allocated_pct": round(total_allocated, 4),
            "strategy_count": len(allocations),
            "top_strategy": ranked[0][0] if ranked else None,
            "regime_composite": self._regime_state.get("composite_regime_score", 0.0),
        })
        if len(self._allocation_history) > 50:
            self._allocation_history = self._allocation_history[-50:]

        return allocations

    def get_strategy_regime_alignment(
        self,
        strategy_name: str,
        regime_state: Dict[str, float],
    ) -> float:
        """Compute how well a strategy aligns with the current regime.

        Returns 0.0 (misaligned) to 1.0 (perfectly aligned).
        """
        composite = regime_state.get("composite_regime_score", 0.0)
        oil = regime_state.get("oil_regime", 0.0)
        fert = regime_state.get("fertilizer_regime", 0.0)
        ag_phase = regime_state.get("ag_spread_phase", 0.0)
        vix = regime_state.get("vix_regime", 0.0)
        chokepoint = regime_state.get("chokepoint_risk", 0.0)

        # Strategy-specific alignment scoring
        alignment_rules: Dict[str, float] = {
            # Oil-correlated strategies thrive in elevated+ regimes
            "oil_momentum_intraday": oil,
            "oil_gap_persistence": oil,
            "shipping_rate_explosion": max(oil, chokepoint),
            "defense_accumulation": composite,
            "gold_safe_haven": max(composite, vix),
            "airline_short": oil * 0.8 + vix * 0.2,
            "fertilizer_food_chain": max(fert, oil * 0.5),
            "vix_spike_scalp": vix,
            "refining_crack_spread": oil,
            "jet_fuel_squeeze": oil,
            "supply_shock_pairs": oil,

            # Ag spread cascade — depends on phase
            "ag_spread_cascade": max(ag_phase, oil * 0.3 + fert * 0.5),

            # Macro strategies
            "europe_energy_crisis": max(oil, chokepoint),
            "em_capital_flight": composite,
            "inflation_hedge": oil * 0.5 + composite * 0.5,
            "china_oil_import_shock": oil,
            "asia_energy_cascade": oil,
            "commodity_currency_divergence": oil,
            "petro_inflation": oil,
        }

        alignment = alignment_rules.get(strategy_name, composite * 0.5)
        return min(max(alignment, 0.0), 1.0)

    def format_telegram(self, allocations: Dict[str, Dict[str, Any]]) -> str:
        """Format allocation summary for Telegram."""
        if not allocations:
            return "\u2699\ufe0f Capital Allocator: No active allocations"

        composite = self._regime_state.get("composite_regime_score", 0.0)
        total_pct = sum(a["allocation_pct"] for a in allocations.values())

        lines = [f"\u2699\ufe0f Regime Allocator (composite={composite:.2f}, gross={total_pct:.1%})"]

        # Top 5 strategies by allocation
        sorted_allocs = sorted(allocations.items(), key=lambda x: x[1]["utility"], reverse=True)
        for name, alloc in sorted_allocs[:5]:
            lines.append(
                f"  #{alloc['rank']} {name}: {alloc['allocation_pct']:.1%} "
                f"(${alloc['allocation_usd']:,.0f}) utility={alloc['utility']:.2f}"
            )

        return "\n".join(lines)
