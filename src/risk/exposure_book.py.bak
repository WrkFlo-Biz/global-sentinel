#!/usr/bin/env python3
"""Consolidated multi-account exposure tracking."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import os
from typing import Any, Dict, List, Optional

import yaml

from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter


def _load_guardrails_config() -> Dict[str, Any]:
    """Load live_trading_guardrails.yaml defaults for limit checks."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "live_trading_guardrails.yaml")
    try:
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


SYMBOL_SECTOR_OVERRIDES = {
    "USO": "energy",
    "XLE": "energy",
    "XOP": "energy",
    "OXY": "energy",
    "CVX": "energy",
    "XOM": "energy",
    "STNG": "shipping",
    "FRO": "shipping",
    "NAT": "shipping",
    "ZIM": "shipping",
    "LMT": "defense",
    "RTX": "defense",
    "NOC": "defense",
    "KTOS": "defense",
    "GLD": "gold",
    "GDX": "gold",
    "SLV": "gold",
    "JETS": "airlines",
    "UAL": "airlines",
    "DAL": "airlines",
    "AAL": "airlines",
    "EEM": "em",
    "INDA": "em",
    "EZU": "europe",
    "EWG": "europe",
    "LNG": "energy",
    "UVXY": "volatility",
    "SVXY": "volatility",
    "VXX": "volatility",
    "TLT": "rates",
    "TIP": "rates",
}

OIL_CORRELATED_SECTORS = {"energy", "shipping", "airlines", "europe", "em"}


class ExposureBook:
    """Build a consolidated exposure snapshot from broker adapters."""

    def __init__(self, accounts: Optional[Dict[str, Any]] = None):
        self.accounts = accounts or self._default_accounts_from_env()

    def snapshot(self) -> Dict[str, Any]:
        per_account: Dict[str, Any] = {}
        combined_positions: List[Dict[str, Any]] = []
        total_equity = 0.0
        total_gross = 0.0
        total_net = 0.0
        total_effective_gross = 0.0
        total_effective_net = 0.0
        total_pending_close_notional = 0.0
        total_pending_close_orders = 0

        for name, adapter in self.accounts.items():
            account_state = adapter.get_account_state() if hasattr(adapter, "get_account_state") else {}
            positions = adapter.list_positions() if hasattr(adapter, "list_positions") else []
            open_orders = adapter.list_open_orders() if hasattr(adapter, "list_open_orders") else []
            normalized_positions = self._apply_pending_close_adjustments(positions, open_orders)
            gross_exposure = sum(abs(_safe_float(p.get("market_value"), 0.0)) for p in positions)
            net_exposure = sum(
                _safe_float(p.get("market_value"), 0.0) if str(p.get("side", "long")) == "long"
                else -abs(_safe_float(p.get("market_value"), 0.0))
                for p in positions
            )
            effective_gross_exposure = sum(abs(_safe_float(p.get("_effective_market_value"), 0.0)) for p in normalized_positions)
            effective_net_exposure = sum(
                _safe_float(p.get("_effective_market_value"), 0.0) if str(p.get("side", "long")) == "long"
                else -abs(_safe_float(p.get("_effective_market_value"), 0.0))
                for p in normalized_positions
            )
            pending_close_notional = sum(_safe_float(p.get("_pending_close_market_value"), 0.0) for p in normalized_positions)
            pending_close_orders = sum(int(_safe_float(p.get("_pending_close_order_count"), 0.0)) for p in normalized_positions)
            equity = _safe_float(account_state.get("equity"), 0.0)
            per_account[name] = {
                "equity": equity,
                "cash": _safe_float(account_state.get("cash"), 0.0),
                "buying_power": _safe_float(account_state.get("buying_power"), 0.0),
                "positions": normalized_positions,
                "open_orders": open_orders,
                "gross_exposure": gross_exposure,
                "net_exposure": net_exposure,
                "effective_gross_exposure": effective_gross_exposure,
                "effective_net_exposure": effective_net_exposure,
                "pending_close_notional": pending_close_notional,
                "pending_close_orders": pending_close_orders,
                "margin_used": max(gross_exposure - _safe_float(account_state.get("cash"), 0.0), 0.0),
            }
            total_equity += equity
            total_gross += gross_exposure
            total_net += net_exposure
            total_effective_gross += effective_gross_exposure
            total_effective_net += effective_net_exposure
            total_pending_close_notional += pending_close_notional
            total_pending_close_orders += pending_close_orders
            for position in normalized_positions:
                tagged = dict(position)
                tagged["_account"] = name
                combined_positions.append(tagged)

        by_sector = self._aggregate_by_sector(combined_positions, total_equity)
        by_strategy = self._aggregate_by_strategy(combined_positions)
        by_direction = {
            "total_long": sum(max(_safe_float(p.get("market_value")), 0.0) for p in combined_positions if str(p.get("side", "long")) == "long"),
            "total_short": sum(abs(_safe_float(p.get("market_value"))) for p in combined_positions if str(p.get("side", "long")) == "short"),
        }
        by_direction["long_pct"] = (by_direction["total_long"] / total_equity) if total_equity else 0.0
        by_direction["short_pct"] = (by_direction["total_short"] / total_equity) if total_equity else 0.0

        snapshot = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "accounts": per_account,
            "combined": {
                "total_equity": total_equity,
                "total_gross_exposure": total_effective_gross,
                "total_net_exposure": total_effective_net,
                "gross_exposure_pct": (total_effective_gross / total_equity) if total_equity else 0.0,
                "net_exposure_pct": (total_effective_net / total_equity) if total_equity else 0.0,
                "raw_total_gross_exposure": total_gross,
                "raw_total_net_exposure": total_net,
                "raw_gross_exposure_pct": (total_gross / total_equity) if total_equity else 0.0,
                "raw_net_exposure_pct": (total_net / total_equity) if total_equity else 0.0,
                "pending_close_notional": total_pending_close_notional,
                "pending_close_orders": total_pending_close_orders,
            },
            "by_sector": by_sector,
            "by_strategy": by_strategy,
            "by_direction": by_direction,
            "concentration_risk": self._concentration_risk(combined_positions, by_sector, total_equity),
            "risk_metrics": self._risk_metrics(combined_positions, total_gross, total_equity),
            "alerts": [],
        }
        snapshot["alerts"] = self.check_limits({}, snapshot=snapshot)["violations"]
        return snapshot

    def check_limits(
        self,
        guardrails: Dict[str, Any],
        snapshot: Optional[Dict[str, Any]] = None,
        proposed_order: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snap = snapshot or self.snapshot()
        violations = []
        position_limits = guardrails.get("position_limits", {}) if guardrails else {}
        if not position_limits:
            cfg = _load_guardrails_config()
            position_limits = cfg.get("position_limits", {})
        max_gross = _safe_float(position_limits.get("max_gross_exposure_pct"), 0.40)
        max_sector = _safe_float(position_limits.get("max_sector_pct"), 0.50)
        gross_pct = _safe_float((snap.get("combined") or {}).get("gross_exposure_pct"), 0.0)
        if gross_pct > max_gross:
            violations.append(f"Gross exposure {gross_pct:.1%} exceeds limit {max_gross:.1%}")
        for sector, row in (snap.get("by_sector") or {}).items():
            sector_pct = _safe_float(row.get("pct_of_equity"), 0.0)
            if sector_pct > max_sector:
                violations.append(f"Sector {sector} at {sector_pct:.1%} exceeds limit {max_sector:.1%}")
        oil_corr = _safe_float((snap.get("concentration_risk") or {}).get("oil_correlation_pct"), 0.0)
        if oil_corr > 0.60:
            violations.append(f"Oil correlation {oil_corr:.1%} exceeds limit 60.0%")
        if proposed_order:
            symbol = str(proposed_order.get("symbol") or "")
            side = str(proposed_order.get("side") or proposed_order.get("direction") or "buy").lower()
            order_notional = _safe_float(proposed_order.get("notional"), 0.0)
            if order_notional <= 0:
                qty = _safe_float(proposed_order.get("qty"), 0.0)
                px = _safe_float(proposed_order.get("limit_price"), _safe_float(proposed_order.get("decision_price"), 0.0))
                order_notional = abs(qty * px)
            projected_gross = _safe_float((snap.get("combined") or {}).get("total_gross_exposure"), 0.0) + order_notional
            total_equity = _safe_float((snap.get("combined") or {}).get("total_equity"), 0.0)
            if total_equity and (projected_gross / total_equity) > max_gross:
                violations.append(f"Proposed order {symbol} would breach gross exposure limit")
            _ = side  # keep parsed side for future extensions
        return {"ok": not violations, "violations": violations}

    def pnl_realtime(self) -> Dict[str, Any]:
        snap = self.snapshot()
        by_account = {}
        for name, row in (snap.get("accounts") or {}).items():
            positions = row.get("positions", []) or []
            by_account[name] = sum(_safe_float(p.get("unrealized_pl"), 0.0) for p in positions)
        return {
            "timestamp_utc": snap["timestamp_utc"],
            "by_account": by_account,
            "combined": sum(by_account.values()),
        }

    def format_telegram(self) -> str:
        snap = self.snapshot()
        pnl = self.pnl_realtime()
        risk = snap.get("risk_metrics", {})
        combined = snap.get("combined", {})
        return (
            f"💰 Equity: ${combined.get('total_equity', 0):,.0f} | "
            f"Gross: {combined.get('gross_exposure_pct', 0):.0%} | "
            f"Net: {combined.get('net_exposure_pct', 0):+.0%} | "
            f"Oil Δ: ${risk.get('oil_delta', 0):+,.0f}/pt | "
            f"P&L: ${pnl.get('combined', 0):+,.0f}"
        )

    def _aggregate_by_sector(self, positions: List[Dict[str, Any]], total_equity: float) -> Dict[str, Dict[str, float]]:
        grouped: Dict[str, Dict[str, float]] = defaultdict(lambda: {"long": 0.0, "short": 0.0, "net": 0.0, "pct_of_equity": 0.0})
        for pos in positions:
            sector = self._sector_for_position(pos)
            mv = abs(_safe_float(pos.get("_effective_market_value"), _safe_float(pos.get("market_value"), 0.0)))
            side = str(pos.get("side", "long"))
            if side == "short":
                grouped[sector]["short"] += mv
                grouped[sector]["net"] -= mv
            else:
                grouped[sector]["long"] += mv
                grouped[sector]["net"] += mv
        for sector, row in grouped.items():
            row["pct_of_equity"] = ((row["long"] + row["short"]) / total_equity) if total_equity else 0.0
        return dict(grouped)

    def _aggregate_by_strategy(self, positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"positions": [], "pnl": 0.0, "exposure": 0.0})
        for pos in positions:
            strategy = str(pos.get("strategy") or "unassigned")
            grouped[strategy]["positions"].append(pos.get("symbol"))
            grouped[strategy]["pnl"] += _safe_float(pos.get("unrealized_pl"), 0.0)
            grouped[strategy]["exposure"] += abs(_safe_float(pos.get("_effective_market_value"), _safe_float(pos.get("market_value"), 0.0)))
        return dict(grouped)

    def _concentration_risk(self, positions: List[Dict[str, Any]], by_sector: Dict[str, Dict[str, float]], total_equity: float) -> Dict[str, float]:
        top_positions = sorted((abs(_safe_float(p.get("_effective_market_value"), _safe_float(p.get("market_value"), 0.0))) for p in positions), reverse=True)
        oil_related = sum(
            abs(_safe_float(p.get("_effective_market_value"), _safe_float(p.get("market_value"), 0.0)))
            for p in positions
            if self._sector_for_position(p) in OIL_CORRELATED_SECTORS
        )
        sector_values = [row["long"] + row["short"] for row in by_sector.values()]
        return {
            "largest_position_pct": (top_positions[0] / total_equity) if top_positions and total_equity else 0.0,
            "largest_sector_pct": (max(sector_values) / total_equity) if sector_values and total_equity else 0.0,
            "top_5_positions_pct": (sum(top_positions[:5]) / total_equity) if top_positions and total_equity else 0.0,
            "oil_correlation_pct": (oil_related / total_equity) if total_equity else 0.0,
        }

    def _risk_metrics(self, positions: List[Dict[str, Any]], total_gross: float, total_equity: float) -> Dict[str, float]:
        oil_delta = 0.0
        vix_delta = 0.0
        ceasefire_loss = 0.0
        for pos in positions:
            sector = self._sector_for_position(pos)
            mv = _safe_float(pos.get("_effective_market_value"), _safe_float(pos.get("market_value"), 0.0))
            if sector in {"energy", "shipping"}:
                oil_delta += mv * 0.015
                ceasefire_loss += abs(mv) * 0.08
            elif sector == "airlines":
                oil_delta -= mv * 0.01
                ceasefire_loss += abs(mv) * 0.05
            if sector == "volatility":
                vix_delta += mv * 0.06
                ceasefire_loss += abs(mv) * 0.20
        return {
            "oil_delta": oil_delta,
            "vix_delta": vix_delta,
            "daily_var_95": total_gross * 0.02 * 1.65,
            "max_loss_if_ceasefire": min(ceasefire_loss, total_equity) if total_equity else ceasefire_loss,
        }

    def _sector_for_position(self, position: Dict[str, Any]) -> str:
        symbol = str(position.get("symbol") or "").upper()
        return str(position.get("sector") or SYMBOL_SECTOR_OVERRIDES.get(symbol) or "other")

    def _default_accounts_from_env(self) -> Dict[str, Any]:
        accounts: Dict[str, Any] = {}
        seen: set[tuple[str, str, str]] = set()
        base_url = os.getenv("ALPACA_PAPER_BASE_URL") or os.getenv("ALPACA_BASE_URL")
        if base_url:
            base_url = base_url.rstrip("/")
            if base_url.endswith("/v2"):
                base_url = base_url[:-3]
        specs = {
            "day_trade": (
                os.getenv("ALPACA_API_KEY_DAYTRADE") or os.getenv("ALPACA_DAY_TRADE_KEY") or os.getenv("APCA_API_KEY_ID"),
                os.getenv("ALPACA_SECRET_KEY_DAYTRADE") or os.getenv("ALPACA_DAY_TRADE_SECRET") or os.getenv("APCA_API_SECRET_KEY"),
            ),
            "medium_long": (
                os.getenv("ALPACA_API_KEY_MEDIUM_LONG") or os.getenv("ALPACA_API_KEY_MEDIUMLONG") or os.getenv("ALPACA_API_KEY_MEDLONG") or os.getenv("ALPACA_MEDIUM_LONG_KEY") or os.getenv("APCA_API_KEY_ID"),
                os.getenv("ALPACA_SECRET_KEY_MEDIUM_LONG") or os.getenv("ALPACA_SECRET_KEY_MEDIUMLONG") or os.getenv("ALPACA_SECRET_KEY_MEDLONG") or os.getenv("ALPACA_MEDIUM_LONG_SECRET") or os.getenv("APCA_API_SECRET_KEY"),
            ),
        }
        for name, (key, secret) in specs.items():
            if not key or not secret:
                continue
            dedupe_key = (key, secret, base_url or "")
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            accounts[name] = AlpacaPaperAdapter(api_key=key, api_secret=secret, base_url=base_url)
        if not accounts:
            try:
                accounts["default"] = AlpacaPaperAdapter(base_url=base_url)
            except Exception:
                return {}
        return accounts

    def _apply_pending_close_adjustments(
        self,
        positions: List[Dict[str, Any]],
        open_orders: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        close_order_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for order in open_orders or []:
            symbol = str(order.get("symbol") or "").upper()
            if symbol:
                close_order_map[symbol].append(order)

        adjusted: List[Dict[str, Any]] = []
        for position in positions or []:
            pos = dict(position)
            symbol = str(pos.get("symbol") or "").upper()
            qty = abs(_safe_float(pos.get("qty"), 0.0))
            market_value = abs(_safe_float(pos.get("market_value"), 0.0))
            side = str(pos.get("side", "long")).lower()

            closable_qty = 0.0
            pending_order_count = 0
            for order in close_order_map.get(symbol, []):
                order_side = str(order.get("side") or "").lower()
                if side == "long" and order_side != "sell":
                    continue
                if side == "short" and order_side != "buy":
                    continue
                remaining_qty = abs(_safe_float(order.get("remaining_qty"), _safe_float(order.get("qty"), 0.0)))
                if remaining_qty <= 0:
                    continue
                pending_order_count += 1
                closable_qty += remaining_qty

            closable_qty = min(closable_qty, qty)
            close_ratio = (closable_qty / qty) if qty > 0 else 0.0
            pending_close_market_value = market_value * close_ratio
            effective_market_value = market_value - pending_close_market_value

            pos["_pending_close_qty"] = closable_qty
            pos["_pending_close_order_count"] = pending_order_count
            pos["_pending_close_market_value"] = pending_close_market_value
            pos["_effective_market_value"] = effective_market_value
            pos["_pending_close"] = closable_qty > 0
            adjusted.append(pos)
        return adjusted
