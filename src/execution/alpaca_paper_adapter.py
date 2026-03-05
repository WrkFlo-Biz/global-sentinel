#!/usr/bin/env python3
"""
Global Sentinel V4.6 - Alpaca Paper Adapter (Canonical Broker Contract)

Notes:
- Default endpoint is Alpaca PAPER API.
- This adapter enforces shadow_mode by default.
- It normalizes broker-specific payloads to the canonical contract in docs/broker_adapter_contract.md

Env:
- ALPACA_API_KEY
- ALPACA_SECRET_KEY
Optional:
- ALPACA_PAPER_BASE_URL (default: https://paper-api.alpaca.markets)
- ALPACA_ALLOW_LIVE=false (default false)
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_ORDER_STATES = {
    "new", "accepted", "pending", "partially_filled", "filled", "canceled",
    "rejected", "expired", "replaced", "done_for_day"
}


class BrokerAdapterError(Exception):
    def __init__(self, error_code: str, message: str, retryable: bool = False, http_status: Optional[int] = None, context: Optional[dict] = None):
        super().__init__(message)
        self.payload = {
            "error": True,
            "error_type": self.__class__.__name__,
            "error_code": error_code,
            "error_message": message,
            "retryable": retryable,
            "http_status": http_status,
            "context": context or {},
        }


class AlpacaPaperAdapter:
    def __init__(self):
        self.base_url = os.getenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
        self.api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        self.api_secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        self.allow_live = str(os.getenv("ALPACA_ALLOW_LIVE", "false")).lower() in {"1", "true", "yes", "y"}

        if not self.api_key or not self.api_secret:
            raise BrokerAdapterError("missing_credentials", "ALPACA_API_KEY / ALPACA_SECRET_KEY required")

        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GlobalSentinel-AlpacaPaperAdapter/0.1",
        })

    # -------------------------
    # Contract methods
    # -------------------------
    def get_capabilities(self) -> dict:
        return {
            "supports_replace": True,
            "supports_cancel": True,
            "supports_shorting": True,    # account-dependent, but adapter supports API path
            "supports_options": False,    # Alpaca options availability depends on account/product; keep conservative
            "supports_fractional": True,
            "supports_extended_hours": True,
        }

    def get_health(self) -> dict:
        try:
            clock = self._request("GET", "/v2/clock")
            return {
                "status": "ok",
                "latency_ms": None,
                "broker_time_utc": self._safe_get(clock, "timestamp"),
                "details": {"clock": clock},
            }
        except BrokerAdapterError as e:
            return {
                "status": "degraded",
                "latency_ms": None,
                "broker_time_utc": None,
                "details": e.payload,
            }

    def get_account_state(self) -> dict:
        acct = self._request("GET", "/v2/account")
        return {
            "account_status": str(acct.get("status", "unknown")),
            "equity": self._to_num(acct.get("equity")),
            "buying_power": self._to_num(acct.get("buying_power")),
            "cash": self._to_num(acct.get("cash")),
            "day_trading_buying_power": self._to_num(acct.get("daytrading_buying_power")),
            "margin_enabled": bool(acct.get("multiplier") not in (None, "1", 1)),
        }

    def submit_order(self, order: dict) -> dict:
        self._enforce_shadow_mode(order)

        payload = self._map_canonical_order_to_alpaca(order)
        resp = self._request("POST", "/v2/orders", json_body=payload, op="submit_order", context={"symbol": order.get("symbol")})
        return self._normalize_order(resp)

    def get_order(self, order_id: str) -> dict:
        resp = self._request("GET", f"/v2/orders/{order_id}", op="get_order", context={"order_id": order_id})
        return self._normalize_order(resp)

    def cancel_order(self, order_id: str) -> dict:
        # Alpaca may return 204 No Content
        self._request("DELETE", f"/v2/orders/{order_id}", allow_empty=True, op="cancel_order", context={"order_id": order_id})
        try:
            return self.get_order(order_id)
        except Exception:
            return {
                "order_id": order_id,
                "client_order_id": None,
                "status": "canceled",
                "symbol": None,
                "side": None,
                "type": None,
                "qty": 0,
                "filled_qty": 0,
                "remaining_qty": 0,
                "limit_price": None,
                "avg_fill_price": None,
                "submitted_at_utc": None,
                "updated_at_utc": iso_now(),
                "acknowledged_at_utc": None,
                "broker_raw_status": "canceled_no_fetch",
                "reject_reason_code": None,
                "reject_reason_message": None,
            }

    def replace_order(self, order_id: str, patch: dict) -> dict:
        alpaca_patch = {}
        if "limit_price" in patch:
            alpaca_patch["limit_price"] = patch["limit_price"]
        if "stop_price" in patch:
            alpaca_patch["stop_price"] = patch["stop_price"]
        if "qty" in patch:
            alpaca_patch["qty"] = patch["qty"]
        if "time_in_force" in patch:
            alpaca_patch["time_in_force"] = patch["time_in_force"]

        resp = self._request("PATCH", f"/v2/orders/{order_id}", json_body=alpaca_patch, op="replace_order", context={"order_id": order_id})
        return self._normalize_order(resp)

    def list_open_orders(self) -> List[dict]:
        resp = self._request("GET", "/v2/orders", params={"status": "open", "direction": "desc", "nested": "false", "limit": 200}, op="list_open_orders")
        if not isinstance(resp, list):
            return []
        return [self._normalize_order(x) for x in resp if isinstance(x, dict)]

    def list_positions(self) -> List[dict]:
        resp = self._request("GET", "/v2/positions", op="list_positions")
        if not isinstance(resp, list):
            return []
        return [self._normalize_position(x) for x in resp if isinstance(x, dict)]

    def get_trades(self, order_id: str) -> List[dict]:
        # Optional / best-effort. Alpaca activities endpoints vary by account/features.
        # We try FILL activities and filter by order_id if available.
        try:
            resp = self._request("GET", "/v2/account/activities/FILL", params={"direction": "desc", "page_size": 100}, op="get_trades", context={"order_id": order_id})
        except BrokerAdapterError:
            return []

        if not isinstance(resp, list):
            return []

        out = []
        for row in resp:
            if not isinstance(row, dict):
                continue
            # Alpaca fill activity shape can vary; use best-effort mapping
            if order_id and row.get("order_id") and str(row.get("order_id")) != str(order_id):
                continue
            out.append(self._normalize_fill_activity(row, order_id_hint=order_id))
        return out

    # -------------------------
    # Request layer
    # -------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        allow_empty: bool = False,
        op: str = "request",
        context: Optional[dict] = None,
    ):
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(method=method, url=url, params=params, json=json_body, timeout=20)
        except requests.Timeout as e:
            raise BrokerAdapterError("timeout", f"Timeout calling {path}", retryable=True, context={"operation": op, **(context or {})})
        except requests.RequestException as e:
            raise BrokerAdapterError("transient_network", str(e), retryable=True, context={"operation": op, **(context or {})})

        if resp.status_code >= 400:
            payload = self._try_json(resp)
            raise BrokerAdapterError(
                error_code=self._classify_http_error(resp.status_code, payload),
                message=self._extract_error_message(payload, resp),
                retryable=resp.status_code in (408, 429, 500, 502, 503, 504),
                http_status=resp.status_code,
                context={"operation": op, "path": path, **(context or {})},
            )

        if resp.status_code == 204 or not resp.content:
            if allow_empty:
                return {}
            return {}

        return self._try_json(resp)

    def _try_json(self, resp):
        try:
            return resp.json()
        except Exception:
            return {"raw_text": resp.text}

    # -------------------------
    # Normalization
    # -------------------------
    def _normalize_order(self, o: dict) -> dict:
        qty = self._to_num(o.get("qty"))
        filled_qty = self._to_num(o.get("filled_qty"))
        remaining_qty = max((qty or 0) - (filled_qty or 0), 0)

        status = self._map_status(o.get("status"))

        return {
            "order_id": str(o.get("id") or o.get("order_id") or ""),
            "client_order_id": o.get("client_order_id"),
            "status": status,
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "type": o.get("type"),
            "qty": qty,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "limit_price": self._to_num(o.get("limit_price")),
            "avg_fill_price": self._to_num(o.get("filled_avg_price")),
            "submitted_at_utc": o.get("submitted_at"),
            "updated_at_utc": o.get("updated_at"),
            "acknowledged_at_utc": o.get("accepted_at") or o.get("submitted_at"),
            "replaced_by_order_id": o.get("replaced_by"),
            "replaces_order_id": o.get("replaces"),
            "broker_raw_status": o.get("status"),
            "broker_order_payload_ref": None,
            "reject_reason_code": None if status != "rejected" else "broker_rejected",
            "reject_reason_message": None,
        }

    def _normalize_position(self, p: dict) -> dict:
        qty = self._to_num(p.get("qty"))
        side = "short" if qty is not None and qty < 0 else "long"
        return {
            "symbol": p.get("symbol"),
            "qty": qty,
            "avg_entry_price": self._to_num(p.get("avg_entry_price")),
            "market_value": self._to_num(p.get("market_value")),
            "unrealized_pl": self._to_num(p.get("unrealized_pl")),
            "side": side,
            "as_of_utc": iso_now(),
        }

    def _normalize_fill_activity(self, row: dict, order_id_hint: Optional[str] = None) -> dict:
        return {
            "trade_id": str(row.get("id") or row.get("activity_id") or ""),
            "order_id": str(row.get("order_id") or order_id_hint or ""),
            "symbol": row.get("symbol"),
            "side": row.get("side"),
            "fill_qty": self._to_num(row.get("qty")),
            "fill_price": self._to_num(row.get("price")),
            "fill_time_utc": row.get("transaction_time") or row.get("date"),
            "exchange": row.get("exchange"),
            "liquidity_flag": row.get("liquidity"),
            "commission": self._to_num(row.get("net_amount")) if row.get("net_amount") is not None else None,
            "fees": None,
        }

    def _map_status(self, s: Any) -> str:
        x = str(s or "").lower()
        mapping = {
            "new": "new",
            "accepted": "accepted",
            "pending_new": "pending",
            "partially_filled": "partially_filled",
            "filled": "filled",
            "done_for_day": "done_for_day",
            "canceled": "canceled",
            "expired": "expired",
            "replaced": "replaced",
            "rejected": "rejected",
            "accepted_for_bidding": "accepted",
            "pending_cancel": "pending",
            "pending_replace": "pending",
            "stopped": "pending",
            "suspended": "pending",
            "calculated": "pending",
        }
        out = mapping.get(x, "pending" if x else "pending")
        return out if out in VALID_ORDER_STATES else "pending"

    # -------------------------
    # Mapping helpers
    # -------------------------
    def _map_canonical_order_to_alpaca(self, order: dict) -> dict:
        payload = {
            "symbol": order["symbol"],
            "side": order["side"],
            "type": order["type"],
            "time_in_force": order.get("time_in_force", "day"),
            "client_order_id": order["client_order_id"],
        }

        # qty vs notional (keep qty-first)
        if order.get("qty") is not None:
            payload["qty"] = str(order["qty"])
        elif order.get("notional") is not None:
            payload["notional"] = str(order["notional"])
        else:
            raise BrokerAdapterError("invalid_order", "Order requires qty or notional", context={"operation": "submit_order"})

        if order.get("limit_price") is not None:
            payload["limit_price"] = str(order["limit_price"])
        if order.get("stop_price") is not None:
            payload["stop_price"] = str(order["stop_price"])

        if "extended_hours" in order:
            payload["extended_hours"] = bool(order["extended_hours"])

        return payload

    def _enforce_shadow_mode(self, order: dict):
        shadow_mode = bool(order.get("shadow_mode", False))
        if not shadow_mode and not self.allow_live:
            raise BrokerAdapterError(
                "permission_denied",
                "Live routing disabled. Adapter requires shadow_mode=true or ALPACA_ALLOW_LIVE=true.",
                context={"operation": "submit_order", "symbol": order.get("symbol")}
            )

    def _classify_http_error(self, status: int, payload: Any) -> str:
        if status == 401:
            return "permission_denied"
        if status == 403:
            return "permission_denied"
        if status == 404:
            return "unknown"
        if status == 422:
            return "invalid_order"
        if status == 429:
            return "rate_limit"
        if status >= 500:
            return "transient_network"
        return "unknown"

    def _extract_error_message(self, payload: Any, resp) -> str:
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("error") or payload)
        return f"HTTP {resp.status_code}"

    def _safe_get(self, d: Any, k: str):
        return d.get(k) if isinstance(d, dict) else None

    def _to_num(self, v: Any):
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None
