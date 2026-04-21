#!/usr/bin/env python3
"""Canonical normalization for research-only options chain records.

The normalizer accepts heterogeneous option chain payloads from brokers,
market-data vendors, or cached artifacts and converts them into a stable,
typed structure used by downstream research-only filters and feasibility
checks. The output explicitly carries a ``not_for_direct_execution`` flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _normalize_option_type(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in {"c", "call", "calls"}:
        return "call"
    if normalized in {"p", "put", "puts"}:
        return "put"
    return None


def _normalize_iv(value: Any) -> float:
    iv = _safe_float(value)
    if iv is None:
        return 0.0
    # Vendors sometimes provide IV in percent points (e.g. 25.0 for 25%).
    if iv > 3.0:
        return round(iv / 100.0, 6)
    return round(max(iv, 0.0), 6)


def _get_first(payload: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = payload
        found = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current.get(part)
        if found and current is not None:
            return current
    return None


@dataclass(frozen=True)
class CanonicalOptionContract:
    """Typed canonical representation of an option contract."""

    bid: float
    ask: float
    IV: float
    delta: float
    gamma: float
    theta: float
    vega: float
    OI: int
    volume: int
    expiry: str
    strike: float
    contract_type: str
    contract_symbol: Optional[str] = None
    underlying_symbol: Optional[str] = None
    not_for_direct_execution: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bid": self.bid,
            "ask": self.ask,
            "IV": self.IV,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "OI": self.OI,
            "volume": self.volume,
            "expiry": self.expiry,
            "strike": self.strike,
            "contract_type": self.contract_type,
            "contract_symbol": self.contract_symbol,
            "underlying_symbol": self.underlying_symbol,
            "not_for_direct_execution": self.not_for_direct_execution,
        }


class OptionsChainNormalizer:
    """Convert raw option chain payloads into canonical research records."""

    def normalize(self, raw_chain: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize a raw chain into canonical contract dictionaries.

        Contracts missing any of ``expiry``, ``strike``, or ``contract_type``
        are skipped because downstream spread and margin checks require them.
        """

        normalized: List[Dict[str, Any]] = []
        for raw_contract in raw_chain:
            contract = self._normalize_contract(raw_contract)
            if contract is not None:
                normalized.append(contract.to_dict())
        return normalized

    def _normalize_contract(self, raw_contract: Dict[str, Any]) -> Optional[CanonicalOptionContract]:
        bid = _safe_float(_get_first(raw_contract, "bid", "bid_price", "quote.bid", "latest_quote.bid")) or 0.0
        ask = _safe_float(_get_first(raw_contract, "ask", "ask_price", "quote.ask", "latest_quote.ask")) or 0.0
        expiry = str(
            _get_first(raw_contract, "expiry", "expiration_date", "expiration", "details.expiry", "details.expiration_date")
            or ""
        ).strip()
        strike = _safe_float(_get_first(raw_contract, "strike", "strike_price", "details.strike"))
        contract_type = _normalize_option_type(
            _get_first(raw_contract, "contract_type", "option_type", "type", "right", "details.contract_type")
        )

        if not expiry or strike is None or contract_type is None:
            return None

        greeks = raw_contract.get("greeks") if isinstance(raw_contract.get("greeks"), dict) else {}
        return CanonicalOptionContract(
            bid=round(max(bid, 0.0), 6),
            ask=round(max(ask, 0.0), 6),
            IV=_normalize_iv(_get_first(raw_contract, "IV", "iv", "implied_volatility", "greeks.iv")),
            delta=round(_safe_float(_get_first(raw_contract, "delta", "greeks.delta")) or 0.0, 6),
            gamma=round(_safe_float(_get_first(raw_contract, "gamma", "greeks.gamma")) or 0.0, 6),
            theta=round(_safe_float(_get_first(raw_contract, "theta", "greeks.theta")) or 0.0, 6),
            vega=round(_safe_float(_get_first(raw_contract, "vega", "greeks.vega")) or 0.0, 6),
            OI=_safe_int(_get_first(raw_contract, "OI", "open_interest", "metrics.open_interest")),
            volume=_safe_int(_get_first(raw_contract, "volume", "daily_volume", "trade_volume", "metrics.volume")),
            expiry=expiry,
            strike=round(strike, 6),
            contract_type=contract_type,
            contract_symbol=str(_get_first(raw_contract, "contract_symbol", "symbol", "id", "option_symbol") or "") or None,
            underlying_symbol=str(_get_first(raw_contract, "underlying_symbol", "underlying", "root_symbol") or "") or None,
        )
