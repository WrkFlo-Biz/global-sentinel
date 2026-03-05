#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Risk Gate (Composite)

Wraps all risk checks into a single gate:
- VaR/exposure (placeholder — wire in real logic when ready)
- Impact budget (square-root law via ImpactBudgetGate)

Emits canonical gate records per intent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.risk.square_root_impact_gate import SquareRootImpactGate
from src.risk.impact_budget_gate import ImpactBudgetGate


class RiskGate:
    def __init__(
        self,
        *,
        impact_gate: Optional[SquareRootImpactGate] = None,
    ) -> None:
        self.impact_budget_gate = ImpactBudgetGate(impact_gate=impact_gate)

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

        # --- VaR gate (placeholder) ---
        var_pass = True
        gates.append({"gate": "var", "pass": var_pass, "reason": "pass"})

        # --- Exposure gate (placeholder) ---
        exposure_pass = True
        gates.append({"gate": "exposure", "pass": exposure_pass, "reason": "pass"})

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
