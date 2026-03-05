#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Impact Budget Gate (Schema-Aware)

Wraps SquareRootImpactGate with the canonical intent schema
(intent_id, package_id, router_run_id, runtime_flags).

Integrates with TimeWindowPolicyEngine for window-aware impact multipliers
and produces auditable gate decision records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.risk.square_root_impact_gate import SquareRootImpactGate


DEFAULTS = {
    "impact_budget_bps": 15.0,
    "max_participation_rate": 0.02,
    "y_coeff_stress": 1.4,
    "stress_window_multiplier": 1.25,
}


def _get_symbol(intent: Dict[str, Any]) -> str:
    return str(intent.get("symbol") or intent.get("asset") or "")


def _get_qty(intent: Dict[str, Any]) -> float:
    for k in ("qty", "quantity", "shares", "notional_qty"):
        if k in intent and intent[k] is not None:
            try:
                return float(intent[k])
            except Exception:
                pass
    # Check nested order_request
    order_req = intent.get("order_request") or {}
    for k in ("qty", "quantity", "shares"):
        if k in order_req and order_req[k] is not None:
            try:
                return float(order_req[k])
            except Exception:
                pass
    return 0.0


def _get_float(d: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(d.get(key, default))
    except Exception:
        return float(default)


def _stress_mode(runtime_flags: Dict[str, Any]) -> bool:
    return bool(
        runtime_flags.get("incident_mode")
        or runtime_flags.get("microstructure_stress")
        or runtime_flags.get("stale_spike")
        or runtime_flags.get("reject_spike")
    )


@dataclass(frozen=True)
class ImpactGateDecision:
    pass_gate: bool
    expected_impact_bps: float
    recommended_qty_cap: float
    reason: str
    record: Dict[str, Any]


class ImpactBudgetGate:
    """
    Schema-aware impact budget gate.

    Provide ADV/sigma via snapshot["market_microstructure"][symbol].
    Delegates to SquareRootImpactGate for the actual math.
    """

    def __init__(
        self,
        *,
        impact_gate: Optional[SquareRootImpactGate] = None,
        defaults: Optional[Dict[str, float]] = None,
    ) -> None:
        self.impact_gate = impact_gate or SquareRootImpactGate()
        self.defaults = {**DEFAULTS, **(defaults or {})}

    def check(
        self,
        *,
        intent: Dict[str, Any],
        snapshot: Dict[str, Any],
        time_window_name: Optional[str] = None,
        regime: Optional[str] = None,
        runtime_flags: Optional[Dict[str, Any]] = None,
    ) -> ImpactGateDecision:
        runtime_flags = runtime_flags or {}
        symbol = _get_symbol(intent)
        qty = _get_qty(intent)

        # Pull ADV and sigma from snapshot
        m = (snapshot.get("market_microstructure") or {}).get(symbol) or {}
        adv = _get_float(m, "adv_shares", 0.0)
        sigma = _get_float(m, "sigma_daily_pct", 0.0)

        # If missing microstructure data, fail closed
        if adv <= 0.0 or sigma <= 0.0:
            pass_gate = False
            reason = "missing_microstructure_data"
            record = self._build_record(
                pass_gate=False,
                reason=reason,
                intent=intent,
                symbol=symbol,
                qty=qty,
                adv=adv,
                sigma=sigma,
                time_window_name=time_window_name,
                regime=regime,
                runtime_flags=runtime_flags,
            )
            return ImpactGateDecision(False, 0.0, 0.0, reason, record)

        # Delegate to SquareRootImpactGate
        gate_result = self.impact_gate.evaluate(
            qty=qty,
            adv_shares=adv,
            sigma_daily_pct=sigma,
            regime=regime,
            time_window_name=time_window_name,
            spread_widened=bool(runtime_flags.get("spread_widened")),
            volatility_spike=bool(runtime_flags.get("volatility_spike")),
            expected_edge_bps=_get_float(runtime_flags, "expected_edge_bps", 0.0) or None,
        )

        pass_gate = bool(gate_result.get("gate_pass", False))
        reason = "pass" if pass_gate else "; ".join(gate_result.get("reasons", ["impact_budget_or_participation_exceeded"]))

        record = self._build_record(
            pass_gate=pass_gate,
            reason=reason,
            intent=intent,
            symbol=symbol,
            qty=qty,
            adv=adv,
            sigma=sigma,
            time_window_name=time_window_name,
            regime=regime,
            runtime_flags=runtime_flags,
            gate_result=gate_result,
        )

        return ImpactGateDecision(
            pass_gate=pass_gate,
            expected_impact_bps=float(gate_result.get("expected_impact_bps", 0.0) or 0.0),
            recommended_qty_cap=float(gate_result.get("recommended_qty_cap", 0.0) or 0.0),
            reason=reason,
            record=record,
        )

    def _build_record(
        self,
        *,
        pass_gate: bool,
        reason: str,
        intent: Dict[str, Any],
        symbol: str,
        qty: float,
        adv: float,
        sigma: float,
        time_window_name: Optional[str],
        regime: Optional[str],
        runtime_flags: Dict[str, Any],
        gate_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "gate": "impact_budget",
            "pass": pass_gate,
            "reason": reason,
            "intent_id": intent.get("intent_id"),
            "package_id": intent.get("package_id"),
            "router_run_id": intent.get("router_run_id"),
            "symbol": symbol,
            "qty": qty,
            "adv_shares": adv,
            "sigma_daily_pct": sigma,
            "time_window": time_window_name,
            "regime": regime,
        }
        if gate_result:
            record["expected_impact_bps"] = gate_result.get("expected_impact_bps")
            record["participation_rate"] = gate_result.get("participation_rate")
            record["effective_budget_bps"] = gate_result.get("effective_budget_bps")
            record["recommended_qty_cap"] = gate_result.get("recommended_qty_cap")
            record["y_constant"] = (gate_result.get("details") or {}).get("y_constant")
            record["time_window_multiplier"] = (gate_result.get("details") or {}).get("time_window_multiplier")
        return record
