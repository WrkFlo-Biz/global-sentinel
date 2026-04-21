#!/usr/bin/env python3
"""Pre- and post-trade compliance checks."""
from __future__ import annotations

from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class ComplianceEngine:
    """Institutional-style checks layered around order submission."""

    def pre_trade_check(self, order: Dict[str, Any], portfolio: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
        violations: List[str] = []
        symbol = str(order.get("symbol") or "")
        restricted = set(rules.get("restricted_symbols", []) or [])
        if symbol in restricted:
            violations.append(f"{symbol} is on the restricted list")

        liquidity_floor = _safe_float(rules.get("min_avg_daily_volume"), 0.0)
        adv = _safe_float(order.get("avg_daily_volume"), 0.0)
        if liquidity_floor and adv and adv < liquidity_floor:
            violations.append(f"{symbol} ADV {adv:,.0f} below minimum {liquidity_floor:,.0f}")

        per_name_limit = _safe_float(rules.get("max_single_name_pct"), 0.0)
        equity = _safe_float(portfolio.get("equity"), 0.0)
        existing_value = _safe_float(((portfolio.get("positions") or {}).get(symbol) or {}).get("market_value"), 0.0)
        order_notional = _safe_float(order.get("notional"), 0.0)
        if order_notional <= 0.0:
            order_notional = abs(_safe_float(order.get("qty"), 0.0) * _safe_float(order.get("limit_price"), _safe_float(order.get("decision_price"), 0.0)))
        if per_name_limit and equity and (existing_value + order_notional) > (equity * per_name_limit):
            violations.append(f"{symbol} would breach single-name limit")

        strategy_limits = rules.get("strategy_limits", {}) or {}
        strategy_name = str(order.get("strategy") or "")
        if strategy_name and strategy_name in strategy_limits:
            current_count = int((portfolio.get("strategy_position_counts") or {}).get(strategy_name, 0))
            max_count = int(strategy_limits[strategy_name].get("max_positions", 999))
            if current_count >= max_count:
                violations.append(f"{strategy_name} already at max positions")

        if str(order.get("direction") or order.get("side") or "long").lower() in {"short", "sell"} and rules.get("require_shortable", False):
            if order.get("shortable") is False:
                violations.append(f"{symbol} is not shortable")

        return {
            "passed": not violations,
            "violations": violations,
            "checked_symbol": symbol,
        }

    def post_trade_check(self, filled_orders: List[Dict[str, Any]], portfolio: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        for order in filled_orders:
            if _safe_float(order.get("avg_fill_price"), 0.0) <= 0.0:
                issues.append(f"{order.get('order_id')} missing avg fill price")
            if order.get("commission") is None:
                issues.append(f"{order.get('order_id')} missing commission")
        return {
            "passed": not issues,
            "issues": issues,
            "positions_after": len(portfolio.get("positions", {}) or {}),
        }

    def generate_compliance_report(self, date: str, pre_trade: Optional[List[Dict[str, Any]]] = None, post_trade: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        pre = pre_trade or []
        post = post_trade or []
        return {
            "date": date,
            "pre_trade_checks": len(pre),
            "post_trade_checks": len(post),
            "pre_trade_failures": sum(1 for row in pre if not row.get("passed", False)),
            "post_trade_failures": sum(1 for row in post if not row.get("passed", False)),
        }
