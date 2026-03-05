#!/usr/bin/env python3
"""
Global Sentinel V4.6 - Tradier Sandbox Adapter (Canonical Broker Contract)

Notes:
- Uses Tradier sandbox endpoint by default.
- Requires TRADIER_TOKEN and TRADIER_ACCOUNT_ID.
- Shadow-mode enforced by default (adapter-level guard).

Env:
- TRADIER_TOKEN
- TRADIER_ACCOUNT_ID
Optional:
- TRADIER_SANDBOX_BASE_URL (default: https://sandbox.tradier.com/v1)
- TRADIER_ALLOW_LIVE=false (default false)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, List, Optional

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


class TradierSandboxAdapter:
    def __init__(self):
        self.base_url = os.getenv("TRADIER_SANDBOX_BASE_URL", "https://sandbox.tradier.com/v1").rstrip("/")
        self.token = os.getenv("TRADIER_TOKEN")
        self.account_id = os.getenv("TRADIER_ACCOUNT_ID")
        self.allow_live = str(os.getenv("TRADIER_ALLOW_LIVE", "false")).lower() in {"1", "true", "yes", "y"}

        if not self.token or not self.account_id:
            raise BrokerAdapterError("missing_credentials", "TRADIER_TOKEN and TRADIER_ACCOUNT_ID required")

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "GlobalSentinel-TradierSandboxAdapter/0.1",
        })

    # -------------------------
    # Contract methods
    # -------------------------
    def get_capabilities(self) -> dict:
        return {
            "supports_replace": True,
            "supports_cancel": True,
            "supports_shorting": True,   # account/asset dependent; adapter supports path
            "supports_options": True,
            "supports_fractional": False,
            "supports_extended_hours": False,
        }

    def get_health(self) -> dict:
        try:
            clock = self._request("GET", "/markets/clock", op="get_health")
            return {
                "status": "ok",
                "latency_ms": None,
                "broker_time_utc": None,
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
        balances = self._request("GET", f"/accounts/{self.account_id}/balances", op="get_account_state")
        b = (((balances or {}).get("balances") or {}).get("total") or {}) if isinstance(balances, dict) else {}
        # Tradier keys vary; fall back through common names
        return {
            "account_status": str((((balances or {}).get("balances") or {}).get("account_status") or "unknown")),
            "equity": self._to_num(b.get("equity") or b.get("total_equity")),
            "buying_power": self._to_num(b.get("margin") or b.get("option_buying_power") or b.get("stock_buying_power") or b.get("cash")),
            "cash": self._to_num(b.get("cash") or b.get("total_cash")),
            "day_trading_buying_power": self._to_num(b.get("day_trading_buying_power")),
            "margin_enabled": None,
        }

    def submit_order(self, order: dict) -> dict:
        self._enforce_shadow_mode(order)
        form = self._map_canonical_order_to_tradier(order)
        resp = self._request("POST", f"/accounts/{self.account_id}/orders", data=form, op="submit_order", context={"symbol": order.get("symbol")})
        order_node = self._extract_order_node(resp)
        return self._normalize_order(order_node, client_order_id_hint=order.get("client_order_id"))

    def get_order(self, order_id: str) -> dict:
        resp = self._request("GET", f"/accounts/{self.account_id}/orders/{order_id}", op="get_order", context={"order_id": order_id})
        order_node = self._extract_order_node(resp)
        return self._normalize_order(order_node)

    def cancel_order(self, order_id: str) -> dict:
        # Tradier cancel may be DELETE /accounts/{account_id}/orders/{id}
        self._request("DELETE", f"/accounts/{self.account_id}/orders/{order_id}", op="cancel_order", context={"order_id": order_id})
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
                "broker_order_payload_ref": None,
                "reject_reason_code": None,
                "reject_reason_message": None,
            }

    def replace_order(self, order_id: str, patch: dict) -> dict:
        # Tradier replace often uses PUT /accounts/{id}/orders/{id} with order params
        form = {}
        if "qty" in patch:
            form["quantity"] = patch["qty"]
        if "limit_price" in patch:
            form["price"] = patch["limit_price"]
        if "stop_price" in patch:
            form["stop"] = patch["stop_price"]
        if "time_in_force" in patch:
            form["duration"] = patch["time_in_force"]

        resp = self._request("PUT", f"/accounts/{self.account_id}/orders/{order_id}", data=form, op="replace_order", context={"order_id": order_id})
        order_node = self._extract_order_node(resp)
        return self._normalize_order(order_node)

    def list_open_orders(self) -> List[dict]:
        resp = self._request("GET", f"/accounts/{self.account_id}/orders", params={"includeTags": "true"}, op="list_open_orders")
        nodes = self._extract_order_list(resp)
        out = []
        for n in nodes:
            try:
                canon = self._normalize_order(n)
                if canon["status"] in {"new", "accepted", "pending", "partially_filled"}:
                    out.append(canon)
            except Exception:
                continue
        return out

    def list_positions(self) -> List[dict]:
        resp = self._request("GET", f"/accounts/{self.account_id}/positions", op="list_positions")
        # Tradier wraps positions -> position (dict or list)
        positions_node = ((resp or {}).get("positions") or {}).get("position")
        if positions_node is None:
            return []
        if isinstance(positions_node, dict):
            positions_node = [positions_node]
        out = []
        for p in positions_node:
            if not isinstance(p, dict):
                continue
            qty = self._to_num(p.get("quantity"))
            out.append({
                "symbol": p.get("symbol"),
                "qty": qty,
                "avg_entry_price": self._to_num(p.get("cost_basis")),
                "market_value": self._to_num(p.get("market_value")),
                "unrealized_pl": self._to_num(p.get("unrealized_gain_loss")),
                "side": "short" if (qty is not None and qty < 0) else "long",
                "as_of_utc": iso_now(),
            })
        return out

    def get_trades(self, order_id: str) -> List[dict]:
        """
        Best-effort using history endpoint. Tradier response shapes vary.
        If unavailable, return [].
        """
        try:
            resp = self._request("GET", f"/accounts/{self.account_id}/history", params={"limit": 200}, op="get_trades", context={"order_id": order_id})
        except BrokerAdapterError:
            return []

        # Tradier may return history -> event(s)
        events = ((resp or {}).get("history") or {}).get("event")
        if events is None:
            return []
        if isinstance(events, dict):
            events = [events]

        out = []
        for e in events:
            if not isinstance(e, dict):
                continue
            if str(e.get("type", "")).lower() not in {"trade", "fill"}:
                continue
            if order_id and e.get("id") and str(e.get("id")) != str(order_id):
                # Tradier may not expose order id here; keep loose matching
                pass
            out.append({
                "trade_id": str(e.get("id") or ""),
                "order_id": str(e.get("id") or order_id or ""),
                "symbol": e.get("symbol"),
                "side": e.get("side"),
                "fill_qty": self._to_num(e.get("quantity")),
                "fill_price": self._to_num(e.get("price")),
                "fill_time_utc": e.get("date"),
                "exchange": None,
                "liquidity_flag": None,
                "commission": self._to_num(e.get("commission")),
                "fees": self._to_num(e.get("fees")),
            })
        return out

    # -------------------------
    # Request layer
    # -------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        op: str = "request",
        context: Optional[dict] = None,
    ):
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(method=method, url=url, params=params, data=data, timeout=20)
        except requests.Timeout:
            raise BrokerAdapterError("timeout", f"Timeout calling {path}", retryable=True, context={"operation": op, **(context or {})})
        except requests.RequestException as e:
            raise BrokerAdapterError("transient_network", str(e), retryable=True, context={"operation": op, **(context or {})})

        if resp.status_code >= 400:
            payload = self._try_json(resp)
            raise BrokerAdapterError(
                self._classify_http_error(resp.status_code, payload),
                self._extract_error_message(payload, resp),
                retryable=resp.status_code in {408, 429, 500, 502, 503, 504},
                http_status=resp.status_code,
                context={"operation": op, "path": path, **(context or {})},
            )

        if not resp.content:
            return {}
        return self._try_json(resp)

    def _try_json(self, resp):
        try:
            return resp.json()
        except Exception:
            return {"raw_text": resp.text}

    # -------------------------
    # Response extraction / normalization
    # -------------------------
    def _extract_order_node(self, resp: dict) -> dict:
        # Tradier commonly wraps in {"order": {...}}
        if not isinstance(resp, dict):
            return {}
        if "order" in resp and isinstance(resp["order"], dict):
            return resp["order"]
        if "orders" in resp and isinstance(resp["orders"], dict):
            o = resp["orders"].get("order")
            if isinstance(o, list) and o:
                return o[0]
            if isinstance(o, dict):
                return o
        return resp

    def _extract_order_list(self, resp: dict) -> List[dict]:
        if not isinstance(resp, dict):
            return []
        orders = ((resp.get("orders") or {}).get("order"))
        if orders is None:
            return []
        if isinstance(orders, dict):
            return [orders]
        if isinstance(orders, list):
            return [o for o in orders if isinstance(o, dict)]
        return []

    def _normalize_order(self, o: dict, client_order_id_hint: Optional[str] = None) -> dict:
        qty = self._to_num(o.get("quantity") or o.get("qty"))
        filled_qty = self._to_num(o.get("exec_quantity") or o.get("filled_quantity") or 0)
        remaining_qty = max((qty or 0) - (filled_qty or 0), 0)

        raw_status = o.get("status")
        status = self._map_status(raw_status)

        # Tradier may not echo client_order_id
        client_order_id = o.get("client_order_id") or o.get("tag") or client_order_id_hint

        return {
            "order_id": str(o.get("id") or o.get("order_id") or ""),
            "client_order_id": client_order_id,
            "status": status,
            "symbol": o.get("symbol"),
            "side": (o.get("side") or "").lower() if o.get("side") else None,
            "type": (o.get("type") or "").lower() if o.get("type") else None,
            "qty": qty,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "limit_price": self._to_num(o.get("price") or o.get("limit_price")),
            "avg_fill_price": self._to_num(o.get("avg_fill_price") or o.get("avg_price")),
            "submitted_at_utc": o.get("create_date") or o.get("transaction_date"),
            "updated_at_utc": o.get("transaction_date") or o.get("last_update"),
            "acknowledged_at_utc": o.get("create_date") or o.get("transaction_date"),
            "replaced_by_order_id": o.get("replaced_by_order_id"),
            "replaces_order_id": o.get("replaces_order_id"),
            "broker_raw_status": raw_status,
            "broker_order_payload_ref": None,
            "reject_reason_code": None if status != "rejected" else "broker_rejected",
            "reject_reason_message": o.get("reason_description") or o.get("message"),
        }

    def _map_status(self, s: Any) -> str:
        x = str(s or "").lower()
        mapping = {
            "open": "accepted",
            "pending": "pending",
            "partially_filled": "partially_filled",
            "filled": "filled",
            "canceled": "canceled",
            "cancelled": "canceled",
            "expired": "expired",
            "rejected": "rejected",
            "error": "rejected",
            "ok": "accepted",
            "submitted": "new",
        }
        out = mapping.get(x, "pending" if x else "pending")
        return out if out in VALID_ORDER_STATES else "pending"

    # -------------------------
    # Mapping helpers
    # -------------------------
    def _map_canonical_order_to_tradier(self, order: dict) -> dict:
        # Tradier expects form-encoded fields
        side = order["side"].lower()
        ord_type = order["type"].lower()

        data = {
            "class": "equity",
            "symbol": order["symbol"],
            "side": side,
            "quantity": order["qty"],
            "type": ord_type,
            "duration": self._map_tif(order.get("time_in_force", "day")),
        }

        if order.get("limit_price") is not None:
            data["price"] = order["limit_price"]
        if order.get("stop_price") is not None:
            data["stop"] = order["stop_price"]

        # Persist client_order_id in Tradier tag if supported
        if order.get("client_order_id"):
            data["tag"] = order["client_order_id"]

        return data

    def _map_tif(self, tif: str) -> str:
        tif = str(tif or "day").lower()
        mapping = {
            "day": "day",
            "gtc": "gtc",
            "ioc": "ioc",
            "fok": "fok",
            # Tradier doesn't map every broker TIF exactly; keep conservative defaults
        }
        return mapping.get(tif, "day")

    def _enforce_shadow_mode(self, order: dict):
        shadow_mode = bool(order.get("shadow_mode", False))
        if not shadow_mode and not self.allow_live:
            raise BrokerAdapterError(
                "permission_denied",
                "Live routing disabled. Adapter requires shadow_mode=true or TRADIER_ALLOW_LIVE=true.",
                context={"operation": "submit_order", "symbol": order.get("symbol")}
            )

    def _classify_http_error(self, status: int, payload: Any) -> str:
        if status in (401, 403):
            return "permission_denied"
        if status == 404:
            return "unknown"
        if status in (422, 400):
            return "invalid_order"
        if status == 429:
            return "rate_limit"
        if status >= 500:
            return "transient_network"
        return "unknown"

    def _extract_error_message(self, payload: Any, resp) -> str:
        if isinstance(payload, dict):
            if "errors" in payload:
                return str(payload["errors"])
            if "fault" in payload:
                return str(payload["fault"])
            if "message" in payload:
                return str(payload["message"])
            if "error" in payload:
                return str(payload["error"])
            return str(payload)
        return f"HTTP {resp.status_code}"

    def _to_num(self, v: Any):
        try:
            if v is None:
                return None
            return float(v)
        except Exception:
            return None
