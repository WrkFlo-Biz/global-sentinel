#!/usr/bin/env python3
"""Research-only options margin checks for manual strategy review."""
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class OptionsMarginPolicy:
    """Apply conservative margin heuristics to common options structures."""

    contract_multiplier: int = 100
    min_naked_equity: float = 25_000.0
    max_margin_fraction: float = 0.50
    naked_short_margin_ratio: float = 0.20

    def check_margin(self, strategy: Dict[str, Any], account_equity: float) -> Dict[str, Any]:
        """Validate covered calls, defined-risk spreads, and naked shorts."""

        equity = max(_safe_float(account_equity, 0.0), 0.0)
        strategy_type = str(strategy.get("strategy_type", "")).strip().lower()
        legs = list(strategy.get("legs") or [])
        if equity <= 0:
            return self._result(False, "invalid_account_equity", 0.0, equity, strategy_type)

        if strategy_type == "covered_call":
            return self._check_covered_call(strategy, equity)
        if strategy_type in {"vertical_spread", "credit_spread", "debit_spread"}:
            return self._check_vertical_spread(strategy, equity)
        if strategy_type in {"naked_call", "naked_put", "naked_position"}:
            return self._check_naked_position(strategy, equity)

        # Infer from legs if an explicit strategy type is not supplied.
        short_legs = [leg for leg in legs if str(leg.get("side", "")).lower() == "sell"]
        long_legs = [leg for leg in legs if str(leg.get("side", "")).lower() == "buy"]
        if short_legs and long_legs:
            return self._check_vertical_spread(strategy, equity)
        if short_legs:
            return self._check_naked_position(strategy, equity)
        return self._result(True, "no_short_margin_required", 0.0, equity, strategy_type or "long_premium_only")

    def _check_covered_call(self, strategy: Dict[str, Any], equity: float) -> Dict[str, Any]:
        legs = list(strategy.get("legs") or [])
        short_calls = [
            leg
            for leg in legs
            if str(leg.get("side", "")).lower() == "sell" and str(leg.get("contract_type", "")).lower() == "call"
        ]
        contracts = sum(max(_safe_int(leg.get("quantity"), 1), 1) for leg in short_calls)
        required_shares = contracts * self.contract_multiplier
        shares_owned = _safe_int(strategy.get("shares_owned"), 0)
        if shares_owned < required_shares:
            return self._result(
                False,
                "insufficient_shares_for_covered_call",
                0.0,
                equity,
                "covered_call",
                {"required_shares": required_shares, "shares_owned": shares_owned},
            )
        return self._result(
            True,
            "covered_call_margin_ok",
            0.0,
            equity,
            "covered_call",
            {"required_shares": required_shares, "shares_owned": shares_owned},
        )

    def _check_vertical_spread(self, strategy: Dict[str, Any], equity: float) -> Dict[str, Any]:
        legs = list(strategy.get("legs") or [])
        if len(legs) < 2:
            return self._result(False, "insufficient_legs_for_spread", 0.0, equity, "vertical_spread")

        strikes = sorted(_safe_float(leg.get("strike"), 0.0) for leg in legs)
        if strikes[0] <= 0 or strikes[-1] <= 0:
            return self._result(False, "invalid_spread_strikes", 0.0, equity, "vertical_spread")

        quantities = [max(_safe_int(leg.get("quantity"), 1), 1) for leg in legs]
        max_contracts = max(quantities)
        width = strikes[-1] - strikes[0]
        if width <= 0:
            return self._result(False, "spread_width_not_positive", 0.0, equity, "vertical_spread")

        net_credit = 0.0
        for leg in legs:
            bid = _safe_float(leg.get("bid"))
            ask = _safe_float(leg.get("ask"))
            midpoint = (bid + ask) / 2.0
            side = str(leg.get("side", "buy")).strip().lower()
            sign = 1.0 if side == "sell" else -1.0
            net_credit += sign * midpoint

        required_margin = max((width - max(net_credit, 0.0)) * self.contract_multiplier * max_contracts, 0.0)
        return self._margin_fraction_result(required_margin, equity, "vertical_spread")

    def _check_naked_position(self, strategy: Dict[str, Any], equity: float) -> Dict[str, Any]:
        if equity < self.min_naked_equity:
            return self._result(
                False,
                "account_equity_below_naked_minimum",
                0.0,
                equity,
                "naked_position",
                {"minimum_equity": self.min_naked_equity},
            )

        legs = [leg for leg in list(strategy.get("legs") or []) if str(leg.get("side", "")).lower() == "sell"]
        notional_base = 0.0
        for leg in legs:
            strike = _safe_float(leg.get("strike"), 0.0)
            underlying_price = _safe_float(leg.get("underlying_price"), strike)
            quantity = max(_safe_int(leg.get("quantity"), 1), 1)
            notional_base += max(strike, underlying_price) * self.contract_multiplier * quantity

        required_margin = notional_base * self.naked_short_margin_ratio
        return self._margin_fraction_result(required_margin, equity, "naked_position")

    def _margin_fraction_result(self, required_margin: float, equity: float, strategy_type: str) -> Dict[str, Any]:
        margin_fraction = required_margin / equity if equity > 0 else 1.0
        if margin_fraction > self.max_margin_fraction:
            return self._result(
                False,
                "required_margin_exceeds_policy_fraction",
                required_margin,
                equity,
                strategy_type,
                {"margin_fraction": round(margin_fraction, 6), "threshold": self.max_margin_fraction},
            )
        return self._result(
            True,
            "margin_ok",
            required_margin,
            equity,
            strategy_type,
            {"margin_fraction": round(margin_fraction, 6), "threshold": self.max_margin_fraction},
        )

    def _result(
        self,
        passed: bool,
        reason: str,
        required_margin: float,
        account_equity: float,
        strategy_type: str,
        details: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "pass": passed,
            "reason": reason,
            "required_margin": round(required_margin, 6),
            "account_equity": round(account_equity, 6),
            "strategy_type": strategy_type,
            "not_for_direct_execution": True,
        }
        if details:
            payload["details"] = details
        return payload
