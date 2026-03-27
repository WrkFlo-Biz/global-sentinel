#!/usr/bin/env python3
"""Transaction cost analysis for filled orders."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class TCAReport:
    """Generate per-fill and aggregate TCA metrics."""

    def generate(self, filled_orders: List[Dict[str, Any]], market_data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        per_fill = []
        strategy_costs: Dict[str, List[float]] = defaultdict(list)
        hour_costs: Dict[str, List[float]] = defaultdict(list)

        for order in filled_orders:
            symbol = str(order.get("symbol") or "")
            md = market_data.get(symbol, {})
            qty = abs(_safe_float(order.get("filled_quantity", order.get("quantity")), 0.0))
            fill_price = _safe_float(order.get("avg_fill_price", order.get("fill_price")), 0.0)
            decision_price = _safe_float(order.get("decision_price", md.get("decision_price", fill_price)), fill_price)
            arrival_price = _safe_float(order.get("arrival_price", md.get("arrival_price", decision_price)), decision_price)
            vwap = _safe_float(md.get("vwap"), arrival_price)
            twap = _safe_float(md.get("twap"), arrival_price)
            side = str(order.get("direction", "long"))
            sign = 1.0 if side == "long" else -1.0
            trade_value = qty * fill_price
            implementation_shortfall = sign * (fill_price - decision_price) * qty
            slippage = sign * (fill_price - arrival_price) * qty
            market_impact = sign * (fill_price - vwap) * qty
            timing_cost = sign * (arrival_price - decision_price) * qty
            commission = _safe_float(order.get("commission"), 0.0)
            total_cost = implementation_shortfall + commission
            cost_bps = (total_cost / trade_value * 10000.0) if trade_value else 0.0
            record = {
                "order_id": order.get("order_id"),
                "symbol": symbol,
                "strategy": order.get("strategy", "unassigned"),
                "implementation_shortfall": implementation_shortfall,
                "slippage": slippage,
                "market_impact": market_impact,
                "commission": commission,
                "timing_cost": timing_cost,
                "trade_value": trade_value,
                "cost_bps": cost_bps,
                "beat_vwap": (fill_price <= vwap) if side == "long" else (fill_price >= vwap),
                "beat_twap": (fill_price <= twap) if side == "long" else (fill_price >= twap),
            }
            per_fill.append(record)
            strategy_costs[record["strategy"]].append(cost_bps)
            hour = str(order.get("filled_at", ""))[:13] or "unknown"
            hour_costs[hour].append(cost_bps)

        total_trade_value = sum(row["trade_value"] for row in per_fill)
        total_cost = sum(row["implementation_shortfall"] + row["commission"] for row in per_fill)
        return {
            "fills_analyzed": len(per_fill),
            "per_fill": per_fill,
            "total_trade_value": total_trade_value,
            "total_implementation_cost": total_cost,
            "avg_cost_bps": (total_cost / total_trade_value * 10000.0) if total_trade_value else 0.0,
            "by_strategy": {
                strategy: {
                    "fills": len(values),
                    "avg_cost_bps": (sum(values) / len(values)) if values else 0.0,
                }
                for strategy, values in strategy_costs.items()
            },
            "by_time_of_day": {
                hour: {
                    "fills": len(values),
                    "avg_cost_bps": (sum(values) / len(values)) if values else 0.0,
                }
                for hour, values in hour_costs.items()
            },
            "vwap_beats": sum(1 for row in per_fill if row["beat_vwap"]),
            "twap_beats": sum(1 for row in per_fill if row["beat_twap"]),
        }

    def format_report(self, tca: Dict[str, Any]) -> str:
        return (
            f"TCA fills={tca.get('fills_analyzed', 0)} "
            f"avg_cost_bps={_safe_float(tca.get('avg_cost_bps')):.2f} "
            f"vwap_beats={tca.get('vwap_beats', 0)}/{tca.get('fills_analyzed', 0)}"
        )

    def format_telegram(self, tca: Dict[str, Any]) -> str:
        return (
            f"TCA: avg slippage {_safe_float(tca.get('avg_cost_bps')):.1f}bps, "
            f"total cost ${_safe_float(tca.get('total_implementation_cost')):,.0f}, "
            f"VWAP beat on {tca.get('vwap_beats', 0)}/{tca.get('fills_analyzed', 0)} fills"
        )
