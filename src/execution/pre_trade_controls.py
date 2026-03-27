#!/usr/bin/env python3
"""Pre-trade controls aligned with basic algorithmic trading guardrails."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


@dataclass
class PreTradeResult:
    passed: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PreTradeControls:
    """Evaluate pre-trade exposure, price, and market-data constraints."""

    def __init__(
        self,
        max_single_order_notional_pct: float = 0.12,
        max_portfolio_notional_pct: float = 0.50,
        max_single_name_pct: float = 0.15,
        max_sector_pct: float = 0.30,
        price_collar_pct: float = 0.05,
        max_orders_per_minute: int = 10,
        min_quote_freshness_seconds: float = 30.0,
        max_spread_pct: float = 0.02,
    ):
        self.max_single_order_notional_pct = max_single_order_notional_pct
        self.max_portfolio_notional_pct = max_portfolio_notional_pct
        self.max_single_name_pct = max_single_name_pct
        self.max_sector_pct = max_sector_pct
        self.price_collar_pct = price_collar_pct
        self.max_orders_per_minute = max_orders_per_minute
        self.min_quote_freshness_seconds = min_quote_freshness_seconds
        self.max_spread_pct = max_spread_pct

    @classmethod
    def from_yaml_file(cls, path: Path) -> "PreTradeControls":
        try:
            import yaml

            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        return cls(**{k: v for k, v in raw.items() if k in cls.__init__.__code__.co_varnames})

    @classmethod
    def from_guardrails(cls, config_dir: Path) -> "PreTradeControls":
        """Load from live_trading_guardrails.yaml with tightened limits."""
        guardrails_path = config_dir / "live_trading_guardrails.yaml"
        try:
            import yaml
            raw = yaml.safe_load(guardrails_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        pos = raw.get("position_limits", {})
        order = raw.get("order_controls", {})
        return cls(
            max_single_order_notional_pct=float(pos.get("max_position_pct", 0.02)),
            max_portfolio_notional_pct=float(pos.get("max_gross_exposure_pct", 0.10)),
            max_single_name_pct=float(pos.get("max_single_name_pct", 0.05)),
            max_sector_pct=float(pos.get("max_sector_pct", 0.15)),
            price_collar_pct=float(order.get("price_collar_pct", 0.03)),
            max_orders_per_minute=int(order.get("max_orders_per_minute", 5)),
            min_quote_freshness_seconds=float(order.get("quote_max_age_seconds", 30)),
        )

    def _infer_notional(self, trade_idea: Dict[str, Any], market_data: Dict[str, Any]) -> float:
        if trade_idea.get("notional") is not None:
            return _safe_float(trade_idea.get("notional"))
        qty = _safe_float(trade_idea.get("qty"), 0.0)
        ref_price = _safe_float(trade_idea.get("limit_price"), 0.0) or _safe_float(market_data.get("last_price"), 0.0)
        return qty * ref_price

    def check(self, trade_idea: Dict[str, Any], portfolio_state: Dict[str, Any], market_data: Optional[Dict[str, Any]] = None) -> PreTradeResult:
        market_data = market_data or {}
        checks: List[Dict[str, Any]] = []
        equity = max(_safe_float(portfolio_state.get("equity"), 100000.0), 1.0)
        notional = self._infer_notional(trade_idea, market_data)
        symbol = str(trade_idea.get("symbol", ""))
        sector = str(trade_idea.get("sector", market_data.get("sector", "")))

        max_notional = equity * self.max_single_order_notional_pct
        checks.append({"name": "max_single_order_notional", "passed": notional <= max_notional, "value": notional, "threshold": max_notional})

        current_exposure = _safe_float(portfolio_state.get("gross_exposure"), 0.0)
        max_exposure = equity * self.max_portfolio_notional_pct
        checks.append(
            {
                "name": "max_portfolio_exposure",
                "passed": current_exposure + notional <= max_exposure,
                "value": current_exposure + notional,
                "threshold": max_exposure,
            }
        )

        positions = portfolio_state.get("positions", {}) or {}
        existing_position = _safe_float((positions.get(symbol) or {}).get("market_value"), 0.0)
        max_single = equity * self.max_single_name_pct
        checks.append(
            {
                "name": "max_single_name_concentration",
                "passed": existing_position + notional <= max_single,
                "value": existing_position + notional,
                "threshold": max_single,
            }
        )

        if sector:
            sector_exposure = sum(
                _safe_float((position or {}).get("market_value"), 0.0)
                for position in positions.values()
                if str((position or {}).get("sector", "")) == sector
            )
            max_sector = equity * self.max_sector_pct
            checks.append(
                {
                    "name": "max_sector_concentration",
                    "passed": sector_exposure + notional <= max_sector,
                    "value": sector_exposure + notional,
                    "threshold": max_sector,
                }
            )

        limit_price = trade_idea.get("limit_price")
        last_price = market_data.get("last_price")
        if limit_price is not None and last_price not in (None, 0):
            collar = abs(_safe_float(limit_price) - _safe_float(last_price)) / max(_safe_float(last_price), 0.01)
            checks.append({"name": "price_collar", "passed": collar <= self.price_collar_pct, "value": collar, "threshold": self.price_collar_pct})

        bid = market_data.get("bid")
        ask = market_data.get("ask")
        if bid is not None and ask is not None:
            sane = _safe_float(bid) > 0 and _safe_float(ask) > 0 and _safe_float(bid) <= _safe_float(ask)
            checks.append({"name": "market_data_sanity", "passed": sane, "value": {"bid": bid, "ask": ask}, "threshold": "bid > 0 and bid <= ask"})
            if sane and _safe_float(bid) > 0:
                spread = (_safe_float(ask) - _safe_float(bid)) / _safe_float(bid)
                checks.append({"name": "max_spread", "passed": spread <= self.max_spread_pct, "value": spread, "threshold": self.max_spread_pct})

        quote_age = _safe_float(market_data.get("quote_age_seconds"), 0.0)
        if market_data.get("quote_age_seconds") is not None:
            checks.append(
                {
                    "name": "quote_freshness",
                    "passed": quote_age <= self.min_quote_freshness_seconds,
                    "value": quote_age,
                    "threshold": self.min_quote_freshness_seconds,
                }
            )

        recent_orders = int(portfolio_state.get("orders_last_minute", 0))
        checks.append({"name": "max_orders_per_minute", "passed": recent_orders < self.max_orders_per_minute, "value": recent_orders, "threshold": self.max_orders_per_minute})

        return PreTradeResult(passed=all(check["passed"] for check in checks), checks=checks)
