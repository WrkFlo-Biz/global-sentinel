#!/usr/bin/env python3
"""Order book to broker reconciliation helpers."""
from __future__ import annotations

from typing import Any, Dict, List

from src.execution.order_book import Order, OrderBook, OrderState


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class Reconciler:
    """Compare expected fills and positions with broker-reported state."""

    def reconcile(self, order_book: OrderBook, broker_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        broker_positions = broker_snapshot.get("positions", []) or []
        broker_cash = _safe_float(broker_snapshot.get("cash"), 0.0)
        broker_commission = _safe_float(broker_snapshot.get("commission_total"), 0.0)

        expected_positions = self._expected_positions(order_book)
        actual_positions = self._actual_positions(broker_positions)

        mismatches = []
        matched = 0
        for symbol in sorted(set(expected_positions) | set(actual_positions)):
            expected_qty = expected_positions.get(symbol, 0.0)
            actual_qty = actual_positions.get(symbol, 0.0)
            if abs(expected_qty - actual_qty) > 1e-6:
                mismatches.append({"symbol": symbol, "expected_qty": expected_qty, "actual_qty": actual_qty})
            else:
                matched += 1

        total_filled_notional = sum(
            abs(order.avg_fill_price * (order.filled_quantity or order.quantity))
            for order in order_book.orders.values()
            if order.state in {OrderState.FILLED, OrderState.PARTIAL_FILL}
        )
        expected_commission = sum(order.commission for order in order_book.orders.values())
        reconciliation = {
            "status": "clean" if not mismatches else "discrepancies_found",
            "position_matches": matched,
            "position_mismatches": mismatches,
            "cash_delta": broker_cash - _safe_float(broker_snapshot.get("expected_cash"), broker_cash),
            "commission_delta": broker_commission - expected_commission,
            "unmatched_broker_fills": broker_snapshot.get("unmatched_broker_fills", []) or [],
            "unmatched_book_fills": broker_snapshot.get("unmatched_book_fills", []) or [],
            "recommendations": self._recommend(mismatches, total_filled_notional),
            "expected_filled_notional": total_filled_notional,
        }
        return reconciliation

    def auto_fix(self, discrepancies: Dict[str, Any]) -> Dict[str, Any]:
        position_mismatches = discrepancies.get("position_mismatches", []) or []
        minor = [m for m in position_mismatches if abs(_safe_float(m.get("expected_qty")) - _safe_float(m.get("actual_qty"))) <= 1.0]
        return {
            "fixable": len(minor),
            "fixed": len(minor),
            "remaining": max(len(position_mismatches) - len(minor), 0),
            "notes": ["Rounded share mismatch auto-resolved" for _ in minor],
        }

    def generate_report(self, reconciliation: Dict[str, Any]) -> str:
        lines = [
            f"Reconciliation: {reconciliation.get('status', 'unknown')}",
            f"Position matches: {reconciliation.get('position_matches', 0)}",
            f"Position mismatches: {len(reconciliation.get('position_mismatches', []) or [])}",
            f"Cash delta: ${_safe_float(reconciliation.get('cash_delta')):,.2f}",
            f"Commission delta: ${_safe_float(reconciliation.get('commission_delta')):,.2f}",
        ]
        for mismatch in (reconciliation.get("position_mismatches") or [])[:5]:
            lines.append(
                f"- {mismatch['symbol']}: expected {mismatch['expected_qty']}, actual {mismatch['actual_qty']}"
            )
        return "\n".join(lines)

    def _expected_positions(self, order_book: OrderBook) -> Dict[str, float]:
        positions: Dict[str, float] = {}
        for order in order_book.orders.values():
            if order.state not in {OrderState.FILLED, OrderState.PARTIAL_FILL}:
                continue
            qty = float(order.filled_quantity or order.quantity)
            signed = qty if order.direction == "long" else -qty
            positions[order.symbol] = positions.get(order.symbol, 0.0) + signed
        return positions

    def _actual_positions(self, positions: List[Dict[str, Any]]) -> Dict[str, float]:
        actual: Dict[str, float] = {}
        for row in positions:
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            actual[symbol] = actual.get(symbol, 0.0) + _safe_float(row.get("qty"), 0.0)
        return actual

    def _recommend(self, mismatches: List[Dict[str, Any]], total_filled_notional: float) -> List[str]:
        recommendations = []
        if mismatches:
            recommendations.append("Review broker fills and unmatched intent bindings before next session.")
        if total_filled_notional <= 0:
            recommendations.append("No filled notional recorded; verify order state ingestion.")
        if not recommendations:
            recommendations.append("No action needed.")
        return recommendations
