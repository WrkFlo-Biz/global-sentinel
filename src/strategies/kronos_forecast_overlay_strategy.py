"""Kronos-style forecast overlay from extracted Instagram ML content.

Visible reel/feed text recovered from the front Instagram window included:
  - ``gigaqian``: "Kronos is a time series foundation model specifically
    designed to forecast price action in financial markets."

This strategy does not attempt to train a foundation model inside GS. Instead,
it consumes model outputs already attached to ``market_data`` and turns them
into tradeable ideas with the same candidate schema used elsewhere in GS.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

KRONOS_WATCHLIST = ["SPY", "QQQ", "IWM", "GLD", "USO", "NVDA", "TSLA", "AAPL", "META"]

PARAMS: dict[str, Any] = {
    "min_forecast_edge_pct": 0.30,
    "min_model_confidence": 0.58,
    "min_relative_volume": 1.10,
    "base_notional_usd": 750.0,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _pct_change(sym_data: dict[str, Any]) -> float:
    if "change_pct" in sym_data:
        return _safe_float(sym_data["change_pct"])
    price = _safe_float(sym_data.get("price"))
    prior_close = _safe_float(sym_data.get("prior_close"))
    if price > 0 and prior_close > 0:
        return ((price - prior_close) / prior_close) * 100.0
    return 0.0


def _relative_volume(sym_data: dict[str, Any]) -> float:
    if "relative_volume" in sym_data:
        return _safe_float(sym_data["relative_volume"], 1.0)
    volume = _safe_float(sym_data.get("volume"))
    avg_volume = _safe_float(sym_data.get("avg_volume"))
    if volume > 0 and avg_volume > 0:
        return volume / avg_volume
    return 1.0


class KronosForecastOverlayStrategy:
    """Translate Kronos-like forecasts into GS trade candidates."""

    def __init__(self, params: dict[str, Any] | None = None):
        self._params = {**PARAMS, **(params or {})}

    def evaluate(
        self,
        symbol: str,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if market_data is None or symbol not in KRONOS_WATCHLIST:
            return None

        sym_data = market_data.get(symbol, {})
        price = _safe_float(sym_data.get("price"))
        if price <= 0:
            return None

        forecast_edge = _safe_float(
            sym_data.get("kronos_forecast_return_1h")
            or sym_data.get("kronos_forecast_return")
            or sym_data.get("forecast_return_pct")
        )
        model_confidence = _safe_float(
            sym_data.get("kronos_confidence")
            or sym_data.get("forecast_confidence")
            or sym_data.get("kronos_rankic")
        )
        if abs(forecast_edge) < self._params["min_forecast_edge_pct"]:
            return None
        if model_confidence < self._params["min_model_confidence"]:
            return None

        rel_vol = _relative_volume(sym_data)
        if rel_vol < self._params["min_relative_volume"]:
            return None

        change_pct = _pct_change(sym_data)
        vwap = _safe_float(sym_data.get("vwap"), default=price)
        vol_forecast = _safe_float(sym_data.get("kronos_volatility_forecast") or sym_data.get("forecast_volatility_pct"), default=abs(change_pct) * 0.75)
        vol_forecast = max(vol_forecast, 0.35)

        direction = ""
        alignment_bonus = 0.0
        if forecast_edge > 0 and price >= vwap:
            direction = "long"
            alignment_bonus = 0.05 if change_pct >= 0 else 0.02
        elif forecast_edge < 0 and price <= vwap:
            direction = "short"
            alignment_bonus = 0.05 if change_pct <= 0 else 0.02
        else:
            return None

        if scorecard:
            components = scorecard.get("component_scores", {})
            market_vol = _safe_float(components.get("market_volatility"))
            if market_vol > 0.55:
                alignment_bonus += 0.03

        confidence = 0.46
        confidence += min(abs(forecast_edge), 1.2) * 0.20
        confidence += min(model_confidence, 0.95) * 0.24
        confidence += min(rel_vol, 2.0) * 0.06
        confidence += alignment_bonus
        confidence = min(confidence, 0.92)

        stop_loss_pct = -max(0.9, vol_forecast * 0.80)
        take_profit_pct = min(4.4, abs(stop_loss_pct) * 1.85)
        notional = self._params["base_notional_usd"] * (0.75 + confidence * 0.40)

        return {
            "strategy": "kronos_forecast_overlay",
            "symbol": symbol,
            "direction": direction,
            "notional_usd": round(notional, 2),
            "confidence_score": round(confidence, 3),
            "confidence": round(confidence, 3),
            "stop_loss_pct": round(stop_loss_pct, 2),
            "take_profit_pct": round(take_profit_pct, 2),
            "tier": "tier_2",
            "tier_size_multiplier": round(min(0.82, 0.42 + confidence * 0.33), 2),
            "account": "day_trade",
            "entry_signal": f"Kronos forecast edge {forecast_edge:.2f}% on {symbol}",
            "metadata": {
                "source": "instagram_gigaqian_kronos",
                "forecast_edge_pct": round(forecast_edge, 3),
                "model_confidence": round(model_confidence, 3),
                "relative_volume": round(rel_vol, 3),
                "forecast_volatility_pct": round(vol_forecast, 3),
            },
        }

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if market_data is None:
            return []

        ideas = []
        for symbol in KRONOS_WATCHLIST:
            idea = self.evaluate(symbol=symbol, market_data=market_data, scorecard=scorecard)
            if idea:
                ideas.append(idea)
        ideas.sort(key=lambda item: item["confidence_score"], reverse=True)
        return ideas


def evaluate_kronos_forecast_overlay(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    strategy = KronosForecastOverlayStrategy(params=dict((strat or {}).get("params", {})))
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)
