#!/usr/bin/env python3
"""Feasibility checks for research-only multi-leg option structures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SpreadFeasibilityChecker:
    """Validate whether a candidate options spread is analyzable."""

    max_combined_spread_pct: float = 0.30

    def check(self, legs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate leg availability, combined spread, and premium math."""

        if not legs:
            return self._result(False, "no_legs_provided", {})

        missing_indexes: List[int] = []
        total_mid = 0.0
        total_half_spread = 0.0
        net_premium = 0.0

        for index, leg in enumerate(legs):
            if not self._leg_available(leg):
                missing_indexes.append(index)
                continue

            bid = _safe_float(leg.get("bid"))
            ask = _safe_float(leg.get("ask"))
            midpoint = (bid + ask) / 2.0
            half_spread = max(ask - bid, 0.0) / 2.0
            quantity = max(_safe_int(leg.get("quantity"), 1), 1)
            side = str(leg.get("side", "buy")).strip().lower()
            sign = 1.0 if side == "buy" else -1.0

            total_mid += abs(midpoint * quantity)
            total_half_spread += half_spread * quantity
            net_premium += sign * midpoint * quantity * 100.0

        if missing_indexes:
            return self._result(False, "legs_unavailable", {"missing_leg_indexes": missing_indexes})

        if total_mid <= 0:
            return self._result(False, "net_premium_not_calculable", {"total_mid": total_mid})

        combined_spread_pct = total_half_spread / total_mid
        if combined_spread_pct > self.max_combined_spread_pct:
            return self._result(
                False,
                "combined_spread_too_wide",
                {"combined_spread_pct": round(combined_spread_pct, 6), "threshold": self.max_combined_spread_pct},
            )

        return self._result(
            True,
            "spread_feasible",
            {
                "combined_spread_pct": round(combined_spread_pct, 6),
                "net_premium": round(net_premium, 6),
                "leg_count": len(legs),
            },
        )

    def _leg_available(self, leg: Dict[str, Any]) -> bool:
        bid = _safe_float(leg.get("bid"), -1.0)
        ask = _safe_float(leg.get("ask"), -1.0)
        strike = _safe_float(leg.get("strike"), -1.0)
        expiry = str(leg.get("expiry", "")).strip()
        contract_type = str(leg.get("contract_type", "")).strip().lower()
        if bid < 0 or ask < 0 or ask < bid:
            return False
        if strike <= 0 or not expiry:
            return False
        if contract_type not in {"call", "put"}:
            return False
        if leg.get("available") is False:
            return False
        return True

    def _result(self, passed: bool, reason: str, details: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "pass": passed,
            "reason": reason,
            "details": details,
            "not_for_direct_execution": True,
        }
