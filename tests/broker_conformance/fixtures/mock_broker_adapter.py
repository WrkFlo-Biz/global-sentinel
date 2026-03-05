"""
Global Sentinel V4.9 - Mock Broker Adapter (for CI conformance + fault injection tests)

In-memory adapter implementing the canonical broker adapter contract.
Use this in CI to run conformance tests without sandbox credentials.

Fault injection modes (deterministic CI/replay behavior):
- reject_symbols: reject submit for listed symbols
- timeout_on_submit_symbols: raise TimeoutError on submit
- partial_fill_only_symbols: cap fills to never complete
- stale_open_symbols: prevent fills (order stays open)
- cancel_fail_symbols: reject cancel requests
- replace_fail_symbols: reject replace requests
- delayed_ack_symbols: initial status = 'new' (ack delayed until N get_order calls)
- out_of_order_transition_symbols: simulate_fill keeps status 'new' despite partial fill

Configure via set_fault_profile({...}) or env vars (MOCK_BROKER_*).
"""

from __future__ import annotations

import json as _json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_ORDER_STATES = {
    "new", "accepted", "pending", "partially_filled", "filled", "canceled",
    "rejected", "expired", "replaced", "done_for_day"
}


class MockBrokerAdapter:
    def __init__(self):
        self.orders = {}           # order_id -> canonical order dict
        self.order_by_client_id = {}  # client_order_id -> order_id
        self.trades = {}           # order_id -> list[trade]
        self.positions = {}        # symbol -> position dict
        self.account = {
            "account_status": "ACTIVE",
            "equity": 100000.0,
            "buying_power": 100000.0,
            "cash": 100000.0,
            "day_trading_buying_power": 400000.0,
            "margin_enabled": True,
        }

        # Fault injection
        self.fault_profile = self._load_fault_profile_from_env()
        self._submit_attempts = {}   # client_order_id -> int
        self._order_to_symbol = {}   # order_id -> symbol
        self._get_order_calls = {}   # order_id -> count

    # -------------------------
    # Fault injection API
    # -------------------------
    def set_fault_profile(self, profile: dict):
        """
        Runtime override for smoke tests.
        Example:
          {
            "reject_symbols": ["XOM"],
            "timeout_on_submit_symbols": ["CAT"],
            "partial_fill_only_symbols": ["NVDA"],
            "stale_open_symbols": ["TSLA"],
            "cancel_fail_symbols": [],
            "replace_fail_symbols": [],
            "submit_timeout_once_per_symbol": true
          }
        """
        self.fault_profile = self._normalize_fault_profile(profile or {})

    def _load_fault_profile_from_env(self) -> dict:
        raw_json = os.getenv("MOCK_BROKER_FAULT_PROFILE_JSON")
        if raw_json:
            try:
                return self._normalize_fault_profile(_json.loads(raw_json))
            except Exception:
                pass

        def csv_env(name):
            raw = os.getenv(name, "").strip()
            if not raw:
                return []
            return [x.strip().upper() for x in raw.split(",") if x.strip()]

        profile = {
            "reject_symbols": csv_env("MOCK_BROKER_REJECT_SYMBOLS"),
            "timeout_on_submit_symbols": csv_env("MOCK_BROKER_TIMEOUT_ON_SUBMIT_SYMBOLS"),
            "partial_fill_only_symbols": csv_env("MOCK_BROKER_PARTIAL_FILL_ONLY_SYMBOLS"),
            "stale_open_symbols": csv_env("MOCK_BROKER_STALE_OPEN_SYMBOLS"),
            "cancel_fail_symbols": csv_env("MOCK_BROKER_CANCEL_FAIL_SYMBOLS"),
            "replace_fail_symbols": csv_env("MOCK_BROKER_REPLACE_FAIL_SYMBOLS"),
            "delayed_ack_symbols": csv_env("MOCK_BROKER_DELAYED_ACK_SYMBOLS"),
            "out_of_order_transition_symbols": csv_env("MOCK_BROKER_OUT_OF_ORDER_TRANSITION_SYMBOLS"),
            "submit_timeout_once_per_symbol": str(os.getenv("MOCK_BROKER_SUBMIT_TIMEOUT_ONCE_PER_SYMBOL", "true")).lower() in {"1", "true", "yes", "y"},
            "ack_after_n_get_order_calls": int(os.getenv("MOCK_BROKER_ACK_AFTER_N_GET_ORDER_CALLS", "2")),
        }
        return self._normalize_fault_profile(profile)

    def _normalize_fault_profile(self, profile: dict) -> dict:
        def norm_list(v):
            if not v:
                return []
            return [str(x).upper() for x in v]
        return {
            "reject_symbols": norm_list(profile.get("reject_symbols")),
            "timeout_on_submit_symbols": norm_list(profile.get("timeout_on_submit_symbols")),
            "partial_fill_only_symbols": norm_list(profile.get("partial_fill_only_symbols")),
            "stale_open_symbols": norm_list(profile.get("stale_open_symbols")),
            "cancel_fail_symbols": norm_list(profile.get("cancel_fail_symbols")),
            "replace_fail_symbols": norm_list(profile.get("replace_fail_symbols")),
            "delayed_ack_symbols": norm_list(profile.get("delayed_ack_symbols")),
            "out_of_order_transition_symbols": norm_list(profile.get("out_of_order_transition_symbols")),
            "submit_timeout_once_per_symbol": bool(profile.get("submit_timeout_once_per_symbol", True)),
            "ack_after_n_get_order_calls": int(profile.get("ack_after_n_get_order_calls", 2) or 2),
        }

    def _sym(self, symbol) -> str:
        return str(symbol or "").upper()

    def _fault_enabled(self, key: str, symbol: str) -> bool:
        return self._sym(symbol) in set(self.fault_profile.get(key, []))

    def _simulate_submit_timeout_if_needed(self, order: dict):
        symbol = self._sym(order.get("symbol"))
        if not self._fault_enabled("timeout_on_submit_symbols", symbol):
            return
        once = bool(self.fault_profile.get("submit_timeout_once_per_symbol", True))
        cid = str(order.get("client_order_id") or f"sym:{symbol}")
        self._submit_attempts[cid] = self._submit_attempts.get(cid, 0) + 1
        if once and self._submit_attempts[cid] > 1:
            return
        raise TimeoutError(f"Mock submit timeout injected for symbol={symbol}")

    def _build_rejected_order_record(self, order: dict, reason_code: str, reason_message: str) -> dict:
        order_id = f"mock-{uuid.uuid4().hex[:12]}"
        qty = float(order.get("qty", 0) or 0)
        rec = {
            "order_id": order_id,
            "client_order_id": order.get("client_order_id"),
            "status": "rejected",
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("type"),
            "qty": qty,
            "filled_qty": 0.0,
            "remaining_qty": qty,
            "limit_price": float(order["limit_price"]) if order.get("limit_price") is not None else None,
            "avg_fill_price": None,
            "submitted_at_utc": iso_now(),
            "updated_at_utc": iso_now(),
            "acknowledged_at_utc": iso_now(),
            "replaced_by_order_id": None,
            "replaces_order_id": None,
            "broker_raw_status": "rejected",
            "broker_order_payload_ref": None,
            "reject_reason_code": reason_code,
            "reject_reason_message": reason_message,
        }
        self.orders[order_id] = rec
        cid = order.get("client_order_id")
        if cid:
            self.order_by_client_id[cid] = order_id
        self.trades[order_id] = []
        self._order_to_symbol[order_id] = self._sym(order.get("symbol"))
        return dict(rec)

    # -------------------------
    # Contract methods
    # -------------------------
    def get_capabilities(self) -> dict:
        return {
            "supports_replace": True,
            "supports_cancel": True,
            "supports_shorting": True,
            "supports_options": True,
            "supports_fractional": False,
            "supports_extended_hours": False,
        }

    def get_health(self) -> dict:
        return {
            "status": "ok",
            "latency_ms": 1,
            "broker_time_utc": iso_now(),
            "details": {"adapter": "mock"},
        }

    def get_account_state(self) -> dict:
        return dict(self.account)

    def submit_order(self, order: dict) -> dict:
        # Shadow guard
        if not order.get("shadow_mode", False):
            return self._error_order_like(
                client_order_id=order.get("client_order_id"),
                symbol=order.get("symbol"),
                side=order.get("side"),
                type_=order.get("type"),
                status="rejected",
                reject_reason_code="permission_denied",
                reject_reason_message="Mock adapter requires shadow_mode=true",
            )

        # Duplicate client_order_id behavior: idempotent return same order
        cid = order.get("client_order_id")
        if cid and cid in self.order_by_client_id:
            existing_id = self.order_by_client_id[cid]
            return dict(self.orders[existing_id])

        # Fault injection: timeout
        self._simulate_submit_timeout_if_needed(order)

        # Fault injection: reject
        symbol = self._sym(order.get("symbol"))
        if self._fault_enabled("reject_symbols", symbol):
            return self._build_rejected_order_record(
                order,
                reason_code="mock_injected_reject",
                reason_message=f"Injected reject for symbol={symbol}",
            )

        order_id = f"mock-{uuid.uuid4().hex[:12]}"
        qty = float(order.get("qty", 0) or 0)

        # Delayed ack: initial status is "new" instead of "accepted"
        initial_status = "new" if self._fault_enabled("delayed_ack_symbols", symbol) else "accepted"

        rec = {
            "order_id": order_id,
            "client_order_id": cid,
            "status": initial_status,
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("type"),
            "qty": qty,
            "filled_qty": 0.0,
            "remaining_qty": qty,
            "limit_price": float(order["limit_price"]) if order.get("limit_price") is not None else None,
            "avg_fill_price": None,
            "submitted_at_utc": iso_now(),
            "updated_at_utc": iso_now(),
            "acknowledged_at_utc": iso_now() if initial_status == "accepted" else None,
            "replaced_by_order_id": None,
            "replaces_order_id": None,
            "broker_raw_status": initial_status,
            "broker_order_payload_ref": None,
            "reject_reason_code": None,
            "reject_reason_message": None,
        }
        self.orders[order_id] = rec
        self._get_order_calls[order_id] = 0
        if cid:
            self.order_by_client_id[cid] = order_id
        self.trades[order_id] = []
        self._order_to_symbol[order_id] = self._sym(order.get("symbol"))
        return dict(rec)

    def get_order(self, order_id: str) -> dict:
        if order_id not in self.orders:
            raise KeyError(f"Order not found: {order_id}")

        rec = self.orders[order_id]
        symbol = self._order_to_symbol.get(order_id, self._sym(rec.get("symbol")))
        self._get_order_calls[order_id] = self._get_order_calls.get(order_id, 0) + 1

        # Delayed ack: transition from "new" to "accepted" after N get_order calls
        if self._fault_enabled("delayed_ack_symbols", symbol):
            if rec["status"] == "new":
                threshold = int(self.fault_profile.get("ack_after_n_get_order_calls", 2) or 2)
                if self._get_order_calls[order_id] >= threshold:
                    rec["status"] = "accepted"
                    rec["broker_raw_status"] = "accepted"
                    rec["acknowledged_at_utc"] = iso_now()
                    rec["updated_at_utc"] = iso_now()
                    self.orders[order_id] = rec

        return dict(rec)

    def cancel_order(self, order_id: str) -> dict:
        if order_id not in self.orders:
            raise KeyError(f"Order not found: {order_id}")

        rec = self.orders[order_id]
        if rec["status"] in {"filled", "canceled", "rejected", "expired"}:
            return dict(rec)

        # Fault injection: cancel fail
        symbol = self._order_to_symbol.get(order_id, self._sym(rec.get("symbol")))
        if self._fault_enabled("cancel_fail_symbols", symbol):
            return self._error_order_like(
                client_order_id=rec.get("client_order_id"),
                symbol=rec.get("symbol"),
                side=rec.get("side"),
                type_=rec.get("type"),
                status="rejected",
                reject_reason_code="mock_cancel_fail",
                reject_reason_message=f"Injected cancel failure for symbol={symbol}",
            )

        rec["status"] = "canceled"
        rec["broker_raw_status"] = "canceled"
        rec["updated_at_utc"] = iso_now()
        self.orders[order_id] = rec
        return dict(rec)

    def replace_order(self, order_id: str, patch: dict) -> dict:
        if order_id not in self.orders:
            raise KeyError(f"Order not found: {order_id}")

        rec = self.orders[order_id]
        if rec["status"] in {"filled", "canceled", "rejected", "expired"}:
            return dict(rec)

        # Fault injection: replace fail
        symbol = self._order_to_symbol.get(order_id, self._sym(rec.get("symbol")))
        if self._fault_enabled("replace_fail_symbols", symbol):
            return self._error_order_like(
                client_order_id=rec.get("client_order_id"),
                symbol=rec.get("symbol"),
                side=rec.get("side"),
                type_=rec.get("type"),
                status="rejected",
                reject_reason_code="mock_replace_fail",
                reject_reason_message=f"Injected replace failure for symbol={symbol}",
            )

        if "limit_price" in patch:
            rec["limit_price"] = float(patch["limit_price"])
        if "qty" in patch:
            new_qty = float(patch["qty"])
            rec["qty"] = new_qty
            rec["remaining_qty"] = max(new_qty - float(rec.get("filled_qty") or 0), 0.0)

        rec["status"] = "replaced"
        rec["broker_raw_status"] = "replaced"
        rec["updated_at_utc"] = iso_now()
        self.orders[order_id] = rec
        return dict(rec)

    def list_open_orders(self) -> list:
        out = []
        for rec in self.orders.values():
            if rec["status"] in {"new", "accepted", "pending", "partially_filled", "replaced"}:
                out.append(dict(rec))
        return out

    def list_positions(self) -> list:
        return [dict(v) for v in self.positions.values()]

    def get_trades(self, order_id: str) -> list:
        return [dict(t) for t in self.trades.get(order_id, [])]

    # -------------------------
    # Test helpers
    # -------------------------
    def simulate_fill(self, order_id: str, fill_qty: float, fill_price: float):
        if order_id not in self.orders:
            raise KeyError(f"Order not found: {order_id}")

        rec = self.orders[order_id]
        symbol = self._order_to_symbol.get(order_id, self._sym(rec.get("symbol")))

        # Fault injection: stale open (never fill)
        if self._fault_enabled("stale_open_symbols", symbol):
            rec["status"] = "accepted" if rec["filled_qty"] == 0 else "partially_filled"
            rec["broker_raw_status"] = rec["status"]
            rec["updated_at_utc"] = iso_now()
            self.orders[order_id] = rec
            return dict(rec)

        qty = float(rec.get("qty") or 0)
        filled_before = float(rec.get("filled_qty") or 0)
        remaining = max(qty - filled_before, 0)

        # Fault injection: partial fill only (cap fill to never complete)
        if self._fault_enabled("partial_fill_only_symbols", symbol):
            if remaining > 0:
                hard_cap = max(1.0, remaining / 2.0)
                fill_qty = min(float(fill_qty), hard_cap)
                if fill_qty >= remaining:
                    fill_qty = max(remaining - 1.0, 1.0) if remaining > 1 else remaining / 2.0

        fill_qty = min(fill_qty, remaining)
        if fill_qty <= 0:
            return dict(rec)

        # Out-of-order transition check
        ooo = self._fault_enabled("out_of_order_transition_symbols", symbol)

        # append trade
        trade = {
            "trade_id": f"trade-{uuid.uuid4().hex[:12]}",
            "order_id": order_id,
            "symbol": rec["symbol"],
            "side": rec["side"],
            "fill_qty": float(fill_qty),
            "fill_price": float(fill_price),
            "fill_time_utc": iso_now(),
            "exchange": "MOCK",
            "liquidity_flag": None,
            "commission": 0.0,
            "fees": 0.0,
        }
        self.trades.setdefault(order_id, []).append(trade)

        # update order
        filled_after = filled_before + fill_qty
        rec["filled_qty"] = filled_after
        rec["remaining_qty"] = max(qty - filled_after, 0.0)

        # weighted average fill
        old_avg = rec.get("avg_fill_price")
        if old_avg is None:
            rec["avg_fill_price"] = float(fill_price)
        else:
            rec["avg_fill_price"] = ((float(old_avg) * filled_before) + (float(fill_price) * fill_qty)) / max(filled_after, 1e-9)

        # Status assignment with out-of-order transition support
        if rec["remaining_qty"] <= 0:
            rec["status"] = "filled"
        else:
            if ooo and rec.get("status") in {"new", "pending"}:
                # Keep broker state oddly "new" despite partial fill to emulate racey broker snapshots
                pass  # status stays as-is
            else:
                rec["status"] = "partially_filled"

        rec["broker_raw_status"] = rec["status"]
        rec["updated_at_utc"] = iso_now()
        self.orders[order_id] = rec

        # update position (simple)
        self._apply_position_trade(trade)

        return dict(rec)

    # -------------------------
    # Internals
    # -------------------------
    def _apply_position_trade(self, trade: dict):
        sym = trade["symbol"]
        qty = float(trade["fill_qty"])
        side = str(trade["side"]).lower()
        signed_qty = qty if side == "buy" else -qty

        pos = self.positions.get(sym)
        if pos is None:
            pos = {
                "symbol": sym,
                "qty": 0.0,
                "avg_entry_price": None,
                "market_value": None,
                "unrealized_pl": None,
                "side": None,
                "as_of_utc": iso_now(),
            }

        prev_qty = float(pos["qty"] or 0.0)
        new_qty = prev_qty + signed_qty

        if prev_qty == 0:
            pos["avg_entry_price"] = float(trade["fill_price"])

        pos["qty"] = new_qty
        pos["side"] = "short" if new_qty < 0 else ("long" if new_qty > 0 else None)
        pos["as_of_utc"] = iso_now()

        if abs(new_qty) < 1e-9:
            self.positions.pop(sym, None)
        else:
            self.positions[sym] = pos

    def _error_order_like(
        self,
        client_order_id,
        symbol,
        side,
        type_,
        status="rejected",
        reject_reason_code="unknown",
        reject_reason_message="error",
    ):
        return {
            "order_id": f"mock-{uuid.uuid4().hex[:12]}",
            "client_order_id": client_order_id,
            "status": status,
            "symbol": symbol,
            "side": side,
            "type": type_,
            "qty": 0.0,
            "filled_qty": 0.0,
            "remaining_qty": 0.0,
            "limit_price": None,
            "avg_fill_price": None,
            "submitted_at_utc": iso_now(),
            "updated_at_utc": iso_now(),
            "acknowledged_at_utc": iso_now(),
            "replaced_by_order_id": None,
            "replaces_order_id": None,
            "broker_raw_status": status,
            "broker_order_payload_ref": None,
            "reject_reason_code": reject_reason_code,
            "reject_reason_message": reject_reason_message,
        }
