#!/usr/bin/env python3
"""Real-time buying power calculations."""
from __future__ import annotations

from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class BuyingPowerTracker:
    """Compute current and projected buying power."""

    def compute(self, account_state: Dict[str, Any], pending_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        equity = _safe_float(account_state.get("equity"), 0.0)
        cash = _safe_float(account_state.get("cash"), 0.0)
        market_value_long = _safe_float(account_state.get("market_value_long"), 0.0)
        market_value_short = _safe_float(account_state.get("market_value_short"), 0.0)
        maintenance_margin = _safe_float(account_state.get("maintenance_margin"), 0.0)
        available_buying_power = _safe_float(account_state.get("buying_power"), 0.0)
        pending_order_reserve = sum(self._order_notional(order) for order in pending_orders)
        effective_buying_power = max(available_buying_power - pending_order_reserve, 0.0)
        margin_usage_pct = ((market_value_long + market_value_short + pending_order_reserve) / equity) if equity else 0.0
        distance = ((available_buying_power - maintenance_margin) / max(equity, 1.0)) if equity else 0.0
        return {
            "equity": equity,
            "cash": cash,
            "market_value_long": market_value_long,
            "market_value_short": market_value_short,
            "maintenance_margin": maintenance_margin,
            "available_buying_power": available_buying_power,
            "pending_order_reserve": pending_order_reserve,
            "effective_buying_power": effective_buying_power,
            "margin_usage_pct": margin_usage_pct,
            "distance_to_margin_call_pct": distance,
        }

    def will_this_order_fit(self, order: Dict[str, Any], current_state: Dict[str, Any]) -> Dict[str, Any]:
        order_notional = self._order_notional(order)
        remaining_after = _safe_float(current_state.get("effective_buying_power"), 0.0) - order_notional
        equity = _safe_float(current_state.get("equity"), 0.0)
        market_value_long = _safe_float(current_state.get("market_value_long"), 0.0)
        market_value_short = _safe_float(current_state.get("market_value_short"), 0.0)
        usage_after = ((market_value_long + market_value_short + order_notional) / equity) if equity else 1.0
        fits = remaining_after >= 0.0
        return {
            "fits": fits,
            "reason": "OK" if fits else f"Would exceed buying power by ${abs(remaining_after):,.2f}",
            "remaining_after": remaining_after,
            "margin_usage_after_pct": usage_after,
        }

    def _order_notional(self, order: Dict[str, Any]) -> float:
        explicit = _safe_float(order.get("notional"), 0.0)
        if explicit > 0:
            return explicit
        qty = abs(_safe_float(order.get("qty", order.get("quantity", 0.0)), 0.0))
        px = _safe_float(order.get("limit_price"), _safe_float(order.get("decision_price"), _safe_float(order.get("avg_fill_price"), 0.0)))
        return qty * px
