#!/usr/bin/env python3
"""Append-only order lifecycle tracking for Global Sentinel."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderState(Enum):
    IDEA = "idea"
    PENDING_APPROVAL = "pending"
    APPROVED = "approved"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acked"
    PARTIAL_FILL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ERROR = "error"


VALID_TRANSITIONS = {
    OrderState.IDEA: {OrderState.PENDING_APPROVAL, OrderState.APPROVED, OrderState.CANCELLED},
    OrderState.PENDING_APPROVAL: {OrderState.APPROVED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.APPROVED: {OrderState.VALIDATED, OrderState.REJECTED, OrderState.CANCELLED},
    OrderState.VALIDATED: {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.CANCELLED},
    OrderState.SUBMITTED: {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.ERROR, OrderState.CANCELLED},
    OrderState.ACKNOWLEDGED: {OrderState.PARTIAL_FILL, OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.PARTIAL_FILL: {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
}


@dataclass
class Order:
    order_id: str
    symbol: str
    direction: str
    quantity: int
    price_type: str = "market"
    limit_price: Optional[float] = None
    strategy: str = ""
    account: str = ""
    state: OrderState = OrderState.IDEA
    broker_order_id: Optional[str] = None
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    slippage_bps: float = 0.0
    created_at: str = ""
    approved_at: Optional[str] = None
    submitted_at: Optional[str] = None
    filled_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    trade_idea_id: Optional[str] = None
    analog_match: Optional[str] = None
    chokepoint_score: Optional[float] = None
    strategy_ref: Optional[str] = None
    decision_price: Optional[float] = None
    arrival_price: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    state_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = iso_now()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


class OrderBook:
    """In-memory order book with append-only audit logs."""

    def __init__(self, repo_root: Optional[Path] = None, audit_relpath: str = "logs/execution/order_book_audit.jsonl"):
        self.repo_root = repo_root
        self.orders: Dict[str, Order] = {}
        self._state_log: List[Dict[str, Any]] = []
        self.audit_path: Optional[Path] = None
        if repo_root is not None:
            self.audit_path = repo_root / audit_relpath
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def create_order(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        strategy: str,
        account: str,
        **kwargs: Any,
    ) -> Order:
        seed = f"{symbol}|{direction}|{quantity}|{strategy}|{account}|{iso_now()}"
        order_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        order = Order(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            quantity=int(quantity),
            strategy=strategy,
            account=account,
            **kwargs,
        )
        self.orders[order_id] = order
        self._log_transition(order, None, OrderState.IDEA, "created", None)
        return order

    def transition(
        self,
        order_id: str,
        new_state: OrderState,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        order = self.orders.get(order_id)
        if order is None:
            return False

        old_state = order.state
        allowed = VALID_TRANSITIONS.get(old_state, set())
        terminal = {
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.ERROR,
        }
        if old_state in terminal or new_state not in allowed:
            self._log_transition(order, old_state, new_state, f"invalid_transition:{reason}", metadata)
            return False

        order.state = new_state
        stamp = iso_now()
        if new_state == OrderState.APPROVED:
            order.approved_at = stamp
        elif new_state == OrderState.SUBMITTED:
            order.submitted_at = stamp
        elif new_state in {OrderState.FILLED, OrderState.PARTIAL_FILL}:
            order.filled_at = stamp
        elif new_state == OrderState.REJECTED:
            order.rejection_reason = reason

        if metadata:
            if "broker_order_id" in metadata:
                order.broker_order_id = metadata["broker_order_id"]
            if "filled_quantity" in metadata:
                order.filled_quantity = int(metadata["filled_quantity"] or 0)
            if "avg_fill_price" in metadata:
                order.avg_fill_price = float(metadata["avg_fill_price"] or 0.0)
            if "commission" in metadata:
                order.commission = float(metadata["commission"] or 0.0)
            if "slippage_bps" in metadata:
                order.slippage_bps = float(metadata["slippage_bps"] or 0.0)
            order.metadata.update(metadata)

        self._log_transition(order, old_state, new_state, reason, metadata)
        return True

    def active_orders(self) -> List[Order]:
        terminal = {
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.ERROR,
        }
        return [order for order in self.orders.values() if order.state not in terminal]

    def by_strategy(self) -> Dict[str, List[Order]]:
        grouped: Dict[str, List[Order]] = {}
        for order in self.orders.values():
            grouped.setdefault(order.strategy or "unassigned", []).append(order)
        return grouped

    def by_account(self) -> Dict[str, List[Order]]:
        grouped: Dict[str, List[Order]] = {}
        for order in self.orders.values():
            grouped.setdefault(order.account or "unassigned", []).append(order)
        return grouped

    def daily_summary(self, day_prefix: Optional[str] = None) -> Dict[str, Any]:
        day = day_prefix or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        todays = [o for o in self.orders.values() if o.created_at.startswith(day)]
        filled = [o for o in todays if o.state == OrderState.FILLED]
        slipped = [o.slippage_bps for o in todays if o.slippage_bps]
        return {
            "date": day,
            "total_orders": len(todays),
            "filled": len(filled),
            "rejected": sum(1 for o in todays if o.state == OrderState.REJECTED),
            "cancelled": sum(1 for o in todays if o.state == OrderState.CANCELLED),
            "total_filled_notional": sum(abs(o.avg_fill_price * o.filled_quantity) for o in filled),
            "total_commission": sum(o.commission for o in todays),
            "avg_slippage_bps": (sum(slipped) / len(slipped)) if slipped else 0.0,
            "by_strategy": {
                strategy: {
                    "count": len(orders),
                    "filled": sum(1 for o in orders if o.state == OrderState.FILLED),
                }
                for strategy, orders in self.by_strategy().items()
            },
            "rejection_reasons": [
                {"order_id": o.order_id, "symbol": o.symbol, "reason": o.rejection_reason}
                for o in todays
                if o.state == OrderState.REJECTED
            ],
        }

    def export_audit_log(self, path: str) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self._state_log, indent=2), encoding="utf-8")

    def _log_transition(
        self,
        order: Order,
        old_state: Optional[OrderState],
        new_state: OrderState,
        reason: str,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        entry = {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "old_state": old_state.value if old_state else None,
            "new_state": new_state.value,
            "reason": reason,
            "timestamp_utc": iso_now(),
            "metadata": metadata or {},
        }
        self._state_log.append(entry)
        order.state_history.append(entry)
        if self.audit_path is not None:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
