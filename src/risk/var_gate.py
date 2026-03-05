#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Risk Gate (Composite)

Wraps all risk checks into a single gate:
- VaR check (parametric, 95% confidence, normal approximation)
- Exposure check (single-name + sector concentration)
- Impact budget (square-root law via ImpactBudgetGate)

Emits canonical gate records per intent.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from src.risk.square_root_impact_gate import SquareRootImpactGate
from src.risk.impact_budget_gate import ImpactBudgetGate

# Normal distribution z-score for 95% confidence
_Z_95 = 1.6449

# Default risk parameters
_DEFAULT_MAX_VAR_PCT = 2.0          # max portfolio VaR as % of gross value
_DEFAULT_SIGMA = 0.02               # daily portfolio volatility (2%)
_DEFAULT_HOLDING_PERIOD = 1         # 1-day VaR
_DEFAULT_MAX_SINGLE_NAME_PCT = 10.0  # max 10% single-name concentration
_DEFAULT_MAX_SECTOR_PCT = 25.0      # max 25% sector concentration


def _compute_parametric_var(
    portfolio_value: float,
    sigma: float = _DEFAULT_SIGMA,
    holding_period_days: int = _DEFAULT_HOLDING_PERIOD,
) -> float:
    """Parametric VaR at 95% confidence using normal approximation."""
    return portfolio_value * sigma * _Z_95 * math.sqrt(holding_period_days)


def _check_var(
    snapshot: Dict[str, Any],
    max_var_pct: float = _DEFAULT_MAX_VAR_PCT,
) -> Dict[str, Any]:
    """
    Simple parametric VaR gate.

    VaR = portfolio_value * sigma * z_score * sqrt(holding_period_days)
    Fail if VaR exceeds max_var_pct of gross portfolio value.
    """
    portfolio = snapshot.get("portfolio", {})
    gross_value = portfolio.get("gross_value", 0)

    if not gross_value:
        return {"gate": "var", "pass": True, "reason": "no_portfolio_data"}

    var_abs = _compute_parametric_var(gross_value)
    var_pct = (var_abs / gross_value) * 100.0 if gross_value else 0.0
    passed = var_pct <= max_var_pct

    return {
        "gate": "var",
        "pass": passed,
        "reason": "pass" if passed else f"var_pct={var_pct:.2f} > max={max_var_pct:.2f}",
        "var_abs": round(var_abs, 2),
        "var_pct": round(var_pct, 4),
        "gross_value": gross_value,
    }


def _check_exposure(
    intent: Dict[str, Any],
    snapshot: Dict[str, Any],
    max_single_name_pct: float = _DEFAULT_MAX_SINGLE_NAME_PCT,
    max_sector_pct: float = _DEFAULT_MAX_SECTOR_PCT,
) -> Dict[str, Any]:
    """
    Position concentration gate.

    Checks:
    1. Single-name exposure: no single position > max_single_name_pct of gross.
    2. Sector exposure: total sector weight < max_sector_pct of gross.
    """
    portfolio = snapshot.get("portfolio", {})
    gross_value = portfolio.get("gross_value", 0)

    if not gross_value:
        return {"gate": "exposure", "pass": True, "reason": "no_portfolio_data"}

    positions = portfolio.get("positions", [])
    symbol = intent.get("symbol", "")
    intent_notional = abs(float(intent.get("notional", 0) or 0))

    # --- Single-name check ---
    existing_notional = 0.0
    for pos in positions:
        if pos.get("symbol") == symbol:
            existing_notional = abs(float(pos.get("notional", 0) or pos.get("market_value", 0) or 0))
            break

    projected_single = existing_notional + intent_notional
    single_pct = (projected_single / gross_value) * 100.0
    single_pass = single_pct <= max_single_name_pct

    # --- Sector check ---
    intent_sector = intent.get("candidate_context", {}).get("sector", "")

    sector_notional = 0.0
    if intent_sector:
        for pos in positions:
            pos_sector = pos.get("sector", "")
            if pos_sector == intent_sector:
                sector_notional += abs(float(pos.get("notional", 0) or pos.get("market_value", 0) or 0))
        sector_notional += intent_notional

    sector_pct = (sector_notional / gross_value) * 100.0 if intent_sector else 0.0
    sector_pass = sector_pct <= max_sector_pct if intent_sector else True

    passed = single_pass and sector_pass
    reasons = []
    if not single_pass:
        reasons.append(f"single_name={single_pct:.2f}% > max={max_single_name_pct:.2f}%")
    if not sector_pass:
        reasons.append(f"sector={sector_pct:.2f}% > max={max_sector_pct:.2f}%")

    return {
        "gate": "exposure",
        "pass": passed,
        "reason": "pass" if passed else "; ".join(reasons),
        "single_name_pct": round(single_pct, 4),
        "sector_pct": round(sector_pct, 4) if intent_sector else None,
        "sector": intent_sector or None,
    }


class RiskGate:
    def __init__(
        self,
        *,
        impact_gate: Optional[SquareRootImpactGate] = None,
        max_var_pct: float = _DEFAULT_MAX_VAR_PCT,
        max_single_name_pct: float = _DEFAULT_MAX_SINGLE_NAME_PCT,
        max_sector_pct: float = _DEFAULT_MAX_SECTOR_PCT,
    ) -> None:
        self.impact_budget_gate = ImpactBudgetGate(impact_gate=impact_gate)
        self.max_var_pct = max_var_pct
        self.max_single_name_pct = max_single_name_pct
        self.max_sector_pct = max_sector_pct

    def check_intent(
        self,
        *,
        intent: Dict[str, Any],
        snapshot: Dict[str, Any],
        time_window_name: Optional[str] = None,
        regime: Optional[str] = None,
        runtime_flags: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run all risk gates on an intent. Returns a canonical risk decision record.
        """
        runtime_flags = runtime_flags or {}
        gates: List[Dict[str, Any]] = []

        # --- VaR gate (parametric, 95% confidence) ---
        var_result = _check_var(snapshot, max_var_pct=self.max_var_pct)
        var_pass = var_result["pass"]
        gates.append(var_result)

        # --- Exposure gate (single-name + sector concentration) ---
        exposure_result = _check_exposure(
            intent, snapshot,
            max_single_name_pct=self.max_single_name_pct,
            max_sector_pct=self.max_sector_pct,
        )
        exposure_pass = exposure_result["pass"]
        gates.append(exposure_result)

        # --- Impact budget gate ---
        impact_decision = self.impact_budget_gate.check(
            intent=intent,
            snapshot=snapshot,
            time_window_name=time_window_name,
            regime=regime,
            runtime_flags=runtime_flags,
        )
        gates.append(impact_decision.record)

        pass_all = bool(var_pass and exposure_pass and impact_decision.pass_gate)

        return {
            "intent_id": intent.get("intent_id"),
            "package_id": intent.get("package_id"),
            "router_run_id": intent.get("router_run_id"),
            "pass": pass_all,
            "time_window": time_window_name,
            "regime": regime,
            "gates": gates,
            "recommended_qty_cap": impact_decision.recommended_qty_cap,
        }
