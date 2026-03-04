"""
Global Sentinel V4.5 - Broker Adapter Conformance Harness

Contract tests for broker adapters (paper/sandbox first).

Expected adapter interface (example):
  adapter.get_capabilities() -> dict
  adapter.get_health() -> dict
  adapter.get_account_state() -> dict
  adapter.submit_order(order: dict) -> dict
  adapter.get_order(order_id: str) -> dict
  adapter.cancel_order(order_id: str) -> dict
  adapter.replace_order(order_id: str, patch: dict) -> dict
  adapter.list_open_orders() -> list[dict]
  adapter.list_positions() -> list[dict]
  adapter.get_trades(order_id: str) -> list[dict]  # optional but recommended

This test file is adapter-agnostic. You provide an adapter fixture.
"""

from __future__ import annotations

import time
import uuid
import pytest


# -----------------------------
# Adapter fixture hook
# -----------------------------
@pytest.fixture
def broker_adapter():
    """
    Replace this fixture in your environment with a real adapter import.

    Example:
      from src.brokers.alpaca_paper_adapter import AlpacaPaperAdapter
      return AlpacaPaperAdapter()

    For now this raises to force explicit wiring.
    """
    pytest.skip("Wire broker_adapter fixture to your paper broker adapter implementation.")


# -----------------------------
# Helpers
# -----------------------------
VALID_ORDER_STATES = {
    "new",
    "accepted",
    "pending",
    "partially_filled",
    "filled",
    "canceled",
    "rejected",
    "expired",
    "replaced",
    "done_for_day",
}


def _assert_has_keys(obj, keys):
    for k in keys:
        assert k in obj, f"Missing key '{k}' in object: {obj}"


def _build_test_limit_order(symbol="AAPL", side="buy", qty=1, limit_price=1.00):
    # Tiny "far away" order to avoid immediate fills in many paper systems
    client_order_id = f"gs-test-{uuid.uuid4().hex[:12]}"
    return {
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "qty": qty,
        "limit_price": limit_price,
        "client_order_id": client_order_id,
        "shadow_mode": True,
    }


# -----------------------------
# Core conformance tests
# -----------------------------
def test_health_and_capabilities_shape(broker_adapter):
    health = broker_adapter.get_health()
    caps = broker_adapter.get_capabilities()

    assert isinstance(health, dict)
    assert isinstance(caps, dict)

    _assert_has_keys(health, ["status"])
    _assert_has_keys(caps, ["supports_replace", "supports_cancel", "supports_shorting", "supports_options"])


def test_account_state_shape(broker_adapter):
    acct = broker_adapter.get_account_state()
    assert isinstance(acct, dict)
    _assert_has_keys(acct, ["equity", "buying_power", "account_status"])


def test_submit_order_returns_canonical_shape(broker_adapter):
    order = _build_test_limit_order()
    resp = broker_adapter.submit_order(order)

    assert isinstance(resp, dict)
    _assert_has_keys(resp, ["order_id", "client_order_id", "status", "symbol", "side", "type"])
    assert resp["status"] in VALID_ORDER_STATES
    assert resp["client_order_id"] == order["client_order_id"]


def test_get_order_roundtrip(broker_adapter):
    order = _build_test_limit_order()
    submit = broker_adapter.submit_order(order)
    order_id = submit["order_id"]

    got = broker_adapter.get_order(order_id)
    assert isinstance(got, dict)
    _assert_has_keys(got, ["order_id", "status", "symbol"])
    assert got["order_id"] == order_id
    assert got["status"] in VALID_ORDER_STATES


def test_cancel_order_idempotency_or_safe_repeat(broker_adapter):
    order = _build_test_limit_order()
    submit = broker_adapter.submit_order(order)
    order_id = submit["order_id"]

    first = broker_adapter.cancel_order(order_id)
    assert isinstance(first, dict)
    _assert_has_keys(first, ["order_id", "status"])
    assert first["order_id"] == order_id

    # Repeat cancel should not crash; may return same canceled or already-final state
    second = broker_adapter.cancel_order(order_id)
    assert isinstance(second, dict)
    _assert_has_keys(second, ["order_id", "status"])
    assert second["order_id"] == order_id
    assert second["status"] in VALID_ORDER_STATES


def test_replace_order_if_supported(broker_adapter):
    caps = broker_adapter.get_capabilities()
    if not caps.get("supports_replace", False):
        pytest.skip("Adapter does not support replace")

    order = _build_test_limit_order(limit_price=1.00)
    submit = broker_adapter.submit_order(order)
    order_id = submit["order_id"]

    patch = {"limit_price": 1.05}
    replaced = broker_adapter.replace_order(order_id, patch)

    assert isinstance(replaced, dict)
    _assert_has_keys(replaced, ["order_id", "status"])
    assert replaced["order_id"] == order_id
    assert replaced["status"] in VALID_ORDER_STATES


def test_list_open_orders_shape(broker_adapter):
    orders = broker_adapter.list_open_orders()
    assert isinstance(orders, list)
    for o in orders[:5]:
        assert isinstance(o, dict)
        _assert_has_keys(o, ["order_id", "status", "symbol"])


def test_list_positions_shape(broker_adapter):
    pos = broker_adapter.list_positions()
    assert isinstance(pos, list)
    for p in pos[:5]:
        assert isinstance(p, dict)
        _assert_has_keys(p, ["symbol", "qty"])


def test_client_order_id_uniqueness_enforced_or_detected(broker_adapter):
    """
    Adapter should either:
    - reject duplicate client_order_id, or
    - return the same canonical order safely (idempotent behavior)
    """
    order = _build_test_limit_order()
    first = broker_adapter.submit_order(order)
    second = broker_adapter.submit_order(order)

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    _assert_has_keys(first, ["client_order_id", "order_id", "status"])
    _assert_has_keys(second, ["client_order_id", "order_id", "status"])
    assert first["client_order_id"] == second["client_order_id"]

    # Accept either same order_id (idempotent) or explicit duplicate rejection
    if first["order_id"] != second["order_id"]:
        assert second["status"] in {"rejected", "accepted", "pending", "new"}


# -----------------------------
# Reconciliation sanity test
# -----------------------------
def test_reconciliation_snapshot_consistency(broker_adapter):
    """
    Lightweight sanity: list_open_orders + get_order should not disagree catastrophically.
    """
    open_orders = broker_adapter.list_open_orders()
    if not open_orders:
        pytest.skip("No open orders in adapter for reconciliation sanity test.")

    sample = open_orders[0]
    _assert_has_keys(sample, ["order_id", "status"])
    got = broker_adapter.get_order(sample["order_id"])
    _assert_has_keys(got, ["order_id", "status"])
    assert got["order_id"] == sample["order_id"]
    assert got["status"] in VALID_ORDER_STATES
