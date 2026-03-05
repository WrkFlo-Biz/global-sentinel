#!/usr/bin/env python3
"""
Global Sentinel V5.0 - Square-Root Market Impact Gate

Econophysics guardrail based on the universal square-root law of market impact:

    I(Q) ~ Y * sigma * sqrt(Q / V)

Where:
    I(Q) = expected price impact (in price units)
    Q    = metaorder size (shares)
    V    = average daily volume (shares)
    sigma = daily volatility (price units)
    Y    = venue/asset/liquidity constant (O(1), typically 0.5-2.0)

References:
    Bouchaud, J.-P. et al. "The square-root law of market impact"
    Kyle & Obizhaeva (2016), Almgren et al. (2005)

Integration points:
    - OrderIntentRegistry (intent creation sizing check)
    - Risk gate checks (pre-submission)
    - TimeWindowPolicyEngine (impact budget scales with window multiplier)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# Default Y constant per regime (conservative estimates)
DEFAULT_Y_BY_REGIME = {
    "normal": 1.0,
    "elevated": 1.5,
    "crisis": 2.5,
    "manual_review": 2.0,
}

# Default max participation rate (Q/V) cap
DEFAULT_MAX_PARTICIPATION_RATE = 0.01  # 1% of ADV

# Default max impact budget in bps
DEFAULT_MAX_IMPACT_BUDGET_BPS = 50.0


def estimate_impact_bps(
    qty: float,
    adv_shares: float,
    sigma_daily_pct: float,
    y_constant: float = 1.0,
) -> float:
    """
    Estimate expected price impact in basis points using the square-root law.

    Args:
        qty: Order quantity (shares)
        adv_shares: Average daily volume (shares)
        sigma_daily_pct: Daily volatility as percentage (e.g., 2.0 for 2%)
        y_constant: Venue/liquidity constant (higher = worse impact)

    Returns:
        Expected impact in basis points
    """
    if adv_shares <= 0 or qty <= 0:
        return 0.0

    participation_rate = qty / adv_shares
    # Impact in % terms: Y * sigma * sqrt(Q/V)
    impact_pct = y_constant * sigma_daily_pct * (participation_rate ** 0.5)
    return impact_pct * 100.0  # convert % to bps


def recommended_qty_cap(
    adv_shares: float,
    sigma_daily_pct: float,
    max_impact_bps: float,
    y_constant: float = 1.0,
) -> float:
    """
    Compute the maximum order quantity that stays within the impact budget.

    Derived from: max_impact_bps = Y * sigma * sqrt(Q/V) * 100
    Solving for Q: Q = V * (max_impact_bps / (100 * Y * sigma))^2
    """
    if adv_shares <= 0 or sigma_daily_pct <= 0 or y_constant <= 0:
        return 0.0

    ratio = max_impact_bps / (100.0 * y_constant * sigma_daily_pct)
    max_qty = adv_shares * (ratio ** 2)
    return max(0.0, max_qty)


def resolve_y_constant(
    regime: Optional[str] = None,
    time_window_multiplier: float = 1.0,
    spread_widened: bool = False,
    volatility_spike: bool = False,
    y_overrides: Optional[Dict[str, float]] = None,
) -> float:
    """
    Resolve the Y constant based on market conditions.

    In stressed conditions (spread widening, vol spikes, open/close windows),
    Y increases — meaning the same order size has MORE impact.
    """
    y_map = dict(DEFAULT_Y_BY_REGIME)
    if y_overrides:
        y_map.update(y_overrides)

    base_y = y_map.get(str(regime or "normal").lower(), 1.0)

    # Time window stress: open/close have higher impact
    y = base_y * time_window_multiplier

    # Microstructure stress adjustments
    if spread_widened:
        y *= 1.3
    if volatility_spike:
        y *= 1.5

    return y


class SquareRootImpactGate:
    """
    Risk gate that blocks or downsizes orders when expected impact exceeds edge.
    """

    def __init__(
        self,
        max_participation_rate: float = DEFAULT_MAX_PARTICIPATION_RATE,
        max_impact_budget_bps: float = DEFAULT_MAX_IMPACT_BUDGET_BPS,
        y_overrides: Optional[Dict[str, float]] = None,
        time_window_impact_multipliers: Optional[Dict[str, float]] = None,
    ):
        self.max_participation_rate = max_participation_rate
        self.max_impact_budget_bps = max_impact_budget_bps
        self.y_overrides = y_overrides or {}
        # Per-window multipliers for impact budget (tighter in open/close)
        self.time_window_impact_multipliers = time_window_impact_multipliers or {
            "pre_market": 1.8,
            "open_bell": 1.5,
            "morning": 1.0,
            "midday_lull": 0.8,
            "afternoon": 1.0,
            "power_hour": 1.4,
            "close_bell": 1.6,
            "post_market": 2.0,
        }

    def evaluate(
        self,
        qty: float,
        adv_shares: float,
        sigma_daily_pct: float,
        regime: Optional[str] = None,
        time_window_name: Optional[str] = None,
        spread_widened: bool = False,
        volatility_spike: bool = False,
        expected_edge_bps: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate whether an order passes the impact gate.

        Returns:
            Dict with:
                - impact_budget_pass: bool
                - participation_rate_pass: bool
                - gate_pass: bool (both checks pass)
                - expected_impact_bps: float
                - participation_rate: float
                - recommended_qty_cap: float
                - details: dict with full breakdown
        """
        if adv_shares <= 0:
            return {
                "gate_pass": False,
                "impact_budget_pass": False,
                "participation_rate_pass": False,
                "expected_impact_bps": None,
                "participation_rate": None,
                "recommended_qty_cap": 0,
                "reason": "adv_shares_zero_or_missing",
                "details": {"qty": qty, "adv_shares": adv_shares},
            }

        tw_multiplier = self.time_window_impact_multipliers.get(
            str(time_window_name or "").lower(), 1.0
        )

        y = resolve_y_constant(
            regime=regime,
            time_window_multiplier=tw_multiplier,
            spread_widened=spread_widened,
            volatility_spike=volatility_spike,
            y_overrides=self.y_overrides,
        )

        participation_rate = qty / adv_shares
        impact_bps = estimate_impact_bps(qty, adv_shares, sigma_daily_pct, y)

        # Effective budget: tighter during high-impact windows
        effective_budget_bps = self.max_impact_budget_bps / tw_multiplier

        participation_pass = participation_rate <= self.max_participation_rate
        impact_pass = impact_bps <= effective_budget_bps

        # If expected edge is provided, also check impact < edge
        edge_pass = True
        if expected_edge_bps is not None and expected_edge_bps > 0:
            edge_pass = impact_bps < expected_edge_bps

        gate_pass = participation_pass and impact_pass and edge_pass

        qty_cap = recommended_qty_cap(
            adv_shares, sigma_daily_pct, effective_budget_bps, y
        )

        reasons = []
        if not participation_pass:
            reasons.append(
                f"participation_rate={participation_rate:.6f} > max={self.max_participation_rate}"
            )
        if not impact_pass:
            reasons.append(
                f"expected_impact={impact_bps:.2f}bps > budget={effective_budget_bps:.2f}bps"
            )
        if not edge_pass:
            reasons.append(
                f"expected_impact={impact_bps:.2f}bps >= edge={expected_edge_bps:.2f}bps"
            )

        return {
            "gate_pass": gate_pass,
            "impact_budget_pass": impact_pass,
            "participation_rate_pass": participation_pass,
            "edge_pass": edge_pass,
            "expected_impact_bps": round(impact_bps, 4),
            "participation_rate": round(participation_rate, 8),
            "effective_budget_bps": round(effective_budget_bps, 2),
            "recommended_qty_cap": round(qty_cap, 2),
            "reasons": reasons,
            "details": {
                "qty": qty,
                "adv_shares": adv_shares,
                "sigma_daily_pct": sigma_daily_pct,
                "y_constant": round(y, 4),
                "regime": regime,
                "time_window_name": time_window_name,
                "time_window_multiplier": tw_multiplier,
                "spread_widened": spread_widened,
                "volatility_spike": volatility_spike,
                "expected_edge_bps": expected_edge_bps,
            },
        }

    def downsize_to_budget(
        self,
        qty: float,
        adv_shares: float,
        sigma_daily_pct: float,
        regime: Optional[str] = None,
        time_window_name: Optional[str] = None,
        spread_widened: bool = False,
        volatility_spike: bool = False,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        If qty exceeds impact budget, return the downsized quantity.
        Returns (adjusted_qty, gate_result).
        """
        result = self.evaluate(
            qty=qty,
            adv_shares=adv_shares,
            sigma_daily_pct=sigma_daily_pct,
            regime=regime,
            time_window_name=time_window_name,
            spread_widened=spread_widened,
            volatility_spike=volatility_spike,
        )

        if result["gate_pass"]:
            return qty, result

        cap = result["recommended_qty_cap"]
        adjusted = max(1, int(min(qty, cap)))
        result["adjusted_qty"] = adjusted
        result["original_qty"] = qty
        result["downsized"] = adjusted < qty
        return adjusted, result
