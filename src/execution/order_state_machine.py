#!/usr/bin/env python3
"""Formal order state machine with validated transitions and audit metadata."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from src.core.structured_logger import get_logger


class OrderState(Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    ERROR = "error"


VALID_TRANSITIONS = {
    OrderState.DRAFT: {OrderState.VALIDATED, OrderState.CANCELLED},
    OrderState.VALIDATED: {OrderState.SUBMITTED, OrderState.CANCELLED, OrderState.REJECTED},
    OrderState.SUBMITTED: {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.ERROR, OrderState.CANCELLED},
    OrderState.ACKNOWLEDGED: {OrderState.PARTIAL_FILL, OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.PARTIAL_FILL: {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.EXPIRED: set(),
    OrderState.REJECTED: set(),
    OrderState.ERROR: set(),
}

TERMINAL_STATES = {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED, OrderState.REJECTED, OrderState.ERROR}


class InvalidTransitionError(Exception):
    """Raised when an order transition is not legal."""


class OrderStateMachine:
    """Validate and record legal order lifecycle transitions."""

    def __init__(self):
        self._history: List[Dict[str, Any]] = []
        self._current_state: Dict[str, OrderState] = {}
        self._logger = get_logger("order_state_machine")

    @staticmethod
    def _coerce(state: OrderState | str) -> OrderState:
        return state if isinstance(state, OrderState) else OrderState(str(state))

    def transition(self, order_id: str, current_state: OrderState | str, target_state: OrderState | str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        current = self._coerce(current_state)
        target = self._coerce(target_state)
        valid_targets = VALID_TRANSITIONS.get(current, set())
        if target not in valid_targets:
            raise InvalidTransitionError(
                f"Order {order_id}: cannot transition from {current.value} to {target.value}. "
                f"Valid targets: {[state.value for state in valid_targets]}"
            )

        record = {
            "order_id": order_id,
            "from_state": current.value,
            "to_state": target.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": uuid.uuid4().hex[:16],
            "metadata": metadata or {},
        }
        self._history.append(record)
        self._current_state[order_id] = target
        self._logger.info("order_state_transition", order_id=order_id, from_state=current.value, to_state=target.value, trace_id=record["trace_id"])
        return True

    def current_state(self, order_id: str) -> Optional[OrderState]:
        return self._current_state.get(order_id)

    def is_terminal(self, state: OrderState | str) -> bool:
        return self._coerce(state) in TERMINAL_STATES

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def order_history(self, order_id: str) -> List[Dict[str, Any]]:
        return [row for row in self._history if row["order_id"] == order_id]
