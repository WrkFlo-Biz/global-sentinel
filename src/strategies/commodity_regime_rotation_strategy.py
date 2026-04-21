"""Cross-commodity rotation strategy from extracted Instagram commodity content.

Visible reel/feed text recovered from the front Instagram window included:
  - ``neelsalami``: "Former Citadel quant explains how to think about all of
    the commodities: energy, agriculture, and metals."

This module turns that concept into a GS-native signal generator:
  - identify the leading commodity sector
  - prefer backwardated / positive-carry sectors
  - fade laggards in contango when the spread between sectors is wide enough
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

COMMODITY_SECTOR_WATCHLIST: dict[str, list[str]] = {
    "energy": ["USO", "XLE", "XOP", "XOM", "CVX", "UNG", "UGA"],
    "metals": ["MGC", "GC", "GLD", "SLV", "GDX"],
    "agriculture": ["DBA", "WEAT", "CORN", "SOYB", "MOO", "CANE", "JO", "COW"],
}

PARAMS: dict[str, Any] = {
    "min_sector_score": 0.28,
    "min_sector_spread": 0.22,
    "max_candidates": 3,
    "base_notional_usd": 900.0,
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


def _carry_score(sym_data: dict[str, Any]) -> float:
    state = str(sym_data.get("curve_state") or "").strip().lower()
    roll_yield = _safe_float(sym_data.get("roll_yield_pct"))
    front_spread = _safe_float(sym_data.get("front_spread_pct"))
    curve_value = roll_yield if roll_yield != 0 else front_spread

    if state in {"backwardation", "backwardated"}:
        return min(0.35, 0.15 + abs(curve_value) * 0.2)
    if state in {"contango", "contangoed"}:
        return -min(0.35, 0.15 + abs(curve_value) * 0.2)
    if curve_value > 0:
        return min(0.25, 0.08 + curve_value * 0.18)
    if curve_value < 0:
        return -min(0.25, 0.08 + abs(curve_value) * 0.18)
    return 0.0


class CommodityRegimeRotationStrategy:
    """Rank commodity sectors by momentum, carry and participation."""

    def __init__(self, params: dict[str, Any] | None = None):
        self._params = {**PARAMS, **(params or {})}

    def _sector_metrics(
        self,
        sector: str,
        market_data: dict[str, dict[str, Any]],
        scorecard: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        metrics = []
        for symbol in COMMODITY_SECTOR_WATCHLIST[sector]:
            sym_data = market_data.get(symbol)
            if not sym_data:
                continue
            metrics.append(
                {
                    "symbol": symbol,
                    "momentum": _pct_change(sym_data) / 100.0,
                    "carry": _carry_score(sym_data),
                    "rvol": _relative_volume(sym_data),
                }
            )
        if not metrics:
            return None

        sector_score = sum(m["momentum"] * 0.55 + m["carry"] * 0.30 + min(m["rvol"], 2.0) * 0.075 for m in metrics) / len(metrics)

        if scorecard:
            oil_regime = str(scorecard.get("v6_oil_regime") or "").upper()
            components = scorecard.get("component_scores", {})
            geo = _safe_float(components.get("geopolitical_tension"))
            commodity_shock = _safe_float(components.get("commodity_shock"))
            if sector == "energy" and oil_regime in {"SHOCK", "DISLOCATION"}:
                sector_score += 0.12
            if sector == "metals" and geo > 0.25:
                sector_score += 0.08
            if sector == "agriculture" and commodity_shock > 0.45:
                sector_score += 0.06

        leader = max(metrics, key=lambda item: item["momentum"] + item["carry"] + item["rvol"] * 0.05)
        laggard = min(metrics, key=lambda item: item["momentum"] + item["carry"])
        return {
            "sector": sector,
            "score": round(sector_score, 4),
            "leader": leader,
            "laggard": laggard,
        }

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if market_data is None:
            return []

        sector_stats = []
        for sector in COMMODITY_SECTOR_WATCHLIST:
            stat = self._sector_metrics(sector, market_data, scorecard)
            if stat is not None:
                sector_stats.append(stat)

        if len(sector_stats) < 2:
            return []

        sector_stats.sort(key=lambda item: item["score"], reverse=True)
        leader = sector_stats[0]
        laggard = sector_stats[-1]
        spread = leader["score"] - laggard["score"]

        ideas: list[dict[str, Any]] = []
        if leader["score"] >= self._params["min_sector_score"]:
            leader_symbol = leader["leader"]["symbol"]
            confidence = min(0.9, 0.46 + leader["score"] * 0.65 + max(spread, 0.0) * 0.25)
            ideas.append(
                {
                    "strategy": "commodity_regime_rotation",
                    "symbol": leader_symbol,
                    "direction": "long",
                    "notional_usd": round(self._params["base_notional_usd"] * (0.8 + confidence * 0.45), 2),
                    "confidence_score": round(confidence, 3),
                    "confidence": round(confidence, 3),
                    "stop_loss_pct": -2.4,
                    "take_profit_pct": 4.6,
                    "tier": "tier_2",
                    "tier_size_multiplier": round(min(0.8, 0.40 + confidence * 0.35), 2),
                    "account": "medium_long",
                    "entry_signal": f"{leader['sector']} leads commodity complex — momentum + carry alignment",
                    "metadata": {
                        "source": "instagram_neelsalami_commodities",
                        "sector": leader["sector"],
                        "sector_score": leader["score"],
                        "sector_spread": round(spread, 4),
                        "carry_bias": round(leader["leader"]["carry"], 4),
                        "relative_volume": round(leader["leader"]["rvol"], 3),
                    },
                }
            )

        if spread >= self._params["min_sector_spread"] and laggard["score"] <= -0.05:
            laggard_symbol = laggard["laggard"]["symbol"]
            confidence = min(0.88, 0.44 + abs(laggard["score"]) * 0.55 + spread * 0.25)
            ideas.append(
                {
                    "strategy": "commodity_regime_rotation",
                    "symbol": laggard_symbol,
                    "direction": "short",
                    "notional_usd": round(self._params["base_notional_usd"] * (0.7 + confidence * 0.40), 2),
                    "confidence_score": round(confidence, 3),
                    "confidence": round(confidence, 3),
                    "stop_loss_pct": -2.2,
                    "take_profit_pct": 4.2,
                    "tier": "tier_2",
                    "tier_size_multiplier": round(min(0.75, 0.38 + confidence * 0.32), 2),
                    "account": "medium_long",
                    "entry_signal": f"{laggard['sector']} lags commodity complex — negative momentum / carry",
                    "metadata": {
                        "source": "instagram_neelsalami_commodities",
                        "sector": laggard["sector"],
                        "sector_score": laggard["score"],
                        "sector_spread": round(spread, 4),
                        "carry_bias": round(laggard["laggard"]["carry"], 4),
                        "relative_volume": round(laggard["laggard"]["rvol"], 3),
                    },
                }
            )

        ideas.sort(key=lambda item: item["confidence_score"], reverse=True)
        return ideas[: int(self._params["max_candidates"])]


def evaluate_commodity_regime_rotation(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    strategy = CommodityRegimeRotationStrategy(params=dict((strat or {}).get("params", {})))
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)
