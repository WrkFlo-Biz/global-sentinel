#!/usr/bin/env python3
"""Liquidity gating for research-only options contract analysis."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class OptionsLiquidityFilter:
    """Filter option contracts using basic open-interest and spread limits."""

    min_open_interest: int = 100
    max_spread_pct: float = 0.15
    min_volume: int = 10

    def filter(self, contracts: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return ``(passed, rejected)`` contracts annotated with reasons."""

        passed: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for contract in contracts:
            reasons = self._rejection_reasons(contract)
            annotated = dict(contract)
            annotated["not_for_direct_execution"] = True
            annotated["liquidity_filter_passed"] = not reasons
            if reasons:
                annotated["liquidity_rejection_reasons"] = reasons
                rejected.append(annotated)
            else:
                passed.append(annotated)
        return passed, rejected

    def _rejection_reasons(self, contract: Dict[str, Any]) -> List[str]:
        reasons: List[str] = []
        oi = _safe_int(contract.get("OI"))
        volume = _safe_int(contract.get("volume"))
        bid = _safe_float(contract.get("bid"))
        ask = _safe_float(contract.get("ask"))
        if oi < self.min_open_interest:
            reasons.append(f"open_interest_below_{self.min_open_interest}")
        if volume < self.min_volume:
            reasons.append(f"volume_below_{self.min_volume}")
        if bid < 0 or ask < 0 or ask < bid:
            reasons.append("invalid_bid_ask")
            return reasons

        midpoint = (bid + ask) / 2.0 if (bid + ask) > 0 else 0.0
        if midpoint <= 0:
            reasons.append("missing_midpoint")
            return reasons

        spread_pct = (ask - bid) / midpoint
        if spread_pct > self.max_spread_pct:
            reasons.append(f"spread_pct_above_{self.max_spread_pct:.2f}")
        return reasons
